/**
 * 离线外壳 Service Worker。
 *
 * 设计只有一条主线：**入口永不从缓存直接给，带 hash 的产物才永久缓存，接口一律不碰。**
 * 理由是这套前端有个很具体的坑——`pnpm build` 之后 `dist/` 是**整个被替换**的，旧 chunk
 * 随之下线。如果 SW 把 `index.html` 也缓存住并发给用户，那个旧壳引用的 chunk 已经 404，
 * 页面白屏，而且**刷新也救不回来**（SW 还在发旧壳）。所以导航请求走 network-first：
 * 在线时永远拿服务端最新的入口，缓存只在断网时兜底。
 *
 * 三条分支：
 *   1. `/api/*`、非 GET、跨域        → 完全不拦截（业务数据与认证绝不能进 SW 缓存）
 *   2. 导航请求（打开/刷新页面）      → network-first，失败才回退缓存的壳
 *   3. 带内容 hash 的静态产物        → cache-first（URL 里有 hash，内容永不改）
 *
 * ⚠️ 紧急刹车（万一这个 SW 出问题，把同事卡住了）：
 *   用下面这份内容覆盖 `public/sw.js` 后重新构建部署即可，老用户下次导航就会自动卸载它。
 *   `skipWaiting` + `clients.claim` 保证不用等两个版本周期。
 *
 *     self.addEventListener('install', () => self.skipWaiting())
 *     self.addEventListener('activate', (e) => e.waitUntil((async () => {
 *       await Promise.all((await caches.keys()).map((k) => caches.delete(k)))
 *       await self.registration.unregister()
 *       await self.clients.claim()
 *     })()))
 */

// 改了缓存策略就 bump 这个版本号：activate 里会清掉所有旧名字的缓存。
const VERSION = 'v1'

const SHELL_CACHE = `fa-shell-${VERSION}`
const ASSET_CACHE = `fa-assets-${VERSION}`

/** 壳的规范地址。所有导航都缓存到这一个键下，避免 `?a=1`、`#/x` 各存一份。 */
const SHELL_URL = '/web/'

/** 只对这些目录做 cache-first——里面的文件名都带内容 hash。 */
const ASSET_PREFIXES = ['/web/js/', '/web/css/', '/web/img/', '/web/webp/']

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      try {
        // cache: 'reload' 绕开 HTTP 缓存，确保存下的是服务端此刻的壳
        const response = await fetch(new Request(SHELL_URL, { cache: 'reload' }))
        if (response && response.ok) {
          await (await caches.open(SHELL_CACHE)).put(SHELL_URL, response)
        }
      } catch (error) {
        // 装壳失败不该让 SW 装不上：装上了照样能 network-first 正常工作，
        // 只是没得离线兜底。这里吞掉异常并留着日志。
        console.warn('[SW] 预缓存外壳失败，离线兜底不可用：', error)
      }
      await self.skipWaiting()
    })(),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // 清掉旧版本的缓存，否则每发一版就在用户磁盘上留一份 2MB
      const alive = new Set([SHELL_CACHE, ASSET_CACHE])
      const names = await caches.keys()
      await Promise.all(names.filter((name) => !alive.has(name)).map((name) => caches.delete(name)))
      await self.clients.claim()
    })(),
  )
})

/** 缓存并返回。只有 `ok` 的响应才存——别把 404/500 缓存起来变成永久故障。 */
async function putIfOk(cacheName, request, response) {
  if (response && response.ok) {
    const cache = await caches.open(cacheName)
    await cache.put(request, response.clone())
  }
  return response
}

self.addEventListener('fetch', (event) => {
  const { request } = event

  // 分支 1：只碰同源的 GET。接口（含 401/续期）与任何写操作一律放行，
  // 让它们照常走网络与 HTTP 缓存——业务数据进 SW 缓存会造成「看到的是旧数据」这种
  // 最难查的问题，而认证请求被缓存则直接是安全问题。
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return

  // 分支 2：导航（打开页面、刷新、地址栏直达）→ network-first。
  // 这是「以后发版不卡住同事」的落点：在线时永远拿最新的 index.html。
  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request)
          if (fresh && fresh.ok) {
            await (await caches.open(SHELL_CACHE)).put(SHELL_URL, fresh.clone())
          }
          return fresh
        } catch (error) {
          const cached = await caches.match(SHELL_URL)
          if (cached) return cached
          throw error
        }
      })(),
    )
    return
  }

  // 分支 3：带 hash 的静态产物 → cache-first。
  if (ASSET_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(request)
        if (cached) return cached
        return putIfOk(ASSET_CACHE, request, await fetch(request))
      })(),
    )
  }

  // 其余（manifest、图标等）：不拦截，交给网络与 HTTP 缓存
})
