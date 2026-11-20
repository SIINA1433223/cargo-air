/**
 * 表格附件（xlsx / xls / csv）的预览解析：把工作簿摊成可渲染的二维网格。
 *
 * 为什么不用 SheetJS 自带的 `sheet_to_html`：那个函数返回的是一整段 HTML 字符串，
 * 得用 `v-html` 塞进页面——而单元格内容来自**用户上传的文件**，等于把一条 XSS 通道
 * 接进了业务系统（上传一个恶意 xlsx，别人一点预览就中招）。这里只产出**数据**，
 * 由 Vue 模板插值渲染，转义由框架负责，从构造上就没有注入面。
 *
 * 解析出网格而不是「每行一个数组」，是为了保住**合并单元格**：报价单的表头大量使用
 * `A1:F1` 这种跨列合并，摊平后每格都重复一遍标题，看起来像坏了。
 *
 * 本模块**不发请求**，`buildSheet` 是纯函数，便于 vitest 覆盖（vitest 环境是 node）。
 */

/** 网格里的一格 */
export interface SheetCell {
  /** 显示文本（优先用 Excel 里格式化后的文本，日期/百分比才是人看的样子） */
  text: string
  /** 跨列数（合并单元格；1 = 不跨） */
  colspan: number
  /** 跨行数（合并单元格；1 = 不跨） */
  rowspan: number
}

/** 一个工作表：`null` 表示这一格被上方/左方的合并单元格吃掉了，不渲染 */
export interface PreviewSheet {
  name: string
  cells: (SheetCell | null)[][]
}

export interface SheetPreview {
  sheets: PreviewSheet[]
  /** 超出上限被截断（行或列），界面据此提示「只显示前 N 行」 */
  truncated: boolean
}

/** 预览的行上限。真实报价单几十行，200 行够看，再多也没人会在弹窗里翻。 */
export const MAX_PREVIEW_ROWS = 200
/** 列上限：报价单列多，60 列之后基本是空白列，而列数直接决定表格宽度 */
export const MAX_PREVIEW_COLS = 60

/** 只用到的那几个字段，避免本模块依赖 SheetJS 的类型（那样就跑不进 node 单测了） */
interface RawCell {
  /** 格式化后的文本，Excel 显示什么它就是什么 */
  w?: string
  v?: unknown
}

interface RawMerge {
  s: { r: number; c: number }
  e: { r: number; c: number }
}

export interface RawSheet {
  '!ref'?: string
  '!merges'?: RawMerge[]
  [address: string]: unknown
}

/**
 * 读出附件字节，返回每个工作表的网格。
 *
 * SheetJS 走**动态 import**：它有大几百 KB，只在真的点开表格附件时才该下载。
 */
export async function parseSpreadsheet(buffer: ArrayBuffer): Promise<SheetPreview> {
  const XLSX = await import('xlsx')
  const wb = XLSX.read(buffer, { type: 'array' })
  const sheets = wb.SheetNames.map((name) => buildSheet(name, wb.Sheets[name] as RawSheet | undefined))
  // 只要有一个表被截断就提示，用户至少知道「下面还有东西」
  const truncated = sheets.some(
    (s) => s.cells.length >= MAX_PREVIEW_ROWS || s.cells.some((row) => row.length >= MAX_PREVIEW_COLS)
  )
  return { sheets, truncated }
}

/**
 * 把一张工作表摊成网格，并把合并单元格折成 colspan/rowspan。
 *
 * 单元格文本取 `w`（格式化值）而不是 `v`：`v` 是原始值，日期会变成 `45678` 这样的序列号，
 * 百分比会变成 `0.15`——用户看的是报价单，不是单元格的底层表示。
 */
export function buildSheet(name: string, ws: RawSheet | undefined): PreviewSheet {
  const range = decodeRange(ws?.['!ref'])
  if (!ws || !range) return { name, cells: [] }

  const rowCount = Math.min(range.e.r - range.s.r + 1, MAX_PREVIEW_ROWS)
  const colCount = Math.min(range.e.c - range.s.c + 1, MAX_PREVIEW_COLS)

  const cells: (SheetCell | null)[][] = []
  for (let r = 0; r < rowCount; r++) {
    const row: (SheetCell | null)[] = []
    for (let c = 0; c < colCount; c++) {
      const cell = ws[encodeCell(range.s.r + r, range.s.c + c)] as RawCell | undefined
      row.push({ text: cellText(cell), colspan: 1, rowspan: 1 })
    }
    cells.push(row)
  }

  for (const merge of ws['!merges'] ?? []) {
    // 合并区的左上角在可视窗口里的相对坐标
    const r = merge.s.r - range.s.r
    const c = merge.s.c - range.s.c
    if (r < 0 || c < 0 || r >= rowCount || c >= colCount) continue // 整块都在窗口外

    const anchor = cells[r]?.[c]
    if (!anchor) continue
    // 跨出窗口的部分要削掉：算出 5 列宽但只显示 2 列，表格会被撑破
    const spanC = Math.min(merge.e.c - merge.s.c + 1, colCount - c)
    const spanR = Math.min(merge.e.r - merge.s.r + 1, rowCount - r)
    anchor.colspan = Math.max(1, spanC)
    anchor.rowspan = Math.max(1, spanR)

    for (let dr = 0; dr < spanR; dr++) {
      for (let dc = 0; dc < spanC; dc++) {
        if (dr === 0 && dc === 0) continue
        cells[r + dr][c + dc] = null
      }
    }
  }

  return { name, cells }
}

function cellText(cell: RawCell | undefined): string {
  if (!cell) return ''
  if (typeof cell.w === 'string') return cell.w
  const v = cell.v
  if (v === null || v === undefined) return ''
  if (v instanceof Date) return v.toLocaleString()
  return String(v)
}

/** `"A1:F20"` → 起止行列号；空表或 `!ref` 缺失时返回 null */
function decodeRange(ref: string | undefined): { s: { r: number; c: number }; e: { r: number; c: number } } | null {
  if (!ref) return null
  const [start, end] = ref.split(':')
  const s = decodeCell(start)
  // 单个格子的表 `!ref` 只有 `A1`（没有冒号）
  const e = decodeCell(end ?? start)
  if (!s || !e) return null
  return { s, e }
}

function decodeCell(address: string): { r: number; c: number } | null {
  const match = /^([A-Z]+)(\d+)$/i.exec(address.trim())
  if (!match) return null
  const letters = match[1].toUpperCase()
  let c = 0
  for (const ch of letters) c = c * 26 + (ch.charCodeAt(0) - 64)
  return { r: Number(match[2]) - 1, c: c - 1 }
}

/** 列号 → 字母（0 → A、26 → AA）。自己写是为了不 import SheetJS 的工具函数。 */
export function encodeCell(r: number, c: number): string {
  let letters = ''
  let n = c
  while (n >= 0) {
    letters = String.fromCharCode((n % 26) + 65) + letters
    n = Math.floor(n / 26) - 1
  }
  return `${letters}${r + 1}`
}
