import FeishuWorkbenchAPI, { type UserOption } from '@/api/module_feishu/workbench'

/**
 * 人员候选（飞书通讯录）的进程内缓存。
 *
 * 为什么要缓存：后台一个页面上可能同时有好几个人员字段——新建表单里两三个、详情抽屉的
 * 每个分组、索引页点开的那个格子的编辑器——每个都自己发一次请求就是同一份通讯录拉好几遍。
 * 后端本来就缓存了 10 分钟，这里再兜一层，省掉的是浏览器到服务端那一跳。
 *
 * 缓存的是 **Promise 而不是结果**：几个组件在同一帧里挂载时，第一次请求还没回来，
 * 存 Promise 能让后来者直接复用同一个在途请求，而不是各自再发一个。
 */
const cache = new Map<string, Promise<UserOption[]>>()

/** 取某个工作台的人员候选（命中缓存则不发请求）。 */
export function loadUserOptions(role: string): Promise<UserOption[]> {
  const hit = cache.get(role)
  if (hit) return hit
  const pending = FeishuWorkbenchAPI.getUserOptions(role)
    .then((res) => res.data.data)
    .catch(() => {
      // 失败不进缓存：否则一次网络抖动会让人员下拉在整个会话里永远是空的
      cache.delete(role)
      return [] as UserOption[]
    })
  cache.set(role, pending)
  return pending
}

/** 丢掉缓存（不传 role 即全部）——通讯录变动后想让下拉立刻刷新时用。 */
export function invalidateUserOptions(role?: string): void {
  if (role === undefined) cache.clear()
  else cache.delete(role)
}
