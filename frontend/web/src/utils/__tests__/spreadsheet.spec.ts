import { describe, expect, it } from 'vitest'
import {
  buildSheet,
  encodeCell,
  MAX_PREVIEW_COLS,
  MAX_PREVIEW_ROWS,
  parseSpreadsheet,
  type RawSheet,
  type SheetCell,
} from '@/utils/spreadsheet'

/**
 * 表格附件预览的解析层。vitest 环境是 node，不挂组件，所以测的是**数据**：
 * 单元格文本取哪个字段、合并单元格怎么折成 colspan/rowspan、超大表怎么截断。
 *
 * 这几个点都对着真实报价单的形态：表头 A1:F1 跨列合并、日期与百分比的显示值
 * 与原始值不同、几十行数据。
 */

/**
 * 造一张表：`cells` 是 `{"A1": {...}}`。
 *
 * `ref` 缺省时按实际给出的格子**推导**——真实 SheetJS 解析出来的表 `!ref` 一定存在，
 * 而 `!ref` 缺失在这里的语义是「空表」（见对应用例），不能拿它当默认值。
 */
function sheet(cells: Record<string, unknown>, ref?: string, merges?: { s: [number, number]; e: [number, number] }[]) {
  return {
    ...cells,
    '!ref': ref ?? deriveRef(cells),
    '!merges': merges?.map((m) => ({ s: { r: m.s[0], c: m.s[1] }, e: { r: m.e[0], c: m.e[1] } })),
  } as unknown as RawSheet
}

/** 从格子地址推 `A1:C2` 这样的 ref */
function deriveRef(cells: Record<string, unknown>): string | undefined {
  let maxR = -1
  let maxC = -1
  for (const address of Object.keys(cells)) {
    const match = /^([A-Z]+)(\d+)$/.exec(address)
    if (!match) continue
    let c = 0
    for (const ch of match[1]) c = c * 26 + (ch.charCodeAt(0) - 64)
    maxR = Math.max(maxR, Number(match[2]) - 1)
    maxC = Math.max(maxC, c - 1)
  }
  if (maxR < 0) return undefined
  return `A1:${encodeCell(maxR, maxC)}`
}

/** 取网格里的文本，方便断言（null = 被合并吃掉） */
function texts(cells: (SheetCell | null)[][]): (string | null)[][] {
  return cells.map((row) => row.map((cell) => (cell ? cell.text : null)))
}

describe('encodeCell', () => {
  it('列号转字母，含进位（0→A、25→Z、26→AA）', () => {
    expect(encodeCell(0, 0)).toBe('A1')
    expect(encodeCell(4, 25)).toBe('Z5')
    expect(encodeCell(0, 26)).toBe('AA1')
    expect(encodeCell(0, 27)).toBe('AB1')
    expect(encodeCell(0, 51)).toBe('AZ1')
    expect(encodeCell(0, 52)).toBe('BA1')
  })
})

describe('buildSheet', () => {
  it('摊成网格，优先取格式化值 w 而不是原始值 v', () => {
    // 日期在 v 里是序列号、百分比在 v 里是小数，界面要的是 w
    const ws = sheet({
      A1: { v: '货物品名', w: '货物品名' },
      B1: { v: 45678, w: '2025-01-15' },
      A2: { v: 0.15, w: '15%' },
    })

    expect(texts(buildSheet('Sheet1', ws).cells)).toEqual([
      ['货物品名', '2025-01-15'],
      ['15%', ''],
    ])
  })

  it('v 有值但 w 缺失时回落，null/undefined 渲染成空串', () => {
    const ws = sheet({ A1: { v: 42 }, B1: { v: null }, C1: { w: '' } })

    expect(texts(buildSheet('S', ws).cells)).toEqual([['42', '', '']])
  })

  it('合并单元格折成 colspan/rowspan，被覆盖的格子置 null', () => {
    // A1 跨两列（表头常见形态），A2 向下跨两行
    const ws = sheet({ A1: { w: '报价单' }, A2: { w: '左侧' }, B2: { w: '右1' }, B3: { w: '右2' } }, 'A1:B3', [
      { s: [0, 0], e: [0, 1] },
      { s: [1, 0], e: [2, 0] },
    ])

    const { cells } = buildSheet('S', ws)

    expect(cells[0][0]).toMatchObject({ text: '报价单', colspan: 2, rowspan: 1 })
    expect(cells[0][1]).toBeNull()
    expect(cells[1][0]).toMatchObject({ text: '左侧', colspan: 1, rowspan: 2 })
    expect(cells[2][0]).toBeNull()
    expect(cells[1][1]).toMatchObject({ text: '右1' })
    expect(cells[2][1]).toMatchObject({ text: '右2' })
  })

  it('合并区跨出截断窗口时削掉跨度，不撑破表格', () => {
    // 只在窗口里露出 1 列的合并（原本跨 3 列）
    const ws = sheet({ A1: { w: 'x' } }, 'A1:C1', [{ s: [0, 0], e: [0, 2] }])

    const { cells } = buildSheet('S', ws)

    expect(cells[0][0]?.colspan).toBe(3)

    // 窗口只有 2 列时，同一个合并要削成 2
    const cols = 'A1:B1'
    const narrow = buildSheet('S', sheet({ A1: { w: 'x' } }, cols, [{ s: [0, 0], e: [0, 2] }]))
    expect(narrow.cells[0][0]?.colspan).toBe(2)
  })

  it('整块在窗口外的合并不影响网格', () => {
    const ws = sheet({ A1: { w: 'a' } }, 'A1:A1', [{ s: [5, 5], e: [6, 6] }])

    expect(texts(buildSheet('S', ws).cells)).toEqual([['a']])
  })

  it('空表 / 没有 !ref 时给空网格（不是 undefined 崩掉）', () => {
    expect(buildSheet('S', sheet({})).cells).toEqual([])
    expect(buildSheet('S', undefined).cells).toEqual([])
  })

  it('单个格子的表也能解析（!ref 只有 A1、没有冒号）', () => {
    expect(texts(buildSheet('S', sheet({ A1: { w: 'only' } }, 'A1')).cells)).toEqual([['only']])
  })

  it('不是 A1 起点的表按起点偏移取值', () => {
    // 真实表常有空的前导行/列，!ref 从 C3 开始
    const ws = sheet({ C3: { w: '起点' }, D3: { w: '右边' } }, 'C3:D3')

    expect(texts(buildSheet('S', ws).cells)).toEqual([['起点', '右边']])
  })
})

describe('截断', () => {
  it('行超上限只留前 MAX_PREVIEW_ROWS 行', () => {
    const cells: Record<string, unknown> = {}
    for (let r = 1; r <= MAX_PREVIEW_ROWS + 5; r++) cells[`A${r}`] = { w: `r${r}` }

    const { cells: grid } = buildSheet('S', sheet(cells, `A1:A${MAX_PREVIEW_ROWS + 5}`))

    expect(grid).toHaveLength(MAX_PREVIEW_ROWS)
    expect(grid[0][0]?.text).toBe('r1')
    expect(grid[MAX_PREVIEW_ROWS - 1][0]?.text).toBe(`r${MAX_PREVIEW_ROWS}`)
  })

  it('列超上限只留前 MAX_PREVIEW_COLS 列', () => {
    const { cells: grid } = buildSheet('S', sheet({ A1: { w: 'a' } }, `A1:${encodeCell(0, MAX_PREVIEW_COLS + 3)}`))

    expect(grid[0]).toHaveLength(MAX_PREVIEW_COLS)
  })
})

describe('parseSpreadsheet', () => {
  it('解析真实工作簿：多工作表、合并、超限标记', async () => {
    const XLSX = await import('xlsx')
    const wb = XLSX.utils.book_new()
    const ws = XLSX.utils.aoa_to_sheet([
      ['报价单', '', ''],
      ['供应商', '成本金额', '币种'],
      ['A 货代', 1234.5, 'CNY'],
    ])
    ws['!merges'] = [XLSX.utils.decode_range('A1:C1')]
    XLSX.utils.book_append_sheet(wb, ws, '报价')
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['第二张']]), '备注')

    const written = XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer
    const preview = await parseSpreadsheet(written)

    expect(preview.sheets.map((s) => s.name)).toEqual(['报价', '备注'])
    expect(preview.truncated).toBe(false)
    // 表头合并：第一行只渲染一格，跨 3 列
    expect(preview.sheets[0].cells[0][0]).toMatchObject({ text: '报价单', colspan: 3 })
    expect(preview.sheets[0].cells[0][1]).toBeNull()
    expect(texts(preview.sheets[0].cells)[2]).toEqual(['A 货代', '1234.5', 'CNY'])
  })

  it('坏掉的表格会抛错，交给调用方吞掉（不当成加载失败）', async () => {
    // 有 ZIP 魔数但没有合法 ZIP 内容：SheetJS 认它是表格、解压时才炸
    const broken = new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...new Array<number>(64).fill(0x00)])

    await expect(parseSpreadsheet(broken.buffer as ArrayBuffer)).rejects.toThrow()
  })

  it('纯文本会被 SheetJS 当成单元格内容（所以「解析成功但没东西」也是常态）', async () => {
    // 这条是给弹窗定行为的：不能靠「抛没抛错」判断有没有内容可显示，
    // 得看网格是不是空的——所以模板里那个 ElEmpty 兜底是必要的。
    const preview = await parseSpreadsheet(new TextEncoder().encode('NOT A TABLE').buffer as ArrayBuffer)

    expect(preview.sheets).toHaveLength(1)
    expect(preview.sheets[0].cells[0][0]?.text).toBe('NOT A TABLE')
  })
})
