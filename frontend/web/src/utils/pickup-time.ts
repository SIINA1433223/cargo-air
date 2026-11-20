/**
 * 「预计提货时间」排序键。
 *
 * 该列在飞书表里是**文本列**（不是日期列），值有两种形状：`2026/03/26`（当天时刻未定）
 * 与 `2026/03/26 08:00`。这里把纯日期按当天 00:00 解析，于是同一天的两种形状拿到**相同
 * 时间戳**、能正确混排——这正是飞书服务端按字符串排序做不到的地方（`2026/03/26` 与
 * `2026/03/26 00:00` 是两个不同字符串，同一天会被劈成两块）。
 *
 * 解析不出来的（空值、`2026/19` 这类非法值）一律返回 null，排序时沉到列表末尾。
 */

const PICKUP_TIME_RE = /^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2})[:：](\d{2}))?/

/**
 * 把「预计提货时间」的文本值解析成时间戳（毫秒）。
 *
 * @param value 飞书字段原值（字符串；非字符串会被 String() 后解析）
 * @returns 时间戳；无法解析时返回 null
 */
export function parsePickupTime(value: unknown): number | null {
  if (value === null || value === undefined) return null
  const matched = PICKUP_TIME_RE.exec(String(value).trim())
  if (!matched) return null

  const year = Number(matched[1])
  const month = Number(matched[2])
  const day = Number(matched[3])
  // `2026/19` 这种月=19 的畸形值过不了范围校验，返回 null 而不是被 Date 静默滚动到次年
  if (month < 1 || month > 12 || day < 1 || day > 31) return null

  const hour = matched[4] ? Number(matched[4]) : 0
  const minute = matched[5] ? Number(matched[5]) : 0
  if (hour > 23 || minute > 59) return null

  return new Date(year, month - 1, day, hour, minute).getTime()
}

/**
 * 提货时间比较：两个 null 视为相等，null **永远排在最后**（无论升序还是降序）。
 *
 * 「没有提货时间的票沉底」是有意的——排在头部会让人以为它们最早，实际是信息缺失。
 */
export function comparePickupTime(a: number | null, b: number | null, desc: boolean): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return desc ? b - a : a - b
}
