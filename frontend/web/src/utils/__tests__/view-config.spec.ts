import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  COLUMN_WIDTH_MAX,
  COLUMN_WIDTH_MIN,
  ROW_HEIGHT_MAX,
  ROW_HEIGHT_MIN,
  blankFieldName,
  cellColorKey,
  clampSize,
  clearViewConfig,
  colId,
  columnTitle,
  defaultColumns,
  formatFieldValue,
  getViewConfig,
  makeBlankSuffix,
  matchesFilter,
  renderTemplate,
  sanitizeConditions,
  saveViewConfig,
  suffixFromFieldName,
  type FilterCondition,
  type FilterView,
  type ViewColumn,
} from '@/utils/view-config'

/**
 * 自定义列配置是整个「索引页自定义」的地基：模板渲染与字段取值形状必须钉死，
 * 否则用户配好的列会在换了字段类型后静默显示成空白。
 */

/** node 环境没有 localStorage，用内存实现顶上（同时暴露调用记录便于断言） */
function installLocalStorage() {
  const store = new Map<string, string>()
  const impl = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size
    },
  }
  vi.stubGlobal('localStorage', impl)
  return store
}

describe('formatFieldValue 取值形状', () => {
  it('空值统一给占位符', () => {
    expect(formatFieldValue(null)).toBe('—')
    expect(formatFieldValue(undefined)).toBe('—')
    expect(formatFieldValue('')).toBe('—')
    expect(formatFieldValue([])).toBe('—')
  })

  it('布尔显示成是/否（复选框字段）', () => {
    expect(formatFieldValue(true)).toBe('是')
    expect(formatFieldValue(false)).toBe('否')
  })

  it('数组用顿号连接（多选/人员/关联）', () => {
    expect(formatFieldValue(['MXP', 'FRA'])).toBe('MXP、FRA')
    expect(formatFieldValue(['Heisenberg Jiang'])).toBe('Heisenberg Jiang')
  })

  it('数字与日期原样转字符串', () => {
    expect(formatFieldValue(0)).toBe('0')
    expect(formatFieldValue(12.5)).toBe('12.5')
    expect(formatFieldValue('2026-09-11 00:00:00')).toBe('2026-09-11 00:00:00')
  })
})

describe('renderTemplate 拼接列', () => {
  const row = { '客户 REF NO.': '9026010437-C01', 货物品名: '模具', 负责航线: ['Fairy Li', 'Kevin Qin'], 空字段: null }

  it('把 {字段名} 换成该行的值，字面文字原样保留', () => {
    expect(renderTemplate('{客户 REF NO.} · {货物品名}', row)).toBe('9026010437-C01 · 模具')
  })

  it('没有占位符时原样返回（用户可能只想写一句固定说明）', () => {
    expect(renderTemplate('待跟进', row)).toBe('待跟进')
  })

  it('字段为空值时留空而不是插一个「—」，也不会拼出「undefined」', () => {
    // 空字段在文案中间插「—」会让整句读不通（本表大量字段是空的）
    expect(renderTemplate('{货物品名}{空字段}', row)).toBe('模具')
    expect(renderTemplate('{空字段}', row)).toBe('')
    expect(renderTemplate('{不存在的字段}', row)).toBe('')
    expect(renderTemplate('{空字段}', row)).not.toContain('undefined')
  })

  it('数组字段按顿号拼接', () => {
    expect(renderTemplate('航线：{负责航线}', row)).toBe('航线：Fairy Li、Kevin Qin')
  })

  it('占位符里的首尾空格被容忍（用户手打时常带空格）', () => {
    expect(renderTemplate('{ 货物品名 }', row)).toBe('模具')
  })

  it('空表达式得到空串，不抛错', () => {
    expect(renderTemplate('', row)).toBe('')
  })

  it('支持换行分隔（用户可点「换行」按钮插入）', () => {
    expect(renderTemplate('{客户 REF NO.}\n{货物品名}', row)).toBe('9026010437-C01\n模具')
  })

  it('空字段撑出来的空行被丢掉，不会留下空行', () => {
    // 第二行字段为空 → 整行去掉，而不是留下一行空白
    expect(renderTemplate('{客户 REF NO.}\n{空字段}\n{货物品名}', row)).toBe('9026010437-C01\n模具')
    // 全空字段则整格为空串（由表格兜底显示「—」）
    expect(renderTemplate('{空字段}\n{空字段}', row)).toBe('')
  })

  it('每行首尾空白被去掉（分隔符模板不会留下悬空的空格）', () => {
    expect(renderTemplate('  {客户 REF NO.}  \n  {货物品名}  ', row)).toBe('9026010437-C01\n模具')
  })

  it('表达式只做取值替换——不执行任何代码（注入面）', () => {
    const spy = vi.fn()
    vi.stubGlobal('__wd_probe', spy)

    // 含调用语法也只会被当成「查一个叫 __wd_probe() 的字段」，查不到 → 空
    expect(renderTemplate('{__wd_probe()}', row)).toBe('')
    expect(spy).not.toHaveBeenCalled()
    // 花括号之外的文字原样保留，不被当成代码
    expect(renderTemplate('{{7*7}} <script>', row)).toBe('{} <script>')
  })
})

describe('defaultColumns', () => {
  it('把后端列名转成 field 列', () => {
    expect(defaultColumns(['商机编号', '业务类型'])).toEqual([
      { kind: 'field', name: '商机编号' },
      { kind: 'field', name: '业务类型' },
    ])
  })

  it('空输入得到空数组', () => {
    expect(defaultColumns([])).toEqual([])
  })
})

describe('matchesFilter 多条件筛选', () => {
  const row: Record<string, unknown> = {
    '客户 REF NO.': '9026010437-C01',
    业务类型: '空运',
    货物品名: '模具',
    '目的 港': ['MXP', 'FRA'],
    备注: '',
    件数: 3,
    是否拿到货: false,
  }
  const get = (f: string) => row[f]

  /** 少写点样板 */
  const cond = (field: string, operator: FilterCondition['operator'], value?: string): FilterCondition => ({
    id: `${field}-${operator}`,
    field,
    operator,
    value,
  })

  it('没有条件时不过滤', () => {
    expect(matchesFilter([], 'and', get)).toBe(true)
  })

  it('为空：空串/缺失/空数组都算空', () => {
    expect(matchesFilter([cond('备注', 'empty')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('不存在的字段', 'empty')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('货物品名', 'empty')], 'and', get)).toBe(false)
  })

  it('非空', () => {
    expect(matchesFilter([cond('货物品名', 'not_empty')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('备注', 'not_empty')], 'and', get)).toBe(false)
  })

  it('等于：数组字段任一项命中即可', () => {
    expect(matchesFilter([cond('业务类型', 'equals', '空运')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('业务类型', 'equals', '海运')], 'and', get)).toBe(false)
    expect(matchesFilter([cond('目的 港', 'equals', 'MXP')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('目的 港', 'equals', 'LAX')], 'and', get)).toBe(false)
  })

  it('等于/包含忽略大小写与首尾空格（用户手打的值不该要求大小写精确）', () => {
    expect(matchesFilter([cond('业务类型', 'equals', ' 空运 ')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('目的 港', 'contains', 'fra')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('客户 REF NO.', 'contains', '9026010437-c01')], 'and', get)).toBe(true)
  })

  it('包含：数组按顿号连起来匹配', () => {
    expect(matchesFilter([cond('货物品名', 'contains', '模')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('货物品名', 'contains', '钢材')], 'and', get)).toBe(false)
  })

  it('数值字段按字符串比较', () => {
    expect(matchesFilter([cond('件数', 'equals', '3')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('件数', 'contains', '3')], 'and', get)).toBe(true)
  })

  it('布尔字段比较「是/否」', () => {
    expect(matchesFilter([cond('是否拿到货', 'equals', '否')], 'and', get)).toBe(true)
    expect(matchesFilter([cond('是否拿到货', 'equals', '是')], 'and', get)).toBe(false)
  })

  it('值为空的「等于/包含」不算命中——否则一勾就全表通过', () => {
    expect(matchesFilter([cond('货物品名', 'contains', '   ')], 'and', get)).toBe(false)
    expect(matchesFilter([cond('货物品名', 'equals', '')], 'and', get)).toBe(false)
  })

  it('且：全中才算；或：中一个就算', () => {
    const conds = [cond('业务类型', 'equals', '空运'), cond('货物品名', 'contains', '钢材')]
    expect(matchesFilter(conds, 'and', get)).toBe(false)
    expect(matchesFilter(conds, 'or', get)).toBe(true)
  })

  it('结构不对的条件被丢掉（坏一条不该让整个筛选失效）', () => {
    expect(matchesFilter([{ id: 'x', field: '', operator: 'equals', value: 'a' }], 'and', get)).toBe(true)
    expect(matchesFilter([{ id: 'x', field: '业务类型', operator: 'bogus' as never }], 'and', get)).toBe(true)
  })

  it('拿不到 row.fields 的拼接列也能筛（取值由调用方渲染后给出）', () => {
    const rendered = () => renderTemplate('{客户 REF NO.} · {货物品名}', row)
    expect(matchesFilter([cond('客户点', 'contains', '模具')], 'and', rendered)).toBe(true)
    expect(matchesFilter([cond('客户点', 'contains', '钢材')], 'and', rendered)).toBe(false)
  })
})

describe('sanitizeConditions', () => {
  it('只留下字段非空、操作符合法、该带值时带了值的条件', () => {
    const raw: FilterCondition[] = [
      { id: '1', field: '业务类型', operator: 'equals', value: '空运' },
      { id: '2', field: '', operator: 'equals', value: '空运' },
      { id: '3', field: '货物品名', operator: 'bogus' as never },
      { id: '4', field: '备注', operator: 'empty' },
    ]

    expect(sanitizeConditions(raw).map((c) => c.id)).toEqual(['1', '4'])
  })
})

describe('配置读写（按 userId + role 分桶）', () => {
  beforeEach(() => {
    installLocalStorage()
  })

  const columns: ViewColumn[] = [
    { kind: 'field', name: '商机编号' },
    { kind: 'template', label: '客户点', expression: '{客户 REF NO.} · {货物品名}' },
  ]

  it('没配过时返回 null（调用方据此回落到默认列）', () => {
    expect(getViewConfig(1, 'wd_customer_service')).toBeNull()
  })

  it('存了能读回，拼接列两个字段都在', () => {
    saveViewConfig(1, 'wd_customer_service', { columns })
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.columns).toEqual(columns)
  })

  it('不同用户 / 不同工作台互不串台', () => {
    saveViewConfig(1, 'wd_customer_service', { columns })

    expect(getViewConfig(2, 'wd_customer_service')).toBeNull()
    expect(getViewConfig(1, 'wd_operator')).toBeNull()
  })

  it('清除后回到未配置状态', () => {
    saveViewConfig(1, 'wd_customer_service', { columns })
    clearViewConfig(1, 'wd_customer_service')

    expect(getViewConfig(1, 'wd_customer_service')).toBeNull()
  })

  it('坏数据（非法 JSON / 结构不对）不抛错，当作没配过', () => {
    localStorage.setItem('weida_view_config:1:wd_customer_service', '{不是 JSON')
    expect(getViewConfig(1, 'wd_customer_service')).toBeNull()

    localStorage.setItem('weida_view_config:1:wd_customer_service', JSON.stringify({ columns: '不是数组' }))
    expect(getViewConfig(1, 'wd_customer_service')).toBeNull()
  })

  it('结构不对的单列被丢弃，其余保留', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({ columns: [{ kind: 'field', name: '商机编号' }, { kind: '未知' }, null, 42] })
    )

    expect(getViewConfig(1, 'wd_customer_service')?.columns).toEqual([{ kind: 'field', name: '商机编号' }])
  })

  it('空列（blank）能存能读，缺名字的会被丢掉', () => {
    const cols: ViewColumn[] = [
      { kind: 'field', name: '商机编号' },
      { kind: 'blank', label: '我的批注', suffix: '张三_k7f2' },
    ]
    saveViewConfig(1, 'wd_customer_service', { columns: cols })
    expect(getViewConfig(1, 'wd_customer_service')?.columns).toEqual(cols)

    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({ columns: [{ kind: 'blank', label: '' }] })
    )
    expect(getViewConfig(1, 'wd_customer_service')?.columns).toEqual([])
  })

  /**
   * 老版本的空列没有独立的显示名，`name` 直接就是飞书字段名。升级不能把它读丢——
   * 读丢了用户那一列连同里面敲过的内容就从视图里消失了。
   */
  it('老版空列（只有 name）升级成「显示名 = 原字段名，无后缀」', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({ columns: [{ kind: 'blank', name: '我的批注' }] })
    )

    expect(getViewConfig(1, 'wd_customer_service')?.columns).toEqual([{ kind: 'blank', label: '我的批注', suffix: '' }])
  })

  it('命名视图与列配置存在同一桶里，互不覆盖', () => {
    const views: FilterView[] = [
      {
        id: 'v1',
        name: '我的待报价',
        conjunction: 'or',
        conditions: [{ id: 'c1', field: '业务类型', operator: 'equals', value: '空运' }],
      },
    ]
    saveViewConfig(1, 'wd_customer_service', { columns, views, activeViewId: 'v1' })
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.columns).toEqual(columns)
    expect(got?.views).toEqual(views)
    expect(got?.activeViewId).toBe('v1')
  })

  it('结构不对的视图被丢弃，列配置不受影响', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({
        columns,
        views: [{ id: 'ok', name: '能用', conjunction: 'and', conditions: [] }, { id: 'bad' }, 'x'],
      })
    )
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.columns).toEqual(columns)
    expect(got?.views?.map((v) => v.id)).toEqual(['ok'])
  })

  it('老配置（只有 columns、没有 views）照常读出来', () => {
    localStorage.setItem('weida_view_config:1:wd_customer_service', JSON.stringify({ columns }))
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.columns).toEqual(columns)
    expect(got?.views).toEqual([])
  })
})

describe('colId', () => {
  /**
   * 列标识必须是「稳定」的：行高列宽颜色全按它存，用下标的话列一重排，
   * 用户拖好的宽度和刷好的颜色就整体错位到别的列上。
   */
  it('字段列用字段名，空列用后缀', () => {
    expect(colId({ kind: 'field', name: '货物品名' })).toBe('货物品名')
    expect(colId({ kind: 'blank', label: '我的备注', suffix: '张三_k7f2' })).toBe('blank:张三_k7f2')
  })

  /**
   * 空列认后缀不认显示名：显示名随时可改，改了列宽和颜色不该跟着丢。
   */
  it('空列改了显示名，列标识不变', () => {
    const before = colId({ kind: 'blank', label: '我的备注', suffix: '张三_k7f2' })
    const after = colId({ kind: 'blank', label: '批注', suffix: '张三_k7f2' })
    expect(after).toBe(before)
  })

  /** 老版空列没有后缀，退回用显示名（也就是它当年的字段名）当标识。 */
  it('老版空列没有后缀时退回显示名', () => {
    expect(colId({ kind: 'blank', label: '我的备注', suffix: '' })).toBe('blank:我的备注')
  })

  it('拼接列没有字段名，用列名加前缀区分', () => {
    expect(colId({ kind: 'template', label: '货名+目的港', expression: '{货物品名} {目的 港}' })).toBe(
      'tpl:货名+目的港'
    )
    // 前缀是必需的：拼接列的列名若刚好等于某个真字段名，不加前缀两者的宽度会互相覆盖
    expect(colId({ kind: 'template', label: '货物品名', expression: 'x' })).not.toBe(
      colId({ kind: 'field', name: '货物品名' })
    )
  })
})

describe('空列命名：显示名与飞书字段名分开', () => {
  it('飞书字段名 = 显示名 + 后缀', () => {
    expect(blankFieldName({ label: '备注', suffix: '张三_k7f2' })).toBe('备注_张三_k7f2')
  })

  /** 老版空列没有后缀，字段名就是显示名本身（不能凭空补一个下划线出来）。 */
  it('没有后缀时字段名就是显示名', () => {
    expect(blankFieldName({ label: '备注', suffix: '' })).toBe('备注')
  })

  it('列头显示用户自己起的名字，而不是飞书字段名', () => {
    expect(columnTitle({ kind: 'blank', label: '备注', suffix: '张三_k7f2' })).toBe('备注')
    expect(columnTitle({ kind: 'template', label: '货名+港', expression: 'x' })).toBe('货名+港')
    expect(columnTitle({ kind: 'field', name: '货物品名' })).toBe('货物品名')
  })

  it('后缀带用户名与短码', () => {
    expect(makeBlankSuffix('张三')).toMatch(/^张三_[a-z2-9]{4}$/)
  })

  /**
   * 后缀靠下划线拆「用户名 / 短码」，用户名自带下划线或空格会把结构搅乱，
   * 所以一律换成横线。
   */
  it('用户名里的下划线与空白换成横线', () => {
    expect(makeBlankSuffix('张 三_甲')).toMatch(/^张-三-甲_[a-z2-9]{4}$/)
  })

  it('用户名为空时兜一个「用户」，不会拼出以 _ 开头的名字', () => {
    expect(makeBlankSuffix('   ')).toMatch(/^用户_[a-z2-9]{4}$/)
  })

  /** 随机是「不会撞名」的依据，这里只验它确实在变（否则每次都同一个后缀就没意义了）。 */
  it('短码随机', () => {
    const seen = new Set(Array.from({ length: 50 }, () => makeBlankSuffix('张三')))
    expect(seen.size).toBeGreaterThan(1)
  })

  it('能从飞书返回的字段名里切回后缀', () => {
    expect(suffixFromFieldName('备注_张三_k7f2', '备注')).toBe('张三_k7f2')
  })

  /** 飞书可能对字段名动手（去空白、截断），切不出来就当没有后缀，别切出半个名字。 */
  it('名字对不上时返回空后缀', () => {
    expect(suffixFromFieldName('备注', '备注')).toBe('')
    expect(suffixFromFieldName('别人的备注_李四_ab12', '备注')).toBe('')
  })

  it('改名时后缀不动，只换前半截', () => {
    const suffix = suffixFromFieldName('备注_张三_k7f2', '备注')
    expect(blankFieldName({ label: '批注', suffix })).toBe('批注_张三_k7f2')
  })

  /** 显示名里带下划线会和后缀混起来，建列前必须换成横线。 */
  it('显示名里的下划线换成横线后才拼字段名', () => {
    const label = '我的_备注'.replaceAll('_', '-')
    expect(blankFieldName({ label, suffix: '张三_k7f2' })).toBe('我的-备注_张三_k7f2')
  })
})

describe('clampSize', () => {
  it('夹进区间并取整', () => {
    expect(clampSize(120.6, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX)).toBe(121)
    expect(clampSize(5, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX)).toBe(ROW_HEIGHT_MIN)
    expect(clampSize(9999, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX)).toBe(ROW_HEIGHT_MAX)
    expect(clampSize(300, COLUMN_WIDTH_MIN, COLUMN_WIDTH_MAX)).toBe(300)
  })
})

describe('布局个性化（行高 / 列宽 / 颜色）的存取', () => {
  const columns: ViewColumn[] = defaultColumns(['货物品名', '目的 港'])
  const layout = {
    rowHeights: { rec1: 60, rec2: 80 },
    columnWidths: { 货物品名: 240 },
    cellColors: { [cellColorKey('rec1', '货物品名')]: '#ffe58f' },
    rowColors: { rec2: '#b7eb8f' },
    // 字段名里带空格（本表确实有「目的 港」这种），所以要用引号当键
    columnColors: { '目的 港': '#91d5ff' },
  }

  it('原样存回读', () => {
    saveViewConfig(1, 'wd_customer_service', { columns, ...layout })
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.rowHeights).toEqual(layout.rowHeights)
    expect(got?.columnWidths).toEqual(layout.columnWidths)
    expect(got?.cellColors).toEqual(layout.cellColors)
    expect(got?.rowColors).toEqual(layout.rowColors)
    expect(got?.columnColors).toEqual(layout.columnColors)
  })

  it('老配置没有这些字段时读成 undefined，不报错', () => {
    localStorage.setItem('weida_view_config:1:wd_customer_service', JSON.stringify({ columns }))
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.rowHeights).toBeUndefined()
    expect(got?.columnWidths).toBeUndefined()
    expect(got?.cellColors).toBeUndefined()
  })

  it('尺寸脏数据被夹回区间，非数字直接丢掉', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({
        columns,
        rowHeights: { 太小: 1, 太大: 9999, 正好: 50, 坏了: 'x', 也没有: null },
        columnWidths: { 正常: 200, 负数: -30 },
      })
    )
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.rowHeights).toEqual({ 太小: ROW_HEIGHT_MIN, 太大: ROW_HEIGHT_MAX, 正好: 50 })
    expect(got?.columnWidths).toEqual({ 正常: 200, 负数: COLUMN_WIDTH_MIN })
  })

  it('颜色表只留非空字符串，全空时整体读成 undefined', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({
        columns,
        rowColors: { 好: '#fff', 坏了: 123, 空的: '' },
        cellColors: { a: null },
      })
    )
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.rowColors).toEqual({ 好: '#fff' })
    // 一个都没剩下就当没配过，调用方只判一次空
    expect(got?.cellColors).toBeUndefined()
  })

  it('这些字段不是对象时忽略，不影响列配置', () => {
    localStorage.setItem(
      'weida_view_config:1:wd_customer_service',
      JSON.stringify({ columns, rowHeights: 'x', columnWidths: [] })
    )
    const got = getViewConfig(1, 'wd_customer_service')

    expect(got?.columns).toEqual(columns)
    expect(got?.rowHeights).toBeUndefined()
    expect(got?.columnWidths).toBeUndefined()
  })
})
