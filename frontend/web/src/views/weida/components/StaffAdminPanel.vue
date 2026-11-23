<template>
  <div class="sa">
    <div class="sa-toolbar">
      <span class="sa-tip">维护人员资料、身份和登录账号；身份可多选，后续按身份控制权限。</span>
      <el-button type="primary" @click="openEdit()">新增人员</el-button>
    </div>
    <el-table :data="items" size="small" border max-height="560">
      <el-table-column prop="name" label="姓名" width="130" />
      <el-table-column label="身份" min-width="200">
        <template #default="{ row }">{{ roleText(row) || '—' }}</template>
      </el-table-column>
      <el-table-column label="账号" min-width="150">
        <template #default="{ row }">
          <span v-if="row.username">{{ row.username }}</span>
          <span v-else class="sa-empty">未开通</span>
        </template>
      </el-table-column>
      <el-table-column prop="mobile" label="手机" width="120" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.active ? 'success' : 'info'" size="small">
            {{ row.active ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button v-if="!row.local_user_id" link type="primary" @click="openAccount(row)">开通账号</el-button>
          <el-button v-else link type="primary" @click="openReset(row)">重置密码</el-button>
          <el-button link @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="accountOpen"
      :title="
        accountMode === 'create' ? `开通账号 - ${accountTarget?.name || ''}` : `重置密码 - ${accountTarget?.name || ''}`
      "
      width="460px">
      <el-form label-position="top">
        <el-form-item v-if="accountMode === 'create'" label="登录账号" required>
          <el-input v-model="accountForm.username" placeholder="至少 3 个字符" />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input v-model="accountForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitAccount">
          {{ accountMode === 'create' ? '开通' : '重置' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editOpen" :title="editForm.id ? '编辑人员' : '新增人员'" width="520px">
      <el-form label-position="top">
        <el-form-item label="姓名" required>
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="身份（可多选）" required>
          <el-select v-model="editForm.roles" multiple style="width: 100%">
            <el-option v-for="o in ROLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="editForm.mobile" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="editForm.email" />
        </el-form-item>
        <el-form-item v-if="editForm.id" label="状态">
          <el-switch v-model="editForm.active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveStaff">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@/utils/http'

defineOptions({ name: 'WeidaStaffAdminPanel' })

const ROLE_OPTIONS = [
  { value: 'customer_service', label: '国内客服' },
  { value: 'pricing', label: '航线' },
  { value: 'domestic_ops', label: '国内操作' },
  { value: 'admin', label: '管理员' },
  { value: 'super_admin', label: '超级管理员' },
]

const items = ref<Array<Record<string, any>>>([])
const saving = ref(false)
const accountOpen = ref(false)
const accountMode = ref<'create' | 'reset'>('create')
const accountTarget = ref<Record<string, any> | null>(null)
const accountForm = reactive({ username: '', password: '' })
const editOpen = ref(false)
const editForm = reactive({
  id: 0,
  name: '',
  roles: [] as string[],
  mobile: '',
  email: '',
  active: true,
})

function normalizeRoles(row: Record<string, any>): string[] {
  const raw = row?.roles
  if (Array.isArray(raw)) return raw.filter(Boolean)
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.filter(Boolean)
    } catch {
      return raw
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean)
    }
  }
  return row?.role ? [row.role] : []
}

function roleText(row: Record<string, any>): string {
  return normalizeRoles(row)
    .map((r) => ROLE_OPTIONS.find((o) => o.value === r)?.label || r)
    .join('、')
}

async function load() {
  try {
    const res = await request<any>({ url: '/weida/admin/staff', method: 'get' })
    items.value = res.data?.data?.items ?? []
  } catch (e: any) {
    ElMessage.error(e?.message || '读取人员失败')
  }
}

function openAccount(row: Record<string, any>) {
  accountMode.value = 'create'
  accountTarget.value = row
  accountForm.username = ''
  accountForm.password = ''
  accountOpen.value = true
}

function openReset(row: Record<string, any>) {
  accountMode.value = 'reset'
  accountTarget.value = row
  accountForm.username = row.username || ''
  accountForm.password = ''
  accountOpen.value = true
}

async function submitAccount() {
  const target = accountTarget.value
  if (!target) return
  if (accountMode.value === 'create' && accountForm.username.trim().length < 3) {
    ElMessage.warning('登录账号至少 3 个字符')
    return
  }
  if (accountForm.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  saving.value = true
  try {
    await request<any>({
      url:
        accountMode.value === 'create'
          ? `/weida/admin/staff/${target.id}/account`
          : `/weida/admin/staff/${target.id}/password`,
      method: 'post',
      data:
        accountMode.value === 'create'
          ? { username: accountForm.username.trim(), password: accountForm.password }
          : { password: accountForm.password },
    })
    ElMessage.success(accountMode.value === 'create' ? '账号已开通' : '密码已重置')
    accountOpen.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    saving.value = false
  }
}

function openEdit(row?: Record<string, any>) {
  editForm.id = row?.id || 0
  editForm.name = row?.name || ''
  editForm.roles = row ? normalizeRoles(row) : ['customer_service']
  editForm.mobile = row?.mobile || ''
  editForm.email = row?.email || ''
  editForm.active = row ? !!row.active : true
  editOpen.value = true
}

async function saveStaff() {
  if (!editForm.name.trim() || !editForm.roles.length) {
    ElMessage.warning('姓名和身份必填')
    return
  }
  saving.value = true
  try {
    await request<any>({
      url: editForm.id ? `/weida/admin/staff/${editForm.id}` : '/weida/admin/staff',
      method: editForm.id ? 'patch' : 'post',
      data: {
        name: editForm.name.trim(),
        role: editForm.roles[0],
        roles: editForm.roles,
        mobile: editForm.mobile || null,
        email: editForm.email || null,
        active: editForm.active ? 1 : 0,
      },
    })
    ElMessage.success('已保存')
    editOpen.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sa-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.sa-tip {
  color: #8a8378;
  font-size: 13px;
}
.sa-empty {
  color: #a39b8e;
}
</style>
