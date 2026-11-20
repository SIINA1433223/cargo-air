// ---------------------------------------------------------------------------
// 样式（顺序：tailwind 基础 → 项目全局 → Element Plus 暗色 → 动画库）
// ---------------------------------------------------------------------------
import '@styles/core/tailwind.css'
import '@styles/index.scss'
import 'element-plus/theme-chalk/dark/css-vars.css'
import 'animate.css'

// ---------------------------------------------------------------------------
// 应用初始化
// ---------------------------------------------------------------------------
import App from './App.vue'
import { createApp } from 'vue'
import { printConsoleBanner } from '@utils/sys'
import { initPlugins } from '@/plugins'

/**
 * iOS Safari 中 touch 事件默认是 passive 的，导致 `:active` CSS 伪类不生效。
 * 注册一个空的 `touchstart` 监听（非 passive）来激活 `:active` 响应。
 */
document.addEventListener('touchstart', function () {}, { passive: false })

/**
 * 注册离线外壳 Service Worker（`public/sw.js`）。
 *
 * 三条自我约束，都是为了让「注册 SW 这件事本身」永远不可能把应用搞坏：
 *  1. 不 await、不进启动链路 —— 浏览器不支持或注册失败，应用照常跑；
 *  2. 只在生产构建注册 —— dev 下热更新与 SW 缓存会互相打架（改了代码页面还是旧的，
 *     属于最难查的一类问题），所以 `pnpm dev` 里根本不注册；
 *  3. 整段 catch —— 失败只在控制台留一行，绝不冒泡成启动错误。
 *
 * 缓存策略与「为什么入口 HTML 不进缓存」见 `public/sw.js` 顶部注释。
 */
function registerServiceWorker(): void {
  if (!import.meta.env.PROD) return
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return

  // 等 load 之后再注册：别和首屏资源抢带宽
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/web/sw.js', { scope: '/web/' }).catch((error) => {
      console.warn('[SW] 注册失败，应用不受影响：', error)
    })
  })
}

/** 启动顺序：
 *  1. printConsoleBanner  —— 控制台欢迎信息（无依赖）
 *  2. initPlugins         —— 注册所有 Vue 插件（Pinia → Router → 指令 → 国际化 → Element Plus）
 *  3. mount               —— 挂载根组件
 *  挂载后 App.vue 的 onMounted 中初始化站点标题/favicon（需等 Pinia store 就绪）
 */
;(async () => {
  const app = createApp(App)
  printConsoleBanner()
  await initPlugins(app)
  app.mount('#app')
  registerServiceWorker()
})()
