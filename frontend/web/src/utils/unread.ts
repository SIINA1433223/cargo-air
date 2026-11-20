/**
 * 未读状态管理（localStorage 持久化，按角色分桶）。
 *
 * 沿用原版威达系统的存储键 `wd_read_orders`，这样从旧系统切过来的用户不会丢掉已读记录。
 * 已读只存在浏览器本地——飞书表里没有这个字段，也不需要为它写一次飞书请求。
 */

const STORAGE_KEY = 'wd_read_orders'

type ReadOrdersStore = Record<string, string[]>

function readStore(): ReadOrdersStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as ReadOrdersStore
    return {}
  } catch {
    // JSON 损坏或 localStorage 被禁用：当作没有任何已读记录，不影响主流程
    return {}
  }
}

function writeStore(store: ReadOrdersStore): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    // 隐私模式/配额满时静默失败：未读标记丢了不影响业务
  }
}

/** 某角色已读记录的 record_id 集合 */
export function getReadOrderIds(role: string): Set<string> {
  const ids = readStore()[role]
  return new Set(Array.isArray(ids) ? ids : [])
}

/** 标记某条记录为已读（已读则不动，避免重复写 localStorage） */
export function markOrderRead(role: string, recordId: string): void {
  if (!recordId) return
  const store = readStore()
  const ids = store[role] ?? []
  if (ids.includes(recordId)) return
  ids.push(recordId)
  store[role] = ids
  writeStore(store)
}

/** 某条记录是否已读 */
export function isOrderRead(role: string, recordId: string): boolean {
  return getReadOrderIds(role).has(recordId)
}
