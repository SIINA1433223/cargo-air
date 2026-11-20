<template>
  <div class="page-content border-0! bg-transparent! min-h-screen flex-cc">
    <div class="flex-cc max-md:block! max-md:text-center">
      <FaThemeSvg :src="data.imgUrl" size="100%" class="w-100!" />
      <div class="ml-15 w-75 max-md:mx-auto max-md:mt-10 max-md:w-full max-md:text-center">
        <p class="text-xl leading-7 text-g-600 max-md:text-lg">{{ data.desc }}</p>
        <ElButton type="primary" size="large" @click="backHome" v-ripple class="mt-5">
          {{ data.btnText }}
        </ElButton>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useCommon } from '@/hooks/core/useCommon'
import { useUserStore } from '@stores/modules/user.store'
import FaThemeSvg from '@/components/theme/fa-theme-svg/index.vue'

const router = useRouter()
const userStore = useUserStore()

interface ExceptionData {
  /** 标题 */
  title: string
  /** 描述 */
  desc: string
  /** 按钮文本 */
  btnText: string
  /** 图片地址 */
  imgUrl: string
}

const props = withDefaults(
  defineProps<{
    data: ExceptionData
    /**
     * 「返回首页」改成**整页刷新**回首页，而不是 SPA 跳转。
     *
     * 500 页是初始化失败的兜底：那条路径上守卫内置了熔断位（`routeInitError`），
     * 只是个模块级变量，`router.push` 不会清它——点按钮跳回首页会被守卫再拦一次、
     * 又送回 500，用户看到的就是「点了没用，只能手动 F5」。整页刷新才真正清干净。
     * 只有 500 页打开它；401/403/404 没有熔断问题，继续走 SPA 内跳转（不丢 SPA 状态）。
     */
    reloadOnBack?: boolean
  }>(),
  { reloadOnBack: false }
)

const { homePath } = useCommon()

/** 与 `router/beforeEach.ts` 的 `REDIRECT_AFTER_RELOAD_KEY` 必须是同一个字符串 */
const REDIRECT_AFTER_RELOAD_KEY = '__fa_redirect_after_reload__'

const backHome = () => {
  const targetHomePath = homePath.value || '/'

  if (!userStore.isLogin) {
    router.push({
      name: 'Login',
      query: { redirect: targetHomePath },
    })
    return
  }

  if (props.reloadOnBack) {
    // hash 路由下 `location.reload()` 会回到当前地址（`#/500` 或 `#/500?redirect=…`），
    // 落点只能靠 sessionStorage 传给刷新后的那次初始化，见 `beforeEach.ts`。
    sessionStorage.removeItem(REDIRECT_AFTER_RELOAD_KEY)
    sessionStorage.setItem(REDIRECT_AFTER_RELOAD_KEY, targetHomePath)
    window.location.reload()
    return
  }

  router.push(targetHomePath)
}
</script>
