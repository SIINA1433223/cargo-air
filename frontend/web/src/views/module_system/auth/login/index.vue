<!--
  登录页（2026-11-19 重做）。

  原版是 FastApiAdmin 自带的营销式登录页：左侧插画、顶栏切换、扫码/手机/注册/找回
  四个面板、拖拽验证…… 用户的要求是「登录界面必须简洁明了」，所以这里只留一件事：
  账号 + 密码 + 验证码 + 登录。

  逻辑不变，仍走 `userStore.login()` + `waitForDynamicRoutesReady()` + 跳转。
-->
<template>
  <div class="login-wrap">
    <div class="login-card">
      <header class="brand">
        <div class="brand-mark">威达空运</div>
        <p class="brand-sub">国际空运业务系统</p>
      </header>

      <form class="form" @submit.prevent="submit">
        <label class="field">
          <span class="label">账号</span>
          <input
            v-model.trim="form.username"
            class="input"
            type="text"
            autocomplete="username"
            placeholder="请输入账号"
            :disabled="loading"
            @keyup.enter="submit" />
        </label>

        <label class="field">
          <span class="label">密码</span>
          <input
            v-model="form.password"
            class="input"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            :disabled="loading"
            @keyup.enter="submit" />
        </label>

        <label v-if="captcha.enable" class="field">
          <span class="label">验证码</span>
          <div class="captcha-row">
            <input
              v-model.trim="form.captcha"
              class="input"
              type="text"
              placeholder="请输入验证码"
              :disabled="loading"
              @keyup.enter="submit" />
            <button type="button" class="captcha-img" title="点一下换一张" @click="loadCaptcha">
              <img v-if="captcha.img_base" :src="captcha.img_base" alt="验证码" />
              <span v-else class="captcha-loading">加载中…</span>
            </button>
          </div>
        </label>

        <p v-if="error" class="error">{{ error }}</p>

        <button class="submit" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>

      <p class="hint">账号由管理员分配，忘记密码请联系主管</p>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import AuthAPI, { type CaptchaInfo, type LoginFormData } from '@/api/module_system/auth'
  import { waitForDynamicRoutesReady } from '@/router/beforeEach'
  import { useUserStore } from '@stores/modules/user.store'
  import { resolveRedirectTarget } from '@utils/auth/redirect-target'

  defineOptions({ name: 'Login' })

  const router = useRouter()
  const route = useRoute()
  const userStore = useUserStore()

  const loading = ref(false)
  const error = ref('')
  const captcha = reactive<CaptchaInfo>({ enable: false, key: '', img_base: '' })

  const form = reactive<LoginFormData>({
    username: '',
    password: '',
    captcha: '',
    captcha_key: '',
    remember: true,
    login_type: 'account',
  })

  /** 取一张验证码；后端关了验证码时 `enable=false`，这一块整个不显示。 */
  async function loadCaptcha(): Promise<void> {
    try {
      const res = await AuthAPI.getCaptcha()
      // `request()` 返回的是 axios 整体响应：`.data` 是 {code,msg,data} 信封，
      // 真正的验证码在 `.data.data`（与 `userStore.login` 里 `response.data.data` 同一层）。
      const data = (res as unknown as { data?: { data?: CaptchaInfo } }).data?.data
      if (!data) return
      captcha.enable = data.enable
      captcha.img_base = data.img_base
      form.captcha_key = data.key ?? ''
      form.captcha = ''
    } catch {
      // 取不到就当没有验证码：后端会照旧校验，报错话术由后端给。
      captcha.enable = false
    }
  }

  async function submit(): Promise<void> {
    if (loading.value) return
    error.value = ''
    if (!form.username) return void (error.value = '请输入账号')
    if (!form.password) return void (error.value = '请输入密码')
    if (captcha.enable && !form.captcha) return void (error.value = '请输入验证码')

    loading.value = true
    try {
      await userStore.login({ ...form })
      await waitForDynamicRoutesReady()
      await router.replace(resolveRedirectTarget(router, route.query))
    } catch (e) {
      const said = (e as { data?: { msg?: string }; message?: string })?.data?.msg
      error.value = said || (e as { message?: string })?.message || '登录失败，请稍后再试'
      await loadCaptcha()
    } finally {
      loading.value = false
    }
  }

  onMounted(loadCaptcha)
</script>

<style scoped>
  .login-wrap {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    /* 与工作台同一套：暖纸底 + 深青墨 */
    background: hsl(40 36% 95%);
    padding: 24px;
  }

  .login-card {
    width: 100%;
    max-width: 380px;
    background: hsl(40 59% 98%);
    border: 1px solid hsl(38 25% 85%);
    border-radius: 12px;
    padding: 36px 32px 28px;
    box-shadow: 0 1px 2px rgba(31, 75, 69, 0.04);
  }

  .brand {
    margin-bottom: 28px;
  }

  .brand-mark {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: hsl(190 64% 26%);
  }

  .brand-sub {
    margin-top: 6px;
    font-size: 13px;
    color: #8a8378;
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }

  .label {
    font-size: 13px;
    color: #6b655c;
  }

  .input {
    height: 40px;
    padding: 0 12px;
    font-size: 14px;
    color: #2b2a28;
    background: #fff;
    border: 1px solid #ddd6c9;
    border-radius: 8px;
    outline: none;
    transition: border-color 0.15s;
  }

  .input:focus {
    border-color: hsl(190 64% 26%);
  }

  .captcha-row {
    display: flex;
    gap: 10px;
  }

  .captcha-row .input {
    flex: 1;
  }

  .captcha-img {
    width: 108px;
    height: 40px;
    padding: 0;
    border: 1px solid #ddd6c9;
    border-radius: 8px;
    background: #fff;
    overflow: hidden;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .captcha-img img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .captcha-loading {
    font-size: 12px;
    color: #a8a094;
  }

  .submit {
    margin-top: 6px;
    height: 42px;
    border: none;
    border-radius: 8px;
    background: #186d6d;
    color: hsl(40 36% 95%);
    font-size: 15px;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: background 0.15s;
  }

  .submit:hover:not(:disabled) {
    background: #17403a;
  }

  .submit:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .error {
    margin: -6px 0 0;
    font-size: 13px;
    color: #b4492f;
  }

  .hint {
    margin-top: 22px;
    font-size: 12px;
    color: #a8a094;
    text-align: center;
  }
</style>
