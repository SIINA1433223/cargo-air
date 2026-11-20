/**
 * 状态徽章派生。
 *
 * 飞书表里**没有**「状态」字段——状态是由勾选框反推的派生值（沿用旧系统的做法：
 * 勾选框勾上即代表该环节已完成）。所以这里不做任何存储写入，纯粹是展示层推导。
 */

export type BadgeType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

export interface BadgeConfig {
  label: string
  type: BadgeType
}

/** 勾选框字段名（与后端 workbench.py 的常量一致） */
export const CHECKBOX = {
  BOOKING: '订舱单',
  PLAN: '计划',
  CUSTOMS_PLAN: '报关计划',
  FORECAST: '预报',
  INSURANCE: '投保',
} as const

function isChecked(fields: Record<string, unknown>, name: string): boolean {
  return fields[name] === true
}

/** 订舱状态：勾了「订舱单」即已订舱，否则待订舱 */
export function bookingStatusBadge(fields: Record<string, unknown>): BadgeConfig {
  return isChecked(fields, CHECKBOX.BOOKING)
    ? { label: '已订舱', type: 'success' }
    : { label: '待订舱', type: 'warning' }
}

/** 计划状态：勾了「计划」即已下发，否则待下发 */
export function planStatusBadge(fields: Record<string, unknown>): BadgeConfig {
  return isChecked(fields, CHECKBOX.PLAN) ? { label: '已计划', type: 'success' } : { label: '待下发', type: 'warning' }
}

/** 报关状态：勾了「报关计划」即已安排报关 */
export function customsStatusBadge(fields: Record<string, unknown>): BadgeConfig {
  return isChecked(fields, CHECKBOX.CUSTOMS_PLAN)
    ? { label: '已安排', type: 'success' }
    : { label: '待安排', type: 'warning' }
}

/** 勾选框 → 「已勾选/未勾选」的通用徽章（用于操作工作台的各待办项） */
export function checkboxBadge(fields: Record<string, unknown>, name: string, doneLabel: string): BadgeConfig {
  return isChecked(fields, name) ? { label: doneLabel, type: 'success' } : { label: '待处理', type: 'warning' }
}

/**
 * 提货状态：以「实际离厂时间（车队回填）」是否有值为准（有值 = 已提货）。
 *
 * 该字段是飞书 lookup 列（由车队回填后汇总过来），本系统只读；旧系统
 * CsBaseInfoCard 的提货徽章也是看同一个来源。
 */
export const PICKUP_ACTUAL_FIELD = '实际离厂时间（车队回填）'

export function pickupStatusBadge(fields: Record<string, unknown>): BadgeConfig {
  const value = fields[PICKUP_ACTUAL_FIELD]
  const picked = value !== null && value !== undefined && value !== ''
  return picked ? { label: '已提货', type: 'success' } : { label: '待提货', type: 'info' }
}

/**
 * 客服「单票状态」→ 徽章。
 *
 * 与上面几个**不同**：那几个是前端拿本表字段现推的，这一个推不了——它要读商机挂着的那张
 * 订单表里的两个字段，而列表接口只给关联的 record_id、不给对方的字段值。所以状态由后端
 * 算好（`service._single_status`）随记录下发，这里只负责上色。
 *
 * 颜色按「越靠后越暖」排：刚建的单子是中性灰蓝，越接近收尾越显眼，`已完成`回落到绿色——
 * 一列扫下来，需要有人动的那些自己会跳出来。
 */
const SINGLE_STATUS_TONE: Record<string, BadgeType> = {
  待航线报价: 'info',
  待确认拿到货: 'primary',
  待核价: 'warning',
  待交接操作: 'danger',
  已完成: 'success',
}

export function singleStatusBadge(status: string | undefined): BadgeConfig | null {
  if (!status) return null
  // 后端加了新档而前端还没跟上时，用默认色显示原文，不要吞掉——状态是用来催人的，
  // 空白格比一个颜色不对的标签糟糕得多
  return { label: status, type: SINGLE_STATUS_TONE[status] ?? 'info' }
}
