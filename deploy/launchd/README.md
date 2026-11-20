# 内网演示部署（macOS launchd）

把本机的「MySQL + Redis + 后端 API（同时托管前端页面）」三个服务交给 launchd 托管，
做到**开机自启、崩了自动拉起**；再关掉系统睡眠，同事的电脑就能稳定打开网址。

前端**不再是常驻服务**：以前跑 vite dev（6110），同事每次访问都要等它现编译 425 个模块，
叠加后端冷启动，首屏经常卡到超时、要刷新好几次。现在改成构建成静态产物（`pnpm build`），
由后端 6100 **同一个端口**同时提供页面与接口，同事只访问一个地址。

## 为什么需要这个

现象：换一台电脑打开网址报「无法访问此网站 / 连接超时」——请求根本没到这台机器。
两个原因：

1. **系统睡眠**（主因）。`pmset` 里 `sleep 1`，笔记本合盖或闲置一会儿就睡，睡眠后 LAN 端口不可达。
2. **服务寿命**。服务是终端里手动起的，随终端一起死；后端、Redis、MySQL 重启后也不会自己起来。

（历史第三个原因是前端走 vite dev —— 见上，已经换掉了。）

## 安装

```bash
bash deploy/launchd/install.sh
```

脚本会（幂等，可重复跑）：

- `cd frontend/web && pnpm build` 重建前端产物（约 40 秒；`vue-tsc --noEmit` 顺带当一次前端类型门禁）
- 把本目录 3 个 plist 里的 `__HOME__` / `__REPO__` 换成真实路径，装到 `~/Library/LaunchAgents/`
- **卸载**旧的 `com.weida.frontend`（6110 的 vite），连同它的 plist 一起删掉
- 停掉当前手动起的 mysqld / redis-server / 后端 / 残留的 vite（避免端口冲突）
- `launchctl bootstrap` 三个服务

**另外必须手跑一条**（需要 root，脚本代跑不了）：

```bash
sudo pmset -a sleep 0 displaysleep 0 disablesleep 1
```

演示期间**保持插电**——电量过低时 macOS 仍会强制「Maintenance Sleep」，`disablesleep` 拦不住那种保护性睡眠。

## 同事访问入口

```
http://heisenbergdeMacBook-Air.local:6100/web
```

`.local` 是 mDNS 零配置域名，同事的电脑（macOS / Windows 10+ / 装了 Bonjour 的）直接能解析，
不随 IP 漂移。

> 只输主机名和端口（`http://xxxx.local:6100`）也行：后端在 `/` 上有一条 302 到 `/web/`。
>
> 以前这条入口依赖 `vite.config.ts` 的 `server.allowedHosts: ['.local']`（vite 默认拦主机名访问，
> 403 Blocked request）。**现在路径上没有 vite 了，这个坑自动消失**；那行配置留着无妨，
> 只在你手动跑 `pnpm dev` 时才起作用。

若对方环境解析不了 `.local`，用 IP 兜底：

```
http://192.168.0.148:6100/web
```

**建议再做一步 DHCP 预留**，免得 IP 变了兜底地址失效：
登录路由器 `192.168.0.1` → 找「DHCP 地址保留 / 静态分配」→ 把 MAC
`36:10:d7:de:1f:08` 绑定到 `192.168.0.148`。

## 三个服务

| Label               | 端口 | 干什么                              | 日志                               |
| ------------------- | ---- | ----------------------------------- | ---------------------------------- |
| `com.weida.mysql`   | 3306 | 业务库（前台跑，**不带** `--daemonize`） | `~/Library/Logs/weida-mysql.log`   |
| `com.weida.redis`   | 6379 | 认证会话 + 飞书 token 缓存          | `~/Library/Logs/weida-redis.log`   |
| `com.weida.backend` | 6100 | FastApiAdmin 后端 + 前端静态产物托管 | `~/Library/Logs/weida-backend.log` |

三个都是 `RunAtLoad` + `KeepAlive` + `ThrottleInterval 10`。
启动顺序不保证——后端先起来连不上 MySQL/Redis 会自己退，launchd 10 秒后重拉，最终会起来。

`com.weida.frontend.plist` 仍在目录里，但**不再被装载**（按「本地开发想用 vite 热更新」保留，
用法见文件头注释）。注意 6110 与 6100 是**两套**代码，两边同时在线时容易误判「改了没生效」。

## 改完代码怎么办

后端（plist 里**显式传了 `--no-reload`**：`--env=dev` 本身会开热重载，而演示机上改任何一个
`.py`（连测试文件都算）都触发重启，那两秒里所有在途请求失败、浏览器报「网络错误」；改完
`main.py`/plist 后要重跑一次 `install.sh` 才会生效）：

```bash
launchctl kickstart -k gui/$(id -u)/com.weida.backend
```

前端**必须重新构建**，否则页面还是旧的（产物在 `frontend/web/dist`，后端按请求读盘，
不用重启后端；但保险起见可一起 kickstart）：

```bash
cd frontend/web && pnpm build
launchctl kickstart -k gui/$(id -u)/com.weida.backend
```

同事那边**不用再让他们硬刷新了**——后端给静态资源下了正确的缓存头（见下节）。以前要硬
刷新，是因为 `index.html` 没有 `Cache-Control`，浏览器按启发式缓存它几小时；那样发完新版
同事还在用旧入口，而它引用的 chunk 已随 `dist/` 替换下线，表现为**白屏且刷新救不回来**。

## 静态资源缓存与 PWA

`app/scripts/init_app.py` 的 `_StaticFiles.file_response` 按「文件名里有没有内容 hash」下发：

| 请求 | 响应头 | 效果 |
|---|---|---|
| `/web/index.html`、`sw.js`、`manifest.webmanifest`、`favicon.*` | `no-cache` | 每次复验，etag 命中回 304；**发版即时生效** |
| `/web/js/**`、`/web/css/**`、`/web/img/**`、`/web/webp/**`（名带 hash） | `public, max-age=31536000, immutable` | 永久缓存，第二次打开 0 请求 |

首屏 gzip 后约 **2.09MB**，全部落在第二类里，所以**只有第一次访问要下**。这套策略也是
`public/sw.js`（离线外壳 + 可安装）能安全存在的前提：壳永远不会把入口钉死。

> ### ⚠️ 当前部署下 Service Worker 是**不生效**的（这是浏览器的硬性规则，不是配置问题）
>
> 入口是 `http://<主机>:6100/web`，**明文 HTTP 不是「安全上下文」**。Chrome/Edge 只在
> HTTPS 或 `localhost` 下才提供 `navigator.serviceWorker`，因此：
>
> - `sw.js` **永远不会被请求**（`src/main.ts` 里的特性检测直接跳过），离线外壳与
>   「安装应用」入口都不会出现。地址栏左侧会显示「不安全」。
> - 实测佐证：那台 Windows 访问时请求了 `manifest.webmanifest`，但**一次都没请求 `sw.js`**。
>
> **`Cache-Control` 那部分不受影响**（HTTP 缓存与安全上下文无关），所以「第二次打开秒开」
> 这个主要收益是真实生效的——实测一次冷加载之后不再重复下载那批 JS。
>
> 想要真正的可安装 PWA，必须先上 HTTPS（如 `mkcert` 生成内网证书 + 把根证书装到每台
> 同事机器上，再让后端挂证书）。**代码不用改**：一旦变成安全上下文，`sw.js` 会自动开始
> 注册生效。
>
> 只想「像应用一样独立窗口」的话，用浏览器自带的功能即可，**不需要 HTTPS**：
> Edge 菜单「应用 → 将此站点作为应用安装」；Chrome 菜单「投放、保存和共享 → 创建快捷方式」
> 并勾选「在窗口中打开」。

### 紧急刹车：万一 Service Worker 把同事卡住

用下面这份内容覆盖 `frontend/web/public/sw.js`，重新 `pnpm build` 即可。老用户下次导航时
它会自行卸载并清空全部缓存，**不需要让任何人去清站点数据**：

```js
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) =>
  e.waitUntil(
    (async () => {
      await Promise.all((await caches.keys()).map((k) => caches.delete(k)))
      await self.registration.unregister()
      await self.clients.claim()
    })(),
  ),
)
```

改了 `sw.js` 的缓存**策略**（不是这份刹车）时，记得同步 bump 里面的 `VERSION`，否则旧缓存
不会被清理。

## 排查

```bash
# 某个服务是不是活着
launchctl print gui/$(id -u)/com.weida.backend | grep -E 'state|pid'

# 端口
lsof -nP -iTCP:3306 -iTCP:6379 -iTCP:6100 -sTCP:LISTEN

# 看日志
tail -f ~/Library/Logs/weida-backend.log

# 前端首页能不能拿到（install.sh 末尾也会自检一次）
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6100/web/
```

页面打不开但后端活着时，先确认 `frontend/web/dist/index.html` 在不在——后端启动时
**产物目录不存在只记 WARNING、不挂载 `/web`**（不影响接口），这种情况跑一次 `pnpm build` 再重启后端。

## 卸载

```bash
bash deploy/launchd/uninstall.sh
```

只摘掉 launchd 托管，不动数据和 `~/.local` 下的服务本体。睡眠设置要还原：
`sudo pmset -a sleep 1 displaysleep 10 disablesleep 0`
