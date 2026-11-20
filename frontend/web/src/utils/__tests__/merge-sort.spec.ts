import { describe, expect, it } from 'vitest'
import { parsePickupTime } from '@/utils/pickup-time'
import {
  assignGroupPalette,
  buildMergeGroups,
  mergeBadgeLabel,
  sortByMergeGroup,
  type MergeGroupInfo,
} from '@/utils/merge-group'
import type { BitableRecord } from '@/api/module_feishu/workbench'

/**
 * 排序是本次改动的核心，而且**没法靠眼睛验**：合并组可能跨页、同一天有「有/无时间」两种
 * 形状、发票号还可能为空。这里用真实的飞书取值形状把规则钉死。
 */

function rec(recordId: string, fields: Record<string, unknown>, linkIds: Record<string, string[]> = {}): BitableRecord {
  return { record_id: recordId, fields, link_ids: linkIds, user_refs: {} }
}

/** 取排序后的发票号序列，断言用 */
function invoices(records: BitableRecord[]): string[] {
  return records.map((r) => String(r.fields['发票号'] ?? ''))
}

describe('parsePickupTime', () => {
  it('纯日期按当天 00:00 解析，与显式 00:00 得到同一时间戳', () => {
    // 这正是「同一天被劈成两块」的根因：两者按字符串比较不相等，按时间戳则相等
    expect(parsePickupTime('2026/03/26')).toBe(parsePickupTime('2026/03/26 00:00'))
  })

  it('解析出当天具体时刻', () => {
    expect(parsePickupTime('2026/03/26 08:30')).toBe(new Date(2026, 2, 26, 8, 30).getTime())
    expect(parsePickupTime('2026-03-26 08:30')).toBe(new Date(2026, 2, 26, 8, 30).getTime())
  })

  it('非零填零、全角冒号等写法都能解析', () => {
    expect(parsePickupTime('2026/3/6')).toBe(new Date(2026, 2, 6).getTime())
    expect(parsePickupTime('2026/03/26 8:00')).toBe(new Date(2026, 2, 26, 8, 0).getTime())
    expect(parsePickupTime('2026/03/26 08：00')).toBe(new Date(2026, 2, 26, 8, 0).getTime())
  })

  it('空值 / 非日期 / 非法月份一律返回 null（不猜、不让 Date 静默滚动到次年）', () => {
    expect(parsePickupTime('')).toBeNull()
    expect(parsePickupTime(null)).toBeNull()
    expect(parsePickupTime(undefined)).toBeNull()
    expect(parsePickupTime('等通知提货')).toBeNull()
    expect(parsePickupTime('2026/19')).toBeNull()
    expect(parsePickupTime('2026/13/01')).toBeNull()
  })
})

describe('sortByMergeGroup', () => {
  const MST = '合出主单'
  const HAWB = '合出分单'

  function build(records: BitableRecord[]): Map<string, MergeGroupInfo> {
    return buildMergeGroups(records, MST, HAWB)
  }

  it('默认倒序：提货时间从晚到早', () => {
    const records = [
      rec('a', { 发票号: '41001', 预计提货时间: '2026/01/06 00:00' }),
      rec('b', { 发票号: '41002', 预计提货时间: '2026/03/26 08:00' }),
      rec('c', { 发票号: '41003', 预计提货时间: '2026/02/10 00:00' }),
    ]
    expect(invoices(sortByMergeGroup(records, build(records), 'desc'))).toEqual(['41002', '41003', '41001'])
  })

  it('升序可切换', () => {
    const records = [
      rec('a', { 发票号: '41001', 预计提货时间: '2026/01/06 00:00' }),
      rec('b', { 发票号: '41002', 预计提货时间: '2026/03/26 08:00' }),
    ]
    expect(invoices(sortByMergeGroup(records, build(records), 'asc'))).toEqual(['41001', '41002'])
  })

  it('同一提货时间内按发票号由大到小', () => {
    const records = [
      rec('a', { 发票号: '4100157971', 预计提货时间: '2026/10/09 20:00' }),
      rec('b', { 发票号: '4100157986', 预计提货时间: '2026/10/09 20:00' }),
      rec('c', { 发票号: '4100157975', 预计提货时间: '2026/10/09 20:00' }),
    ]
    expect(invoices(sortByMergeGroup(records, build(records), 'desc'))).toEqual([
      '4100157986',
      '4100157975',
      '4100157971',
    ])
  })

  it('纯日期与带时间同一天混排，发票号贯穿整天（修复「劈成两块」）', () => {
    const records = [
      rec('a', { 发票号: '4100130296', 预计提货时间: '2026/03/26' }),
      rec('b', { 发票号: '4100130325', 预计提货时间: '2026/03/26 08:00' }),
      rec('c', { 发票号: '4100130094', 预计提货时间: '2026/03/26 00:00' }),
    ]
    // 按字符串排会把 a 单独甩到最前；按时间戳排则三者同为 03-26、纯按发票号降序
    expect(invoices(sortByMergeGroup(records, build(records), 'desc'))).toEqual([
      '4100130325',
      '4100130296',
      '4100130094',
    ])
  })

  it('合并组不拆散：整组按组内最大提货时间定位，组员相邻', () => {
    // b 和 d 是一组；飞书写入的是**含自身的全组 record_id 列表**
    const records = [
      rec('a', { 发票号: '41001', 预计提货时间: '2026/10/01 00:00' }),
      rec('b', { 发票号: '41002', 预计提货时间: '2026/09/01 00:00' }, { [MST]: ['b', 'd'] }),
      rec('c', { 发票号: '41003', 预计提货时间: '2026/08/01 00:00' }),
      rec('d', { 发票号: '41004', 预计提货时间: '2026/09/01 00:00' }, { [MST]: ['b', 'd'] }),
    ]

    const sorted = sortByMergeGroup(records, build(records), 'desc')
    const order = invoices(sorted)

    // b、d 必须相邻，占了第 1、2 位；a（10/01）在最前，c（08/01）被组的 09/01 挤到最后
    expect(Math.abs(order.indexOf('41002') - order.indexOf('41004'))).toBe(1)
    expect(order[0]).toBe('41001')
    expect(order[3]).toBe('41003')
  })

  it('没有提货时间的沉底，无论升序降序（排头部会被误读成最早）', () => {
    const records = [
      rec('a', { 发票号: '41001', 预计提货时间: '等通知提货' }),
      rec('b', { 发票号: '41002', 预计提货时间: '2026/03/26 00:00' }),
      rec('c', { 发票号: '41003' }),
    ]
    expect(invoices(sortByMergeGroup(records, build(records), 'desc'))[0]).toBe('41002')
    expect(invoices(sortByMergeGroup(records, build(records), 'asc'))[0]).toBe('41002')
  })

  it('排序稳定：同一份数据两次排序结果一致', () => {
    const records = [
      rec('a', { 发票号: '41001', 预计提货时间: '2026/03/26' }),
      rec('b', { 发票号: '41001', 预计提货时间: '2026/03/26' }),
      rec('c', { 发票号: '41001', 预计提货时间: '2026/03/26' }),
    ]
    const first = sortByMergeGroup(records, build(records), 'desc').map((r) => r.record_id)
    const second = sortByMergeGroup(records, build(records), 'desc').map((r) => r.record_id)
    expect(first).toEqual(second)
  })
})

describe('assignGroupPalette', () => {
  const MST = '合出主单'
  const HAWB = '合出分单'

  /** 造 N 个两组员合并组，提货时间递减，保证排序后组顺序就是 g0,g1,... */
  function manyGroups(count: number): BitableRecord[] {
    const out: BitableRecord[] = []
    for (let g = 0; g < count; g += 1) {
      const day = String(28 - g).padStart(2, '0')
      const ids = [`g${g}a`, `g${g}b`]
      for (const [i, id] of ids.entries()) {
        out.push(rec(id, { 发票号: `4100${g}${i}`, 预计提货时间: `2026/03/${day}` }, { [MST]: ids }))
      }
    }
    return out
  }

  it('颜色按列表显示顺序发，而非并查集发现顺序', () => {
    // b 组在数据里更靠前（先被发现），但排序后 a 组（09/01）排在前面
    const records = [
      rec('b1', { 发票号: '41002', 预计提货时间: '2026/01/01' }, { [MST]: ['b1', 'b2'] }),
      rec('b2', { 发票号: '41001', 预计提货时间: '2026/01/01' }, { [MST]: ['b1', 'b2'] }),
      rec('a1', { 发票号: '41004', 预计提货时间: '2026/09/01' }, { [MST]: ['a1', 'a2'] }),
      rec('a2', { 发票号: '41003', 预计提货时间: '2026/09/01' }, { [MST]: ['a1', 'a2'] }),
    ]
    const raw = buildMergeGroups(records, MST, HAWB)
    const colored = assignGroupPalette(sortByMergeGroup(records, raw, 'desc'), raw)

    expect(colored.get('a1')!.paletteIndex).toBe(0)
    expect(colored.get('a2')!.paletteIndex).toBe(0)
    expect(colored.get('b1')!.paletteIndex).toBe(1)
    expect(colored.get('b2')!.paletteIndex).toBe(1)
    // 同组拿到的是同一个对象，因此必然同色
    expect(colored.get('a1')).toBe(colored.get('a2'))
    expect(colored.get('a1')!.rowClass).not.toBe(colored.get('b1')!.rowClass)
  })

  it('相邻显示的两个组不撞色（组数超过调色板长度也成立）', () => {
    const records = manyGroups(9)
    const raw = buildMergeGroups(records, MST, HAWB)
    const colored = assignGroupPalette(sortByMergeGroup(records, raw, 'desc'), raw)

    const order: MergeGroupInfo[] = []
    for (const r of sortByMergeGroup(records, raw, 'desc')) {
      const info = colored.get(r.record_id)
      if (info && order[order.length - 1] !== info) order.push(info)
    }
    expect(order.length).toBe(9)
    for (let i = 1; i < order.length; i += 1) {
      expect(order[i]!.rowClass).not.toBe(order[i - 1]!.rowClass)
    }
  })

  it('不修改传入的 groupMap', () => {
    const records = manyGroups(2)
    const raw = buildMergeGroups(records, MST, HAWB)
    const before = raw.get('g0a')!.paletteIndex
    assignGroupPalette(sortByMergeGroup(records, raw, 'desc'), raw)
    expect(raw.get('g0a')!.paletteIndex).toBe(before)
  })
})

describe('mergeBadgeLabel', () => {
  it('显示完整发票号——截断会让不同组同名', () => {
    // 线上 4100110320 与 4100110318 是两个组，都截成 41001103
    const a = { repInvoiceNo: '4100110320', memberIds: ['1', '2'] } as MergeGroupInfo
    const b = { repInvoiceNo: '4100110318', memberIds: ['1', '2'] } as MergeGroupInfo
    expect(mergeBadgeLabel(a)).toBe('组 · 4100110320（2票）')
    expect(mergeBadgeLabel(a)).not.toBe(mergeBadgeLabel(b))
  })

  it('带上票数——代表票不变时，组合变化也要看得出来', () => {
    // 把代表票和别的组重组：发票号不变，票数变了，徽标必须跟着变
    const three = { repInvoiceNo: '4100110320', memberIds: ['a', 'b', 'c'] } as MergeGroupInfo
    const two = { repInvoiceNo: '4100110320', memberIds: ['a', 'd'] } as MergeGroupInfo
    expect(mergeBadgeLabel(two)).not.toBe(mergeBadgeLabel(three))
  })

  it('没有代表票时退回合并类型名', () => {
    expect(mergeBadgeLabel({ repInvoiceNo: '', mergeType: 'hawb' } as MergeGroupInfo)).toBe('分单合并')
    expect(mergeBadgeLabel({ repInvoiceNo: '', mergeType: 'master' } as MergeGroupInfo)).toBe('主单合并')
  })
})
