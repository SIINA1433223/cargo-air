<template>
  <div class="wh">
    <div class="wh-inner">
      <header>
        <div class="wh-eyebrow">威达空运货代</div>
        <h1>商机系统</h1>
        <p>数据实时读写业务库，选择你要进的工作台。</p>
      </header>
      <div class="wh-rule-strong"></div>
      <div class="wh-rule-light"></div>

      <div class="wh-grid">
        <div v-for="(c, i) in cards" :key="c.role" class="wh-col">
          <div class="wh-card" @click="router.push(`/weida/${c.role}`)">
            <div class="wh-card-top">
              <span class="wh-no">{{ String(i + 1).padStart(2, '0') }}</span>
              <span class="wh-icon">{{ c.icon }}</span>
            </div>
            <h2>{{ c.title }}</h2>
            <div class="wh-desc">{{ c.desc }}</div>
            <p class="wh-detail">{{ c.detail }}</p>
            <div class="wh-enter">进入 →</div>
          </div>
          <button class="wh-dashboard" @click="router.push(`/weida/${c.dashboard}`)">
            打开{{ c.title }}仪表盘
          </button>
        </div>
      </div>

      <footer class="wh-foot">
        <a @click="router.push('/weida/master-data')">基础数据</a>
        <span>· 客商、人员、港口、选项字典</span>
        <a style="margin-left: 16px" @click="pwdOpen = true">修改密码</a>
      </footer>
    </div>
    <el-dialog v-model="pwdOpen" title="修改密码" width="420px">
      <el-form label-position="top">
        <el-form-item label="原密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwdForm.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdOpen = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="changePassword">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '@/utils/http'

defineOptions({ name: 'WeidaHome' })
const router = useRouter()
const pwdOpen = ref(false)
const pwdSaving = ref(false)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })

async function changePassword() {
  if (pwdForm.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  if (pwdForm.new_password !== pwdForm.confirm) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  pwdSaving.value = true
  try {
    await request({
      url: '/weida/me/password',
      method: 'post',
      data: { old_password: pwdForm.old_password, new_password: pwdForm.new_password },
    })
    ElMessage.success('密码已修改')
    pwdOpen.value = false
    pwdForm.old_password = ''
    pwdForm.new_password = ''
    pwdForm.confirm = ''
  } catch (e: any) {
    ElMessage.error(e?.message || '修改失败')
  } finally {
    pwdSaving.value = false
  }
}
const cards = [
  {
    role: 'customer_service',
    dashboard: 'dashboard-cs',
    title: '国内客服',
    desc: '商机、询价、客户报价、拿到货',
    detail: '新建商机、发起航线询价、维护对客方案、确认拿到货与操作交接。',
    icon: '客',
  },
  {
    role: 'pricing',
    dashboard: 'dashboard-pricing',
    title: '航线报价',
    desc: '待报价商机、航线报价、核价',
    detail: '接收客服询价、录入供应商报价、维护有效期与等级比例、发起报价审批。',
    icon: '航',
  },
  {
    role: 'domestic_ops',
    dashboard: 'dashboard-ops',
    title: '国内操作',
    desc: '订舱、分单、提货、报关、账单',
    detail: '交接后建分单、提货计划、报关、代理到货、国外预报、做账与开票。',
    icon: '操',
  },
]
</script>

<style scoped>
.wh {
  min-height: 100%;
  overflow-y: auto;
  background: hsl(40 36% 95%);
  color: hsl(33 17% 10%);
}
.wh-inner {
  max-width: 1024px;
  margin: 0 auto;
  padding: 48px 32px;
}
.wh-eyebrow {
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wh h1 {
  margin: 12px 0 0;
  font-size: 26px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: -0.01em;
}
.wh header p {
  margin: 8px 0 0;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.7;
  color: hsl(34 9% 44%);
}
.wh-rule-strong {
  margin-top: 32px;
  border-top: 1px solid hsl(33 17% 10% / 0.2);
}
.wh-rule-light {
  margin-top: 4px;
  border-top: 1px solid hsl(38 25% 85%);
}
.wh-grid {
  margin-top: 24px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  align-items: start;
}
.wh-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.wh-card {
  min-height: 196px;
  display: flex;
  flex-direction: column;
  padding: 24px;
  border: 1px solid hsl(38 25% 85%);
  border-radius: 12px;
  background: hsl(40 59% 98%);
  cursor: pointer;
  transition: border-color 0.18s;
}
.wh-card:hover {
  border-color: hsl(190 64% 26% / 0.35);
}
.wh-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.wh-no {
  font-size: 11px;
  color: hsl(34 9% 44%);
  font-variant-numeric: tabular-nums;
}
.wh-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: hsl(190 64% 26% / 0.1);
  color: hsl(190 64% 26%);
  font-size: 13px;
  font-weight: 600;
}
.wh-card h2 {
  margin: 24px 0 0;
  font-size: 15px;
  font-weight: 500;
}
.wh-desc {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wh-detail {
  margin: 12px 0 0;
  flex: 1;
  font-size: 13px;
  line-height: 1.7;
  color: hsl(34 9% 44%);
}
.wh-enter {
  margin-top: 20px;
  font-size: 13px;
  color: hsl(190 64% 26%);
}
.wh-dashboard {
  padding: 9px 12px;
  border: 1px solid hsl(38 25% 85%);
  border-radius: 8px;
  background: hsl(40 59% 98%);
  color: hsl(34 9% 44%);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.wh-dashboard:hover {
  color: hsl(190 64% 26%);
  border-color: hsl(190 64% 26% / 0.3);
}
.wh-foot {
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid hsl(38 25% 85%);
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wh-foot a {
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}
@media (max-width: 860px) {
  .wh-grid {
    grid-template-columns: 1fr;
  }
}
</style>
