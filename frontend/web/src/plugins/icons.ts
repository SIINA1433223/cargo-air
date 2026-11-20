import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import { addCollection } from '@iconify/vue'
import type { App } from 'vue'

// 注册所有图标
export function initElIcons(app: App<Element>) {
  for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
  }
}

/**
 * 本地注册 Iconify 图标集（仅 ri，图标风格统一为 Remix Icon）。
 *
 * `<FaSvgIcon>`（`@iconify/vue` 的 `<Icon>`）默认从 `api.iconify.design`
 * 在线拉取图标数据，网络不可达时报 `ERR_CONNECTION_CLOSED` 且图标全空。
 * 本地注册后按需命中即渲染，不再发起远程请求（离线可用）。
 *
 * ri 全集是 3229 个图标的 JSON（约 1MB），是首屏里最大的一块。这里**不**静态 import，
 * 改成启动后立刻异步取回：首屏不再为它阻塞，取回到 `addCollection` 时
 * `@iconify/vue` 是响应式的，已经在屏上的图标会自己补上，不需要刷新页面。
 */
export function initIconifyIcons(): void {
  void import('@iconify-json/ri').then(({ icons }) => {
    addCollection(icons)
  })
}
