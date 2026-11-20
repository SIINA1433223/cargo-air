/**
 * 飞书附件字段（type 17）的前端处理：解析、类型判定、文件名与体积格式化。
 *
 * 本模块**纯函数、不发请求**，便于 vitest 覆盖（vitest 环境是 node，不挂载组件）。
 *
 * 两条硬约束（都来自真实数据/事故，改动前先读）：
 *
 * 1. **绝不把 url / tmp_url 带到界面**（§10.3）。飞书原始附件对象带这两个字段，
 *    里面的地址是含临时凭证的下载链接，前端直接显示等于把凭证贴到页面上。
 *    后端 `normalize_field_value` 已经剥掉，这里再兜一层——两层都不泄漏。
 * 2. 文件名可能极长：真实数据里出现过把 10 个发票号拼进名字的「订舱单文件.xlsx」，
 *    单行放不下会把整行撑坏，所以展示一律走 `shortFileName`，完整名只放 title 提示。
 */

/** 附件字段里的一项（后端已剥掉 url/tmp_url） */
export interface AttachmentItem {
  file_token: string
  name?: string
  size?: number
  type?: string
}

/** 预览方式（§10.1）：PDF 用 iframe、图片用 Image 组件、表格自己渲染成网格，其余只能下载 */
export type AttachmentKind = 'pdf' | 'image' | 'excel' | 'other'

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'heic', 'tif', 'tiff']
/** 表格类附件。飞书的附件里这类是大头（报价单几乎都是 xlsx）。 */
const EXCEL_EXT = ['xlsx', 'xlsm', 'xlsb', 'xls', 'csv', 'ods']
const EXCEL_MIME = [
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-excel',
  'application/vnd.oasis.opendocument.spreadsheet',
  'text/csv',
]

/** 判断预览方式：先看飞书给的 mime，再看扩展名（mime 可能是 octet-stream） */
export function attachmentKind(item: AttachmentItem): AttachmentKind {
  const mime = (item.type ?? '').toLowerCase()
  const ext = extensionOf(item.name ?? item.file_token)

  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (mime.startsWith('image/') || IMAGE_EXT.includes(ext)) return 'image'
  if (EXCEL_MIME.includes(mime) || EXCEL_EXT.includes(ext)) return 'excel'
  return 'other'
}

/** 能在线预览的类型（PDF / 图片 / 表格）；其余弹「请下载查看」 */
export function canPreviewInline(item: AttachmentItem): boolean {
  return attachmentKind(item) !== 'other'
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  if (dot < 0 || dot === name.length - 1) return ''
  return name.slice(dot + 1).toLowerCase()
}

/**
 * 把字段值解析成附件数组。宽容处理三种形状：
 * - 数组（正常）
 * - **裸对象**——飞书的「单值字段返回裸 dict」是踩过的坑（关联字段上是这样），
 *   这里同样兜一层，否则整个附件列表会被静默丢掉
 * - 字符串（只有 file_token 的极端情况，例如手工构造）
 *
 * 不含 file_token 的项一律丢弃：没有 token 就既不能预览也不能下载，留在界面上
 * 只会得到一个点了没反应的条目。
 */
export function toAttachmentList(value: unknown): AttachmentItem[] {
  if (value === null || value === undefined || value === '') return []

  const raw: unknown[] = Array.isArray(value) ? value : [value]
  const out: AttachmentItem[] = []
  const seen = new Set<string>()

  for (const entry of raw) {
    const item = toAttachment(entry)
    if (!item || seen.has(item.file_token)) continue
    seen.add(item.file_token)
    out.push(item)
  }
  return out
}

function toAttachment(entry: unknown): AttachmentItem | null {
  if (typeof entry === 'string') {
    return entry ? { file_token: entry } : null
  }
  if (typeof entry !== 'object' || entry === null) return null

  // 只挑白名单字段：url / tmp_url / 未来飞书新增的任何字段都进不来
  const src = entry as Record<string, unknown>
  const token = src.file_token
  if (typeof token !== 'string' || !token) return null

  const item: AttachmentItem = { file_token: token }
  if (typeof src.name === 'string' && src.name) item.name = src.name
  if (typeof src.size === 'number' && Number.isFinite(src.size)) item.size = src.size
  if (typeof src.type === 'string' && src.type) item.type = src.type
  return item
}

/** 展示名：没有名字时用占位，不回退到 file_token（token 对用户没有意义） */
export function fileNameOf(item: AttachmentItem): string {
  return item.name || '未命名附件'
}

/**
 * 超长文件名的单行展示：保留开头与扩展名，中间用一个省略号。
 * 真实数据的名字前半段是发票号（用户真正要看的），后半段常是重复的号码与后缀。
 */
export function shortFileName(name: string, max = 30): string {
  if (name.length <= max) return name

  const ext = extensionOf(name)
  const tail = ext ? `.${ext}` : ''
  // 至少给头部留 8 个字符，避免 max 很小时把开头也截没了
  const headLen = Math.max(8, max - tail.length - 1)
  if (headLen + tail.length + 1 > name.length) return name
  return `${name.slice(0, headLen)}…${tail || name.slice(-4)}`
}

/** 体积展示；飞书没给 size 时留空而不是显示 0 B */
export function formatFileSize(bytes?: number): string {
  if (bytes === undefined || bytes === null || !Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 追加附件（上传后使用），按 file_token 去重，不修改入参 */
export function addAttachments(current: AttachmentItem[], added: AttachmentItem[]): AttachmentItem[] {
  const seen = new Set(current.map((i) => i.file_token))
  const merged = [...current]
  for (const item of added) {
    if (seen.has(item.file_token)) continue
    seen.add(item.file_token)
    merged.push(item)
  }
  return merged
}

/** 移除附件（删除按钮），按 file_token，不修改入参 */
export function removeAttachment(current: AttachmentItem[], fileToken: string): AttachmentItem[] {
  return current.filter((i) => i.file_token !== fileToken)
}
