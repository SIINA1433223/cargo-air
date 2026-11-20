/**
 * `public/sw.js` 的三条分流规则。
 *
 * 这个文件不在 `src/` 下、也不进 TypeScript 编译，所以它是**唯一没有类型与 lint 兜底**
 * 的一块代码——而它一旦分流错，后果是「同事白屏」或「看到旧数据」这种最难查的故障。
 * 用 vm 把它跑在假的 SW 环境里，把三条规则各自钉住：
 *
 *   1. `/api/*`、非 GET、跨域  → **绝不拦截**（业务数据进缓存 = 看到旧数据；认证请求进缓存 = 安全）
 *   2. 导航                     → **network-first**（在线必须拿最新入口，否则发版后白屏）
 *   3. 带 hash 的产物           → cache-first（URL 含内容 hash，可以放心用缓存）
 */

import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { describe, expect, it, vi } from 'vitest'

const SW_PATH = path.resolve(__dirname, '../../../public/sw.js')
const ORIGIN = 'http://demo.local'
const SHELL_URL = '/web/'

type Handler = (event: { request: FakeRequest; respondWith: (r: unknown) => void }) => void

interface FakeRequest {
  url: string
  method: string
  mode?: string
}

/** 假响应。`tag` 只用来断言「拿到的是哪一份」，真实 Response 没有这个字段。 */
interface FakeResponse {
  ok: boolean
  status: number
  tag: string
  clone: () => FakeResponse
}

function fakeResponse(tag: string, ok = true, status = 200): FakeResponse {
  return { ok, status, tag, clone: () => fakeResponse(tag, ok, status) }
}

/** 载入真实的 sw.js，返回它的 fetch 处理函数与可注入的假缓存/假网络。 */
function load() {
  const handlers: Record<string, Handler> = {}
  const cacheStore = new Map<string, FakeResponse>()
  const cacheNames = new Set<string>()

  // 真实 Cache API 的键既可能是 URL 字符串（壳）也可能是 Request（静态产物），
  // 统一按 URL 归一——不然 `String(request)` 会变成 '[object Object]'，缓存永远命不中。
  const keyOf = (key: unknown): string =>
    typeof key === 'string' ? key : String((key as { url?: string })?.url ?? key)

  const makeCache = () => ({
    put: async (key: unknown, value: FakeResponse) => {
      cacheStore.set(keyOf(key), value)
    },
    match: async (key: unknown) => cacheStore.get(keyOf(key)),
  })

  const fetchMock = vi.fn(async (_input?: unknown): Promise<FakeResponse> => fakeResponse('network'))

  const sandbox = {
    self: {
      location: { origin: ORIGIN },
      addEventListener: (type: string, fn: Handler) => {
        handlers[type] = fn
      },
      skipWaiting: vi.fn(),
      clients: { claim: vi.fn() },
    },
    caches: {
      open: async (name: string) => {
        cacheNames.add(name)
        return makeCache()
      },
      keys: async () => [...cacheNames],
      delete: async (name: string) => cacheNames.delete(name),
      match: async (key: unknown) => cacheStore.get(keyOf(key)),
    },
    fetch: fetchMock,
    URL,
    console,
    Promise,
  }
  vm.runInNewContext(fs.readFileSync(SW_PATH, 'utf8'), sandbox)

  return { handlers, cacheStore, fetchMock, sandbox }
}

/** 跑一次 fetch 事件，返回 `respondWith` 收到的响应（没拦截则为 undefined）。 */
async function dispatch(sw: ReturnType<typeof load>, request: FakeRequest): Promise<unknown> {
  let responded: unknown
  sw.handlers.fetch({ request, respondWith: (r) => (responded = r) })
  return await responded
}

const get = (url: string, extra: Partial<FakeRequest> = {}): FakeRequest => ({
  url: `${ORIGIN}${url}`,
  method: 'GET',
  ...extra,
})

/** 只判定「有没有被拦截」时用：`respondWith` 是**同步**调用的，不必 await。 */
function intercepted(request: FakeRequest, sw = load()): unknown {
  let responded: unknown
  sw.handlers.fetch({ request, respondWith: (r) => (responded = r) })
  return responded
}

describe('sw.js 分流', () => {
  it('接口请求一律不拦截', () => {
    // 业务数据若进了 SW 缓存，用户会看到旧数据；认证请求被缓存则是安全问题
    expect(intercepted(get('/api/v1/feishu/workbench/wd_customer_service/records'))).toBeUndefined()
  })

  it('非 GET 一律不拦截', () => {
    expect(intercepted(get('/web/js/anything.js', { method: 'POST' }))).toBeUndefined()
  })

  it('跨域请求不拦截', () => {
    expect(intercepted({ url: 'https://other.example/x.js', method: 'GET' })).toBeUndefined()
  })

  it('导航请求是 network-first：在线时用网络返回的那份', async () => {
    const sw = load()
    sw.cacheStore.set(SHELL_URL, fakeResponse('cached-shell'))
    sw.fetchMock.mockResolvedValueOnce(fakeResponse('fresh-shell'))

    const got = await dispatch(sw, get(SHELL_URL, { mode: 'navigate' }))

    expect(sw.fetchMock).toHaveBeenCalled()
    expect(got).toMatchObject({ tag: 'fresh-shell' })
  })

  it('导航请求断网时回退到缓存的壳', async () => {
    const sw = load()
    sw.cacheStore.set(SHELL_URL, fakeResponse('cached-shell'))
    sw.fetchMock.mockRejectedValueOnce(new Error('offline'))

    expect(await dispatch(sw, get(SHELL_URL, { mode: 'navigate' }))).toMatchObject({ tag: 'cached-shell' })
  })

  it('导航拿到新壳后会更新壳缓存（否则离线兜底永远是旧的）', async () => {
    const sw = load()
    sw.cacheStore.set(SHELL_URL, fakeResponse('stale-shell'))
    sw.fetchMock.mockResolvedValueOnce(fakeResponse('fresh-shell'))

    await dispatch(sw, get(SHELL_URL, { mode: 'navigate' }))
    expect(sw.cacheStore.get(SHELL_URL)).toMatchObject({ tag: 'fresh-shell' })
  })

  it('带 hash 的产物是 cache-first：命中缓存就不发网络', async () => {
    const sw = load()
    const cached = fakeResponse('cached-js')
    sw.cacheStore.set(`${ORIGIN}/web/js/index.BOHpjF4n.js`, cached)

    const got = await dispatch(sw, get('/web/js/index.BOHpjF4n.js'))
    expect(got).toMatchObject({ tag: 'cached-js' })
    expect(sw.fetchMock).not.toHaveBeenCalled()
  })

  it('带 hash 的产物未命中缓存时才走网络', async () => {
    const sw = load()
    const got = await dispatch(sw, get('/web/js/index.BOHpjF4n.js'))
    expect(sw.fetchMock).toHaveBeenCalled()
    expect(got).toBeTruthy()
  })

  it('manifest 与图标不拦截（交给 HTTP 缓存的 no-cache 处理）', () => {
    expect(intercepted(get('/web/manifest.webmanifest'))).toBeUndefined()
    expect(intercepted(get('/web/icon-192.png'))).toBeUndefined()
  })

  it('缓存策略版本号带在缓存名里（bump 版本才能清掉旧缓存）', () => {
    const source = fs.readFileSync(SW_PATH, 'utf8')
    expect(source).toMatch(/const VERSION = '/)
    expect(source).toMatch(/fa-shell-\$\{VERSION\}/)
    expect(source).toMatch(/fa-assets-\$\{VERSION\}/)
  })
})
