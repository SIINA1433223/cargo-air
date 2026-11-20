/**
 * 合并组计算。
 *
 * 飞书侧「合出主单」/「合出分单」是单向关联字段，写入的是**全组成员的 record_id
 * 数组（含自身）**；同一组的票通过这些 id 互相引用。前端用并查集算出完整分组，
 * 界面上永远显示发票号，不显示 rec 技术 ID。
 *
 * 注意：组内成员可能不在当前这一页（游标分页一次只取 50 条），所以「是否成组」
 * 以关联 id 的个数判定，成员发票号只在本页内解析。
 */

import type { BitableRecord } from '@/api/module_feishu/workbench'
import type { BadgeType } from './status-badge'
import { comparePickupTime, parsePickupTime } from './pickup-time'

export interface MergeGroupInfo {
  /** 并查集根节点（组内任取一个 record_id，仅用于区分不同组） */
  groupId: string
  mergeType: 'master' | 'hawb' | 'mixed'
  /** 全组成员 record_id（含不在本页的） */
  memberIds: string[]
  /** 代表票的发票号（本页能解析到时才有值） */
  repInvoiceNo: string
  /** 本页内可见的成员发票号 */
  visibleInvoiceNos: string[]
  paletteIndex: number
  tagType: BadgeType
  rowClass: string
}

/** 一组一色：浅背景 + 对应 Tag 颜色成对分配 */
const PALETTES: Array<{ tagType: BadgeType; rowClass: string }> = [
  { tagType: 'success', rowClass: 'merge-row-0' },
  { tagType: 'primary', rowClass: 'merge-row-1' },
  { tagType: 'warning', rowClass: 'merge-row-2' },
  { tagType: 'danger', rowClass: 'merge-row-3' },
  { tagType: 'info', rowClass: 'merge-row-4' },
  { tagType: 'success', rowClass: 'merge-row-5' },
  { tagType: 'primary', rowClass: 'merge-row-6' },
  { tagType: 'warning', rowClass: 'merge-row-7' },
]

class UnionFind {
  private parent = new Map<string, string>()

  find(x: string): string {
    if (!this.parent.has(x)) {
      this.parent.set(x, x)
      return x
    }
    let root = x
    while (this.parent.get(root) !== root) root = this.parent.get(root) as string
    // 路径压缩
    while (this.parent.get(x) !== root) {
      const next = this.parent.get(x) as string
      this.parent.set(x, root)
      x = next
    }
    return root
  }

  union(a: string, b: string): void {
    const ra = this.find(a)
    const rb = this.find(b)
    if (ra !== rb) this.parent.set(ra, rb)
  }

  keys(): string[] {
    return [...this.parent.keys()]
  }
}

/**
 * 计算合并组。
 *
 * @param records 当前页记录
 * @param masterField 主单合并关联字段名（如「合出主单」）
 * @param hawbField 分单合并关联字段名（如「合出分单」）
 */
export function buildMergeGroups(
  records: BitableRecord[],
  masterField: string,
  hawbField: string
): Map<string, MergeGroupInfo> {
  const uf = new UnionFind()
  const linked = new Set<string>()
  const hasMaster = new Set<string>()
  const hasHawb = new Set<string>()

  for (const rec of records) {
    const id = rec.record_id
    uf.find(id)
    const masterIds = rec.link_ids[masterField] ?? []
    const hawbIds = rec.link_ids[hawbField] ?? []
    if (masterIds.length) hasMaster.add(id)
    if (hawbIds.length) hasHawb.add(id)
    for (const peer of [...masterIds, ...hawbIds]) {
      if (!peer || peer === id) continue
      linked.add(id)
      linked.add(peer)
      uf.union(id, peer)
    }
  }

  // 分组：只保留真正的组（成员 ≥ 2）
  const raw = new Map<string, string[]>()
  for (const id of uf.keys()) {
    const root = uf.find(id)
    const list = raw.get(root)
    if (list) list.push(id)
    else raw.set(root, [id])
  }

  const invoiceOf = new Map(records.map((r) => [r.record_id, String(r.fields['发票号'] ?? '')]))

  const result = new Map<string, MergeGroupInfo>()
  let palette = 0
  for (const [root, memberIds] of raw) {
    if (memberIds.length < 2) continue
    const visible = memberIds.filter((id) => invoiceOf.has(id))
    const visibleInvoiceNos = visible.map((id) => invoiceOf.get(id) as string).filter(Boolean)
    // 合并类型：只要任一组员挂了主单关联就算主单组；两种都有记为 mixed
    const isMaster = memberIds.some((id) => hasMaster.has(id))
    const isHawb = memberIds.some((id) => hasHawb.has(id))
    const mergeType = isMaster && isHawb ? 'mixed' : isMaster ? 'master' : 'hawb'
    // 代表票：本页可见成员里发票号最小的（与旧系统「按发票号排序选主」一致）
    const repInvoiceNo = [...visibleInvoiceNos].sort((a, b) => a.localeCompare(b))[0] ?? ''
    const p = PALETTES[palette % PALETTES.length]
    palette += 1
    const info: MergeGroupInfo = {
      groupId: root,
      mergeType,
      memberIds,
      repInvoiceNo,
      visibleInvoiceNos,
      paletteIndex: palette - 1,
      tagType: p.tagType,
      rowClass: p.rowClass,
    }
    for (const id of memberIds) result.set(id, info)
  }
  return result
}

/**
 * 合并徽标文案：「组 · 代表票发票号（N 票）」。
 *
 * 必须显示**完整**发票号：线上 259 个组里有 184 个组的发票号前 8 位与别的组相同
 * （如 `4100110320` 与 `4100110318` 都截成 `41001103`），只截前 8 位会让两个不同的组
 * 显示成同一个名字 —— 用户看到的就是「写着同一个组，颜色却不一样」。
 *
 * 票数必须一起显示：只写发票号的话，把**代表票**（组内发票号最小那票）拿去和别的组
 * 重组合并，新组的代表票还是它，徽标一个字符都不变 —— 用户明明改了组合，界面上却
 * 看不出任何变化（拿真实数据模拟过，5 组里 5 组如此）。带上票数，成员一变就看得出来。
 */
export function mergeBadgeLabel(info: MergeGroupInfo): string {
  if (info.repInvoiceNo) return `组 · ${info.repInvoiceNo}（${info.memberIds.length}票）`
  return info.mergeType === 'hawb' ? '分单合并' : '主单合并'
}

/** 合并徽标的悬浮说明：列出本页可见的成员与总数 */
export function mergeBadgeTooltip(info: MergeGroupInfo): string {
  const typeLabel = info.mergeType === 'hawb' ? '分单组' : info.mergeType === 'master' ? '主单组' : '主分单组'
  const shown = info.visibleInvoiceNos.length ? info.visibleInvoiceNos.join('、') : '（其他成员不在本页）'
  return `${typeLabel} · 共 ${info.memberIds.length} 票\n本页成员：${shown}`
}

// ============================ 合并组排序 / 分组 ============================

/** 提货时间字段名（排序键来源） */
export const PICKUP_TIME_FIELD = '预计提货时间'

export type SortDirection = 'asc' | 'desc'

/** 合并组视图的一块：一个有组员的合并组，或全部未合并票（group 为 null） */
export interface MergeBlock {
  group: MergeGroupInfo | null
  items: BitableRecord[]
}

/**
 * 发票号降序。
 *
 * 用字符串比较而非数值：发票号里有 `TE2026052202`、`260819` 这类带字母/不等长的号，
 * 数值解析会把它们丢掉；这也与飞书服务端 `("发票号", True)` 的文本排序语义保持一致。
 */
function compareInvoiceDesc(a: unknown, b: unknown): number {
  const sa = a === null || a === undefined ? '' : String(a)
  const sb = b === null || b === undefined ? '' : String(b)
  if (sa === sb) return 0
  return sa < sb ? 1 : -1
}

/** 一条记录的发票号文本（缺失记空串，空串在降序里自然垫底） */
function invoiceOf(rec: BitableRecord): string {
  const value = rec.fields['发票号']
  return value === null || value === undefined ? '' : String(value)
}

/**
 * 按「提货时间 + 发票号」排序，**合并不拆散**。
 *
 * 关键在组键：整组取组内**最大的**提货时间作为排序键，于是组员无论各自时间如何，
 * 都会被放在同一个位置、彼此相邻。这是飞书服务端排序做不到的——它不知道「合并组」
 * 是什么（组合并是从关联字段现算的），也无法让组员相邻。
 *
 * 这也决定了必须先全量拉取再本地排序：组员可能跨游标页，只排当前页会把组劈开。
 *
 * 实现上**先分块再排块**，而不是直接用比较函数排平铺数组：后者看起来更短，但组键相同时
 * 若用「发票号」作次键，组员之间会得到不同的次键值，同键的**未合并票就能插进组员中间**，
 * 把组合劈开（线上 259 个组里曾劈开 68 个）。分块后「整组同进同出」是构造性的，
 * 不依赖比较函数的巧合。组间同键时用**组内最大发票号**当代表值，兼顾「发票号由大到小」。
 *
 * @param records 全量记录
 * @param groupMap `buildMergeGroups` 的结果
 * @param direction `desc` 为提货时间从晚到早（本系统默认）
 */
export function sortByMergeGroup(
  records: BitableRecord[],
  groupMap: Map<string, MergeGroupInfo>,
  direction: SortDirection
): BitableRecord[] {
  const desc = direction === 'desc'
  const groupTimeKeys = new Map<string, number | null>()
  const groupInvoiceKeys = new Map<string, string>()
  for (const rec of records) {
    const info = groupMap.get(rec.record_id)
    if (!info) continue
    const time = parsePickupTime(rec.fields[PICKUP_TIME_FIELD])
    const prevTime = groupTimeKeys.get(info.groupId)
    if (time !== null && (prevTime === undefined || prevTime === null || time > prevTime)) {
      groupTimeKeys.set(info.groupId, time)
    }
    const invoice = invoiceOf(rec)
    const prevInvoice = groupInvoiceKeys.get(info.groupId)
    if (prevInvoice === undefined || invoice > prevInvoice) groupInvoiceKeys.set(info.groupId, invoice)
  }

  // 分块：一个合并组一块，未合并的每条自成一块
  const buckets = new Map<string, BitableRecord[]>()
  for (const rec of records) {
    const info = groupMap.get(rec.record_id)
    const blockId = info ? info.groupId : `#${rec.record_id}`
    const list = buckets.get(blockId)
    if (list) list.push(rec)
    else buckets.set(blockId, [rec])
  }

  const blocks = [...buckets].map(([blockId, items]) => {
    const head = items[0] as BitableRecord
    const info = groupMap.get(head.record_id) ?? null
    return {
      blockId,
      items,
      info,
      timeKey: info ? (groupTimeKeys.get(info.groupId) ?? null) : parsePickupTime(head.fields[PICKUP_TIME_FIELD]),
      invoiceKey: info ? (groupInvoiceKeys.get(info.groupId) ?? '') : invoiceOf(head),
    }
  })

  blocks.sort((a, b) => {
    const byTime = comparePickupTime(a.timeKey, b.timeKey, desc)
    if (byTime !== 0) return byTime
    // 同键时未合并的排前（与原威达系统一致）：只按「是否成组」区分，组员之间不产生分支
    if (!!a.info !== !!b.info) return a.info ? 1 : -1
    const byInvoice = compareInvoiceDesc(a.invoiceKey, b.invoiceKey)
    if (byInvoice !== 0) return byInvoice
    return a.blockId < b.blockId ? -1 : a.blockId > b.blockId ? 1 : 0
  })

  const sorted: BitableRecord[] = []
  for (const block of blocks) {
    // 块内再按各自提货时间 → 发票号（这层差异不会影响块与块之间，故不破坏相邻性）
    const inner = [...block.items].sort((a, b) => {
      const byTime = comparePickupTime(
        parsePickupTime(a.fields[PICKUP_TIME_FIELD]),
        parsePickupTime(b.fields[PICKUP_TIME_FIELD]),
        desc
      )
      if (byTime !== 0) return byTime
      const byInvoice = compareInvoiceDesc(a.fields['发票号'], b.fields['发票号'])
      if (byInvoice !== 0) return byInvoice
      return a.record_id < b.record_id ? -1 : a.record_id > b.record_id ? 1 : 0
    })
    sorted.push(...inner)
  }
  return sorted
}

/**
 * 按**列表显示顺序**给各组合并组重新发色。
 *
 * `buildMergeGroups` 的调色板是按并查集的发现顺序发的，而发现顺序只取决于数据源顺序，
 * 与记录在屏幕上的先后无关 —— 于是视觉上相邻的两个组可能撞成同一个颜色，看起来就像
 * 「没有按组上色」。按显示顺序循环发色后，**同色的两个组至少相隔 8 个组**，一眼就能分开。
 *
 * 传入的是**排序后的全量**记录（不是筛选后的），所以切换筛选/翻页时颜色不会跳。
 * 返回新的 Map，不修改 `groupMap` 里的对象。
 *
 * @param ordered 已排序的全量记录
 * @param groupMap `buildMergeGroups` 的结果
 */
export function assignGroupPalette(
  ordered: BitableRecord[],
  groupMap: Map<string, MergeGroupInfo>
): Map<string, MergeGroupInfo> {
  const result = new Map<string, MergeGroupInfo>()
  const assigned = new Map<string, MergeGroupInfo>()
  let index = 0
  for (const rec of ordered) {
    const info = groupMap.get(rec.record_id)
    if (!info) continue
    let next = assigned.get(info.groupId)
    if (!next) {
      const palette = PALETTES[index % PALETTES.length] as { tagType: BadgeType; rowClass: string }
      // 同一组的多条记录共用同一个 info 对象，因此必然同色
      next = { ...info, paletteIndex: index, tagType: palette.tagType, rowClass: palette.rowClass }
      assigned.set(info.groupId, next)
      index += 1
    }
    result.set(rec.record_id, next)
  }
  return result
}

/**
 * 把记录归成「合并组各一块 + 未合并一块」。
 *
 * 不重新排序：`records` 传进来时已经排好，块之间的先后沿用首次出现的位置，
 * 所以组在列表中的位置与列表视图完全一致。
 */
export function buildMergeBlocks(records: BitableRecord[], groupMap: Map<string, MergeGroupInfo>): MergeBlock[] {
  const buckets = new Map<string, BitableRecord[]>()
  const groups = new Map<string, MergeGroupInfo>()
  const unmerged: BitableRecord[] = []

  for (const rec of records) {
    const info = groupMap.get(rec.record_id)
    if (!info) {
      unmerged.push(rec)
      continue
    }
    groups.set(info.groupId, info)
    const list = buckets.get(info.groupId)
    if (list) list.push(rec)
    else buckets.set(info.groupId, [rec])
  }

  const blocks: MergeBlock[] = [...buckets].map(([groupId, items]) => ({
    group: groups.get(groupId) ?? null,
    items,
  }))
  if (unmerged.length) blocks.push({ group: null, items: unmerged })
  return blocks
}
