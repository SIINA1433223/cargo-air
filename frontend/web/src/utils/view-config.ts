/**
 * 索引页「自定义列」配置：每个用户对每个工作台各自一份。
 *
 * 存 localStorage（按 userId + role 分桶），不落库。理由：这是纯展示层的个性化配置
 * （选哪些字段、什么顺序、加几条拼接列），改动只影响自己看到的界面，不涉及业务数据；
 * 键里带 userId 是为了同一台机器上换人登录时互不干扰。
 *
 * 若将来要做「部门模板」或「跨设备同步」，只需替换本文件的读写实现，调用方不用动。
 */

/**
 * 一列，三种形态：
 * - `field`    直接展示某个飞书字段；
 * - `template` 用模板把若干字段拼成一句（**只读**——拼接串拆不回原字段，见 `renderTemplate`）；
 * - `blank`    空闲列，落到飞书上一个**真实存在**的文本字段上。内容是用户在索引页直接敲的
 *              真值，所有人可见。
 *
 * 空列的「显示名」与「飞书字段名」是**分开的两个东西**：
 * - `label`  用户自己起的列名（列头显示、筛选里显示），可随时改；
 * - `suffix` 建字段时生成的一段后缀 `{用户名}_{短码}`，永不改。
 * 飞书侧的真实字段名是 `blankFieldName()` 拼出来的 `{label}_{suffix}`。
 *
 * 为什么要拼这么长：空列是**真的往生产表里加字段**，两个人各建一个「备注」就撞名了。
 * 拼上用户名能看出归属，拼上短码保证永不重名——用户输入「备注」时不会因为表里已经
 * 有个「备注」而失败（那正是之前的问题）。
 */
export type ViewColumn =
  | { kind: 'field'; name: string }
  | { kind: 'template'; label: string; expression: string }
  | { kind: 'blank'; label: string; suffix: string }

/** 老版本的空列：没有独立显示名，`name` 直接就是飞书字段名。仅用于回读时兼容。 */
interface LegacyBlankColumn {
  kind: 'blank'
  name: string
}

/** 筛选操作符：为空 / 非空 / 等于 / 包含。 */
export type FilterOperator = 'empty' | 'not_empty' | 'equals' | 'contains'

export interface FilterCondition {
  /** 仅用于列表 key，不参与判定 */
  id: string
  /** 被筛的列：真实字段名，或模板列的 label */
  field: string
  operator: FilterOperator
  /** 等于/包含时才有意义 */
  value?: string
}

/** 保存下来可复用的筛选预设（「命名视图」）。列配置不随视图切换。 */
export interface FilterView {
  id: string
  name: string
  conjunction: 'and' | 'or'
  conditions: FilterCondition[]
}

/**
 * 视图里的布局个性化——像 Excel 那样拖出来的样子。
 *
 * 全部只存在本地（同列的 localStorage 桶），**一个字节都不写飞书**：行高列宽颜色是
 * 「我怎么看这张表」，不是业务数据，没有理由让表协作者也跟着变。
 *
 * 键一律用稳定标识：行用 `record_id`（翻页、筛选都不会变），列用 `colId()`——
 * 千万别用列下标，列一重排，按下标存的高度颜色就整体错位了。
 */
export interface ViewConfig {
  columns: ViewColumn[]
  /** 保存的筛选视图；缺省表示还没存过 */
  views?: FilterView[]
  /** 当前选中的视图（工具栏下拉回显用） */
  activeViewId?: string
  /** record_id → 行高（px），逐行拖出来的 */
  rowHeights?: Record<string, number>
  /** colId → 列宽（px） */
  columnWidths?: Record<string, number>
  /** `${record_id}:${colId}` → 单元格底色 */
  cellColors?: Record<string, string>
  /** record_id → 整行底色 */
  rowColors?: Record<string, string>
  /** colId → 整列底色 */
  columnColors?: Record<string, string>
}

/** 行高/列宽的允许区间；拖拽时按这个夹住，回读时也按这个过滤脏数据。 */
export const ROW_HEIGHT_MIN = 20
export const ROW_HEIGHT_MAX = 200
export const COLUMN_WIDTH_MIN = 60
export const COLUMN_WIDTH_MAX = 600

/** 把像素值夹进区间并取整（拖拽与回读共用，保证两端口径一致）。 */
export function clampSize(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)))
}

/**
 * 列的稳定标识。
 *
 * 拼接列没有字段名，用「列名」当标识——同一视图里两个拼接列同名的话会互相串，
 * 但列名是用户在列配置里自己取的，重名本来就已经分不清了，不再额外处理。
 *
 * 空列认 `suffix` 而不是拼出来的字段名：**改列名不该把这一列的宽度和颜色弄丢**
 * （行高列宽填色全都按 colId 存）。
 */
export function colId(col: ViewColumn): string {
  if (col.kind === 'template') return `tpl:${col.label}`
  if (col.kind === 'blank') return `blank:${col.suffix || col.label}`
  return col.name
}

/**
 * 列头显示的文字。空列与拼接列显示用户自己起的名字，真字段显示字段名。
 */
export function columnTitle(col: ViewColumn): string {
  return col.kind === 'field' ? col.name : col.label
}

/** 空列在飞书表里的**真实字段名**：`{我起的列名}_{用户名}_{短码}`。 */
export function blankFieldName(col: Pick<ViewColumn & { kind: 'blank' }, 'label' | 'suffix'>): string {
  return col.suffix ? `${col.label}_${col.suffix}` : col.label
}

const SUFFIX_CODE_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'

/**
 * 生成空列的命名后缀 `{用户名}_{短码}`。
 *
 * 用户名里的下划线和空白一律换成 `-`：这个后缀要靠下划线拆出「用户名」和「短码」，
 * 用户名自带下划线会把结构搞乱。短码随机，撞上的概率可以忽略；真撞了后端那道重名
 * 检查会拦下来并给出明确提示，不会变成一个看不懂的飞书错误码。
 */
export function makeBlankSuffix(userName: string): string {
  const who = userName.trim().replaceAll(/[\s_]+/g, '-') || '用户'
  let code = ''
  for (let i = 0; i < 4; i += 1) {
    code += SUFFIX_CODE_CHARS[Math.floor(Math.random() * SUFFIX_CODE_CHARS.length)]
  }
  return `${who}_${code}`
}

/**
 * 从飞书返回的完整字段名里切出后缀。
 *
 * 理论上后端会原样返回我们发过去的名字，直接用拼好的就行；但 `field_name` 是飞书
 * 说了算的（去空白、截断都可能），以后缀为准更稳——切不出来就当没有后缀（老字段），
 * 大不了改名时用纯列名。
 */
export function suffixFromFieldName(fullName: string, label: string): string {
  const prefix = `${label}_`
  return fullName.startsWith(prefix) ? fullName.slice(prefix.length) : ''
}

/** 单元格底色的键（`cellColors` 的键就是这个）。 */
export function cellColorKey(recordId: string, columnId: string): string {
  return `${recordId}:${columnId}`
}

/** 操作符元数据，供筛选 UI 渲染（`needsValue` 决定要不要显示值输入框）。 */
export const FILTER_OPERATORS: readonly { value: FilterOperator; label: string; needsValue: boolean }[] = [
  { value: 'empty', label: '为空', needsValue: false },
  { value: 'not_empty', label: '非空', needsValue: false },
  { value: 'equals', label: '等于', needsValue: true },
  { value: 'contains', label: '包含', needsValue: true },
]

const KEY_PREFIX = 'weida_view_config'

/** 模板占位符：`{字段名}`。字段名里允许空格与符号（飞书字段名就有 `客户 REF NO.` 这种）。 */
const PLACEHOLDER_RE = /\{([^{}]+)\}/g

function storageKey(userId: string | number, role: string): string {
  return `${KEY_PREFIX}:${userId}:${role}`
}

/** 读该用户的列配置；没配过返回 null（调用方据此回落到后端默认列）。 */
export function getViewConfig(userId: string | number, role: string): ViewConfig | null {
  try {
    const raw = localStorage.getItem(storageKey(userId, role))
    if (!raw) return null
    const parsed = JSON.parse(raw) as ViewConfig
    if (!parsed || !Array.isArray(parsed.columns)) return null
    const views = Array.isArray(parsed.views) ? parsed.views.filter(isValidView) : []
    return {
      columns: parsed.columns.filter(isValidColumn).map(normalizeBlank),
      views,
      activeViewId: typeof parsed.activeViewId === 'string' ? parsed.activeViewId : undefined,
      rowHeights: sanitizeNumberMap(parsed.rowHeights, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX),
      columnWidths: sanitizeNumberMap(parsed.columnWidths, COLUMN_WIDTH_MIN, COLUMN_WIDTH_MAX),
      cellColors: sanitizeStringMap(parsed.cellColors),
      rowColors: sanitizeStringMap(parsed.rowColors),
      columnColors: sanitizeStringMap(parsed.columnColors),
    }
  } catch (e) {
    // 坏数据不该让整个页面打不开：当作没配过
    console.warn('[view-config] 读取列配置失败，已回落到默认列', e)
    return null
  }
}

export function saveViewConfig(userId: string | number, role: string, cfg: ViewConfig): void {
  try {
    localStorage.setItem(storageKey(userId, role), JSON.stringify(cfg))
  } catch (e) {
    console.warn('[view-config] 保存列配置失败', e)
  }
}

/** 恢复默认：删掉本地配置，下次读就是 null。 */
export function clearViewConfig(userId: string | number, role: string): void {
  try {
    localStorage.removeItem(storageKey(userId, role))
  } catch (e) {
    console.warn('[view-config] 清除列配置失败', e)
  }
}

/**
 * 尺寸表（行高/列宽）回读：丢掉非数字、非有限、区间外的键。
 *
 * 夹区间而不是丢弃：早先版本如果存过 `0` 或 `9999` 这种值（手改 localStorage、
 * 或者夹取逻辑改过），夹回来总比整张表的行高突然失效强。全被过滤掉就返回
 * `undefined`，而不是空对象——调用方判空只看一个条件。
 */
function sanitizeNumberMap(v: unknown, min: number, max: number): Record<string, number> | undefined {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return undefined
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(v as Record<string, unknown>)) {
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    out[key] = clampSize(value, min, max)
  }
  return Object.keys(out).length ? out : undefined
}

/** 颜色表回读：只留非空字符串，其余（数字、null、对象）一律丢掉。 */
function sanitizeStringMap(v: unknown): Record<string, string> | undefined {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return undefined
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(v as Record<string, unknown>)) {
    if (typeof value === 'string' && value) out[key] = value
  }
  return Object.keys(out).length ? out : undefined
}

function isValidColumn(col: unknown): col is ViewColumn | LegacyBlankColumn {
  if (!col || typeof col !== 'object') return false
  const c = col as Record<string, unknown>
  if (c.kind === 'field') return typeof c.name === 'string' && c.name.length > 0
  if (c.kind === 'blank') {
    // 新形态：显示名 + 后缀；老形态只有 name（就是飞书字段名），下面补出显示名
    if (typeof c.label === 'string' && c.label.length > 0) return true
    return typeof c.name === 'string' && c.name.length > 0
  }
  if (c.kind === 'template') return typeof c.label === 'string' && typeof c.expression === 'string'
  return false
}

/** 老配置升级：`{kind:'blank', name}` → `{kind:'blank', label: name, suffix: ''}`。 */
function normalizeBlank(col: ViewColumn | LegacyBlankColumn): ViewColumn {
  if (col.kind !== 'blank') return col
  const legacy = col as LegacyBlankColumn & Partial<{ label: string; suffix: string }>
  if (typeof legacy.label === 'string' && legacy.label) return col as ViewColumn
  return { kind: 'blank', label: String(legacy.name ?? ''), suffix: '' }
}

const OPERATORS: readonly FilterOperator[] = ['empty', 'not_empty', 'equals', 'contains']

function isValidView(view: unknown): view is FilterView {
  if (!view || typeof view !== 'object') return false
  const v = view as Record<string, unknown>
  if (typeof v.id !== 'string' || typeof v.name !== 'string') return false
  if (v.conjunction !== 'and' && v.conjunction !== 'or') return false
  return Array.isArray(v.conditions)
}

/** 丢弃结构不对的筛选条件：坏一条不该让整个视图失效。 */
export function sanitizeConditions(conditions: readonly FilterCondition[]): FilterCondition[] {
  return conditions.filter(
    (c) =>
      !!c &&
      typeof c.field === 'string' &&
      c.field.length > 0 &&
      OPERATORS.includes(c.operator) &&
      (c.operator === 'empty' || c.operator === 'not_empty' || typeof c.value === 'string')
  )
}

/** 从后端默认列名生成初始配置。 */
export function defaultColumns(names: readonly string[]): ViewColumn[] {
  return names.map((name) => ({ kind: 'field' as const, name }))
}

/**
 * 字段值 → 单元格文案。
 *
 * 值已由后端归一化，这里只收口「空值 / 布尔 / 数组」三种形状。
 * 该函数同时供表格列与模板插值使用，保证同一字段在两种列里长得一样。
 */
export function formatFieldValue(v: unknown, emptyText = '—'): string {
  if (v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) return emptyText
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (Array.isArray(v)) return v.map((i) => String(i)).join('、')
  return String(v)
}

/**
 * 渲染拼接列：把 `{字段名}` 换成该行的字段值，其余原样保留。
 *
 * 纯字符串替换，**不用 eval / new Function** —— 表达式来自用户输入，
 * 求值器一旦能执行代码就是注入面。这里的能力边界是刻意收窄的：
 * 只做「取值 + 用字面文字与字段拼起来」，不做算术与条件。
 *
 * 两条空值规则（本表实测大量字段是空的，不处理的话模板没法看）：
 * - 空字段替换成**空串**而不是 `—`：`{A} · {B}` 里 B 为空时，宁可少一段也不要在
 *   业务文案中间插一个 `—`。整格都空由调用方兜底显示 `—`。
 * - 因此产生的**空行会被丢掉**（换行分隔的模板不会出现空行），每行首尾空白也一并去掉。
 */
export function renderTemplate(expression: string, fields: Record<string, unknown>): string {
  if (!expression) return ''
  const filled = expression.replace(PLACEHOLDER_RE, (_m, name: string) => formatFieldValue(fields[name.trim()], ''))
  return filled
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
    .join('\n')
}

/**
 * 把任意字段值摊平成一组可比较的字符串。
 *
 * 多选/人员/关联是数组，摊成多段；空值（null / undefined / '' / []）摊成空数组，
 * 让「为空」判定和「等于」判定共用同一套口径。
 */
function asComparable(v: unknown): string[] {
  if (v === null || v === undefined || v === '') return []
  if (Array.isArray(v)) return v.filter((i) => i !== null && i !== undefined && i !== '').map((i) => String(i))
  if (typeof v === 'boolean') return [v ? '是' : '否']
  return [String(v)]
}

/** 单条条件是否命中。`raw` 是该行在该列上的原始值（模板列传渲染后的字符串）。 */
function matchOne(condition: FilterCondition, raw: unknown): boolean {
  const values = asComparable(raw)
  switch (condition.operator) {
    case 'empty':
      return values.length === 0
    case 'not_empty':
      return values.length > 0
    case 'equals': {
      const needle = (condition.value ?? '').trim().toLowerCase()
      if (!needle) return false
      return values.some((s) => s.trim().toLowerCase() === needle)
    }
    case 'contains': {
      const needle = (condition.value ?? '').trim().toLowerCase()
      // 值为空时「包含」无从谈起：不算命中，免得一勾就全表通过
      if (!needle) return false
      return values.join('、').toLowerCase().includes(needle)
    }
    default:
      return true
  }
}

/**
 * 多条件筛选：`conditions` 按 `conjunction`（且/或）组合。
 *
 * 取值的活儿交给调用方（`getValue`）——真实字段取 `row.fields[name]`，拼接列取
 * `renderTemplate(expression, row.fields)`，这样「筛选涵盖所有个人字段」这句话才
 * 包括用户自己拼出来的列。
 *
 * 没有条件时返回 true（等于不过滤）。
 */
export function matchesFilter(
  conditions: readonly FilterCondition[],
  conjunction: 'and' | 'or',
  getValue: (field: string) => unknown
): boolean {
  const active = sanitizeConditions(conditions)
  if (!active.length) return true
  const results = active.map((c) => matchOne(c, getValue(c.field)))
  return conjunction === 'or' ? results.some(Boolean) : results.every(Boolean)
}
