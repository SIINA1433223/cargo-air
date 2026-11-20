import { describe, expect, it } from 'vitest'
import {
  addAttachments,
  attachmentKind,
  canPreviewInline,
  fileNameOf,
  formatFileSize,
  removeAttachment,
  shortFileName,
  toAttachmentList,
  type AttachmentItem,
} from '@/utils/attachment'

/**
 * 附件解析是「上传/下载/预览」的地基，用**真实飞书取值形状**钉死：
 * 名字是长中文名、size 单位是字节、mime 是 xlsx 的完整长串、原始对象还带 url/tmp_url。
 */

/** 真实记录（发票号 4100124128）的附件取值，含飞书原样返回的 url/tmp_url */
const REAL_RAW = [
  {
    file_token: 'KbA3bh6WEoqVIjx3Nx4cnClmnUb',
    name: '4100121884；4100122848；4100123726；4100124128；4100124949；4100125502；4100126047；4100126342；4100128289；4100128289；4100123054.xlsx',
    size: 27282,
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    url: 'https://open.feishu.cn/open-apis/drive/v1/medias/KbA3bh6WEoqVIjx3Nx4cnClmnUb/download?extra=SECRET',
    tmp_url: 'https://open.feishu.cn/open-apis/drive/v1/medias/batch_get_tmp_download_url?SECRET',
  },
]

describe('toAttachmentList 解析', () => {
  it('解析真实取值，且绝不把 url / tmp_url 带出来（§10.3）', () => {
    const list = toAttachmentList(REAL_RAW)

    expect(list).toHaveLength(1)
    expect(list[0].file_token).toBe('KbA3bh6WEoqVIjx3Nx4cnClmnUb')
    expect(list[0].size).toBe(27282)
    // 关键：白名单取值，含临时凭证的地址不能漏到界面
    expect(Object.keys(list[0]).sort()).toEqual(['file_token', 'name', 'size', 'type'])
    expect(JSON.stringify(list)).not.toContain('SECRET')
  })

  it('空值一律得到空数组', () => {
    expect(toAttachmentList(null)).toEqual([])
    expect(toAttachmentList(undefined)).toEqual([])
    expect(toAttachmentList('')).toEqual([])
    expect(toAttachmentList([])).toEqual([])
  })

  it('单个裸对象也认（飞书单值字段返回裸 dict 是踩过的坑）', () => {
    const list = toAttachmentList({ file_token: 'ft1', name: 'a.pdf' })

    expect(list).toHaveLength(1)
    expect(list[0].file_token).toBe('ft1')
  })

  it('只有 file_token 的字符串也认', () => {
    expect(toAttachmentList('ft1')).toEqual([{ file_token: 'ft1' }])
  })

  it('丢弃没有 file_token 的项（留着也是点了没反应的空条目）', () => {
    const list = toAttachmentList([
      { name: '没有 token.pdf' },
      { file_token: '' },
      null,
      42,
      { file_token: 'ft-ok', name: 'ok.pdf' },
    ])

    expect(list).toEqual([{ file_token: 'ft-ok', name: 'ok.pdf' }])
  })

  it('按 file_token 去重', () => {
    expect(toAttachmentList([{ file_token: 'ft1' }, { file_token: 'ft1', name: '重复' }])).toHaveLength(1)
  })
})

describe('attachmentKind 预览方式（§10.1）', () => {
  it('PDF 走 iframe', () => {
    expect(attachmentKind({ file_token: 't', type: 'application/pdf' })).toBe('pdf')
    // mime 缺失或为 octet-stream 时看扩展名
    expect(attachmentKind({ file_token: 't', name: '订舱单.PDF' })).toBe('pdf')
  })

  it('图片走 Image 组件', () => {
    expect(attachmentKind({ file_token: 't', type: 'image/png' })).toBe('image')
    expect(attachmentKind({ file_token: 't', name: '货照.jpeg' })).toBe('image')
    expect(attachmentKind({ file_token: 't', name: '货照.HEIC' })).toBe('image')
  })

  it('表格走自渲染网格', () => {
    // 真实附件里的那条就是 xlsx（mime 是那一长串），不能只看扩展名
    expect(attachmentKind(REAL_RAW[0] as AttachmentItem)).toBe('excel')
    expect(attachmentKind({ file_token: 't', name: '报价单.XLS' })).toBe('excel')
    expect(attachmentKind({ file_token: 't', name: '导出.csv' })).toBe('excel')
  })

  it('docx / zip 只能下载查看', () => {
    expect(canPreviewInline({ file_token: 't', name: '提货单.docx' })).toBe(false)
    expect(canPreviewInline({ file_token: 't', name: '资料.zip' })).toBe(false)
  })

  it('没有扩展名、没有 mime 时按不支持预览处理', () => {
    expect(attachmentKind({ file_token: 't' })).toBe('other')
    expect(attachmentKind({ file_token: 't', name: 'README' })).toBe('other')
  })
})

describe('fileNameOf / shortFileName', () => {
  it('无名字用占位，不回退到 file_token', () => {
    expect(fileNameOf({ file_token: 'ft1' })).toBe('未命名附件')
  })

  it('短名原样返回', () => {
    expect(shortFileName('4100125907订舱单.xlsx')).toBe('4100125907订舱单.xlsx')
  })

  it('超长中文名截断后仍保留扩展名，且不超过 max', () => {
    const name = REAL_RAW[0].name!
    const short = shortFileName(name)

    expect(short.length).toBeLessThanOrEqual(30)
    expect(short.endsWith('.xlsx')).toBe(true)
    expect(short.startsWith('4100121884')).toBe(true)
    expect(short).toContain('…')
  })

  it('max 很小时也要保住开头，不把文件名截成只有一个省略号', () => {
    const short = shortFileName('4100121884；4100122848.xlsx', 12)

    expect(short.startsWith('41001218')).toBe(true)
    expect(short).toContain('…')
  })
})

describe('formatFileSize', () => {
  it('按量级给出 B / KB / MB', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(900)).toBe('900 B')
    expect(formatFileSize(27282)).toBe('26.6 KB')
    expect(formatFileSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })

  it('飞书没给 size 时留空，不显示 0 B', () => {
    expect(formatFileSize(undefined)).toBe('')
    expect(formatFileSize(-1)).toBe('')
  })
})

describe('addAttachments / removeAttachment', () => {
  const a: AttachmentItem = { file_token: 'ft-a', name: 'a.pdf' }
  const b: AttachmentItem = { file_token: 'ft-b', name: 'b.pdf' }
  const c: AttachmentItem = { file_token: 'ft-c', name: 'c.pdf' }

  it('追加到末尾', () => {
    expect(addAttachments([a], [b, c]).map((i) => i.file_token)).toEqual(['ft-a', 'ft-b', 'ft-c'])
  })

  it('上传同一个文件两次不会出现两条', () => {
    expect(addAttachments([a], [a])).toHaveLength(1)
  })

  it('不修改入参（抽屉的脏值比较依赖原值不变）', () => {
    const original = [a]
    addAttachments(original, [b])
    removeAttachment(original, 'ft-a')

    expect(original).toEqual([a])
  })

  it('删除只影响目标项', () => {
    expect(removeAttachment([a, b, c], 'ft-b').map((i) => i.file_token)).toEqual(['ft-a', 'ft-c'])
  })
})
