<!--
  布局根容器：三区域结构
    - #app-sidebar  ← 左侧菜单导航（收起/展开）
    - #app-main     ← 右侧主区域（顶栏 + 页面内容）
      - #app-header  顶栏（面包屑、搜索、通知、用户菜单）
      - #app-content 页面内容（RouterView + 页签）
    - #app-global   ← 全局浮层层（Toast、Modal、新手引导）
-->
<template>
  <div class="app-layout" :class="{ 'app-focus': focusMode, 'app-bare': hideHeader }">
    <!-- 左侧菜单导航（专注模式不渲染） -->
    <aside v-if="!focusMode" id="app-sidebar">
      <FaSidebarMenu />
    </aside>

    <!-- 右侧主区域 -->
    <main id="app-main">
      <div v-if="!focusMode && !hideHeader" id="app-header">
        <FaHeaderBar />
      </div>
      <div id="app-content">
        <FaPageContent />
      </div>
    </main>

    <!-- 全局浮层层（引导、通知等跨页面组件） -->
    <div id="app-global">
      <FaGlobalComponent />
      <Guide v-if="guideVisible" v-model="guideVisible" @skip="onGuideFinished" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 布局根组件 —— 组装侧栏、顶栏、页面内容、全局浮层。
 *
 * 依赖：
 *   appStore.guideVisible    ← 控制新手指引显隐（session 级状态）
 *   settingStore.showGuide   ← 用户是否关闭指引（持久化）
 *
 * 流程：
 *   首次访问 → guideVisible=true → 用户完成/跳过指引 → onGuideFinished()
 *   → settingStore.showGuide=false → 后续不再显示
 */
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import Guide from '@/components/others/fa-guide/index.vue'
import { useAppStore } from '@stores/modules/app.store'
import { useSettingsStore } from '@stores/modules/setting.store'

defineOptions({ name: 'AppLayout' })

const route = useRoute()
/** 专注模式：工作台页面不显示左侧菜单和顶栏，整屏留给业务表格 */
const focusMode = computed(() => route.path.startsWith('/workbench'))
/** 业务工作台：隐藏系统顶栏，只保留侧栏 */
const hideHeader = computed(() => route.path.startsWith('/weida'))
/** 业务工作台不显示侧栏，避免侧栏状态被持久化后反复展开 */
const hideSidebar = computed(() => route.path.startsWith('/weida'))

const appStore = useAppStore()
const settingStore = useSettingsStore()

/** 进入业务工作台时侧栏默认收起，把宽度让给表格 */
function collapseWorkbenchMenu(): void {
  settingStore.setMenuOpen(false)
  try {
    const raw = JSON.parse(localStorage.getItem('setting') || '{}')
    raw.menuOpen = false
    localStorage.setItem('setting', JSON.stringify(raw))
  } catch {
    // ignore
  }
}

watch(
  () => route.path,
  (path) => {
    if (!path.startsWith('/weida')) return
    collapseWorkbenchMenu()
    window.setTimeout(collapseWorkbenchMenu, 0)
    window.setTimeout(collapseWorkbenchMenu, 300)
  },
  { immediate: true },
)

onMounted(() => {
  if (route.path.startsWith('/weida')) collapseWorkbenchMenu()
})

/** 新手指引显隐 —— session 级状态，首次登录/注册后自动弹出 */
const guideVisible = computed({
  get: () => appStore.guideVisible,
  set: (v: boolean) => appStore.showGuide(v),
})

/** 指引完成后持久化标记「不再显示」 */
function onGuideFinished(): void {
  settingStore.updateSetting('showGuide', false)
}
</script>

<style lang="scss" scoped>
@use './style';
</style>
