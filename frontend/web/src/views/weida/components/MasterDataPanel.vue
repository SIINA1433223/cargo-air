<template>
  <div class="md">
    <header class="md-head">
      <div>
        <h2>基础数据维护</h2>
        <p>客商档案、人员与账号、下拉选项统一在这里维护。</p>
      </div>
      <el-button link @click="$router.push('/dashboard')">← 总览</el-button>
    </header>

    <el-tabs v-model="activeTab" class="md-tabs" @tab-change="onTabChange">
      <el-tab-pane label="客商档案" name="vendors">
        <div class="md-toolbar">
          <el-input
            v-model="vendorKeyword"
            placeholder="搜索客商编号 / 全称 / 简称"
            clearable
            style="width: 320px"
            @keyup.enter="loadVendors"
            @clear="loadVendors" />
          <el-button @click="loadVendors">搜索</el-button>
          <el-button type="primary" @click="openVendor()">新增客商</el-button>
        </div>
        <el-table :data="vendors" size="small" border max-height="560">
          <el-table-column
            v-for="c in vendorColumns"
            :key="c.column"
            :prop="c.column"
            :label="c.label"
            :min-width="vendorWidth(c)"
            show-overflow-tooltip>
            <template #default="{ row }">{{ vendorText(row[c.column]) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openVendor(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="目的港" name="ports">
        <div class="md-toolbar">
          <el-input
            v-model="portKeyword"
            placeholder="搜索 City Name / City Code / 国家代码"
            clearable
            style="width: 360px"
            @keyup.enter="loadPorts"
            @clear="loadPorts" />
          <el-button @click="loadPorts">搜索</el-button>
          <span class="md-count">共 {{ portTotal }} 条</span>
        </div>
        <el-table :data="ports" size="small" border max-height="560">
          <el-table-column prop="city_code" label="City Code" width="110" />
          <el-table-column prop="city_name" label="City Name" min-width="190" />
          <el-table-column prop="country_code" label="Cntry. Code" width="110" />
          <el-table-column prop="dep" label="Dep." width="100" />
          <el-table-column prop="export_port" label="出口口岸" min-width="260" show-overflow-tooltip />
          <el-table-column prop="operation_requirement" label="操作要求" min-width="260" show-overflow-tooltip />
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openPort(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="portPage"
          class="md-pager"
          layout="prev, pager, next"
          :page-size="portPageSize"
          :total="portTotal"
          @current-change="loadPorts" />
      </el-tab-pane>

      <el-tab-pane label="人员与账号" name="staff">
        <StaffAdminPanel />
      </el-tab-pane>

      <el-tab-pane label="邮件监控" name="mail">
        <div class="md-toolbar">
          <el-button @click="loadMailStatus">刷新</el-button>
          <span class="md-count">超过 15 分钟未扫描标红</span>
        </div>
        <el-table v-loading="mailLoading" :data="mailStatus" size="small" border>
          <el-table-column prop="name" label="监控任务" min-width="180" />
          <el-table-column prop="account" label="邮箱" min-width="210" />
          <el-table-column prop="folder" label="游标" min-width="160" />
          <el-table-column prop="last_uid" label="最后 UID" width="110" />
          <el-table-column prop="last_scan_at" label="最后扫描" min-width="170" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.stale ? 'danger' : 'success'" size="small">
                {{ row.stale ? '超时' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div class="md-count" style="margin-top: 12px">
          <span v-for="(v, k) in mailCounts" :key="k" style="margin-right: 18px">
            {{ k }}：{{ v }}
          </span>
        </div>
      </el-tab-pane>

      <el-tab-pane label="车队" name="fleet">
        <div class="md-toolbar">
          <el-select v-model="fleetKind" style="width: 160px" @change="loadFleet">
            <el-option label="车队计划" value="plan" />
            <el-option label="车队数据" value="data" />
            <el-option label="车队账单" value="bill" />
            <el-option label="报价审核" value="review" />
          </el-select>
          <el-input v-model="fleetKeyword" placeholder="搜索" clearable style="width: 260px" @keyup.enter="loadFleet" />
          <el-button @click="loadFleet">搜索</el-button>
          <span class="md-count">共 {{ fleetTotal }} 条</span>
        </div>
        <el-table v-loading="fleetLoading" :data="fleetRows" size="small" border max-height="560">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column
            v-for="c in fleetColumns"
            :key="c"
            :label="c"
            :min-width="160"
            show-overflow-tooltip>
            <template #default="{ row }">{{ fleetText(row.data[c]) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openFleetEdit(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="fleetPage"
          class="md-pager"
          layout="prev, pager, next"
          :page-size="100"
          :total="fleetTotal"
          @current-change="loadFleet" />
      </el-tab-pane>

      <el-tab-pane label="自动化" name="automation">
        <div class="md-toolbar">
          <el-button @click="loadAutomation">刷新</el-button>
          <span class="md-count">共 {{ automationRules.length }} 条规则</span>
        </div>
        <el-table v-loading="automationLoading" :data="automationRules" size="small" border max-height="420">
          <el-table-column prop="name" label="自动化" min-width="280" />
          <el-table-column prop="scope" label="涉及表" min-width="200" />
          <el-table-column prop="trigger" label="触发 / 动作" min-width="220" />
          <el-table-column label="状态" width="170">
            <template #default="{ row }">
              <el-tag :type="row.status.startsWith('已迁移') ? 'success' : row.status.startsWith('已停用') ? 'info' : 'warning'" size="small">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <h3 style="margin-top: 18px">最近执行</h3>
        <el-table :data="automationLogs" size="small" border max-height="320">
          <el-table-column prop="create_time" label="时间" min-width="180" />
          <el-table-column prop="action" label="动作" min-width="140" />
          <el-table-column prop="record_id" label="记录" width="90" />
          <el-table-column prop="message" label="结果" min-width="260" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="选项字典" name="options">
        <div class="md-toolbar">
          <el-select
            v-model="optionCategory"
            placeholder="全部分类"
            clearable
            style="width: 220px"
            @change="loadOptions">
            <el-option v-for="c in optionCategories" :key="c" :label="categoryLabel(c)" :value="c" />
          </el-select>
          <el-button type="primary" @click="openOption()">新增选项</el-button>
        </div>
        <el-table :data="options" size="small" border max-height="560">
          <el-table-column label="分类" width="180">
            <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
          </el-table-column>
          <el-table-column prop="value" label="选项值" min-width="220" />
          <el-table-column prop="sort" label="排序" width="80" />
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-tag :type="row.active ? 'success' : 'info'" size="small">
                {{ row.active ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button link type="primary" @click="openOption(row)">编辑</el-button>
              <el-button link type="danger" @click="removeOption(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="vendorOpen" :title="vendorForm.id ? '编辑客商' : '新增客商'" width="680px">
      <el-form label-position="top" class="md-form">
        <el-form-item v-for="c in vendorColumns" :key="c.column" :label="c.label" :class="{ 'md-wide': vendorWide(c) }">
          <el-select
            v-if="c.column === 'merchant_status'"
            v-model="vendorForm[c.column]"
            allow-create
            filterable
            clearable
            style="width: 100%">
            <el-option label="启用" value="启用" />
            <el-option label="停用" value="停用" />
          </el-select>
          <el-input
            v-else
            v-model="vendorForm[c.column]"
            :type="vendorWide(c) ? 'textarea' : 'text'"
            :rows="vendorWide(c) ? 2 : undefined" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="vendorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveVendor">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="portOpen" title="编辑目的港" width="640px">
      <el-form label-position="top">
        <el-form-item label="City Name">
          <el-input v-model="portForm.city_name" disabled />
        </el-form-item>
        <el-form-item label="City Code">
          <el-input v-model="portForm.city_code" disabled />
        </el-form-item>
        <el-form-item label="Cntry. Code">
          <el-input v-model="portForm.country_code" />
        </el-form-item>
        <el-form-item label="Dep.">
          <el-input v-model="portForm.dep" />
        </el-form-item>
        <el-form-item label="出口口岸">
          <el-input v-model="portForm.export_port" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="操作要求">
          <el-input v-model="portForm.operation_requirement" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="portOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePort">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="optionOpen" :title="optionForm.id ? '编辑选项' : '新增选项'" width="480px">
      <el-form label-position="top">
        <el-form-item label="分类" required>
          <el-select v-model="optionForm.category" allow-create filterable style="width: 100%">
            <el-option v-for="c in optionCategories" :key="c" :label="categoryLabel(c)" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="选项值" required>
          <el-input v-model="optionForm.value" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="optionForm.sort" :min="0" :controls="false" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="optionForm.active" :active-value="1" :inactive-value="0" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="optionOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveOption">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/http'
import StaffAdminPanel from './StaffAdminPanel.vue'

defineOptions({ name: 'WeidaMasterDataPanel' })

const activeTab = ref('vendors')
const mailStatus = ref<Array<Record<string, any>>>([])
const mailCounts = ref<Record<string, number>>({})
const mailLoading = ref(false)
const automationRules = ref<Array<Record<string, any>>>([])
const automationLogs = ref<Array<Record<string, any>>>([])
const automationLoading = ref(false)
const fleetKind = ref('plan')
const fleetRows = ref<Array<Record<string, any>>>([])
const fleetColumns = ref<string[]>([])
const fleetTotal = ref(0)
const fleetPage = ref(1)
const fleetKeyword = ref('')
const fleetLoading = ref(false)
const fleetEditOpen = ref(false)
const fleetSaving = ref(false)
const fleetEditId = ref(0)
const fleetEditData = ref<Record<string, string>>({})
const fleetEditFields = ref<string[]>([])
const saving = ref(false)

const vendors = ref<Array<Record<string, any>>>([])
const vendorColumns = ref<Array<{ column: string; label: string; type: string }>>([])
const vendorKeyword = ref('')
const vendorOpen = ref(false)
const vendorForm = reactive<Record<string, any>>({
  id: 0,
  merchant_no: '',
  merchant_full_name: '',
  merchant_short_name: '',
  merchant_role: '',
  merchant_status: '启用',
  default_currency: '',
  merchant_address: '',
})

const options = ref<Array<Record<string, any>>>([])
const ports = ref<Array<Record<string, any>>>([])
const portKeyword = ref('')
const portPage = ref(1)
const portPageSize = 50
const portTotal = ref(0)
const portOpen = ref(false)
const portForm = reactive<Record<string, any>>({
  id: 0,
  city_name: '',
  city_code: '',
  country_code: '',
  dep: '',
  export_port: '',
  operation_requirement: '',
})

const optionCategories = ref<string[]>([])
const optionCategory = ref('')
const optionOpen = ref(false)
const optionForm = reactive<Record<string, any>>({
  id: 0,
  category: 'transport_clause',
  value: '',
  sort: 0,
  active: 1,
})

const CATEGORY_LABELS: Record<string, string> = {
  transport_clause: '运输条款',
  destination_port: '目的港',
  goods_special_attr: '货物特殊属性',
}

function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] || category
}

function jsonList(value: any): string {
  if (!value) return ''
  if (Array.isArray(value)) return value.join('、')
  if (typeof value !== 'string') return String(value)
  try {
    const parsed = JSON.parse(value.replace(/^\{|\}$/g, '').replace(/,(?=[^,]+$)/, ','))
    if (Array.isArray(parsed)) return parsed.join('、')
  } catch {
    // 原样显示
  }
  return value.replace(/[{}"]/g, '')
}

async function loadVendors() {
  try {
    const res = await request<any>({
      url: '/weida/master/vendors',
      method: 'get',
      params: { keyword: vendorKeyword.value || undefined, page: 1, page_size: 200 },
    })
    vendors.value = res.data?.data?.items ?? []
    vendorColumns.value = res.data?.data?.columns ?? []
  } catch (e: any) {
    ElMessage.error(e?.message || '读取客商失败')
  }
}

function vendorText(value: any): string {
  if (value === null || value === undefined || value === '') return ''
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function vendorWide(c: { column: string; type: string }) {
  return [
    'merchant_address',
    'special_requirements_risk_warning',
    'account_period_cooperation_terms',
    'merchant_person_in_charge',
    'invoice_requirement',
  ].includes(c.column)
}

function vendorWidth(c: { column: string; type: string }) {
  if (c.column === 'merchant_full_name' || c.column === 'merchant_address') return 240
  if (c.type === 'text' || c.type === 'textarea') return 160
  return 120
}

function openVendor(row?: Record<string, any>) {
  for (const c of vendorColumns.value) {
    vendorForm[c.column] = row?.[c.column] ?? ''
  }
  vendorForm.id = row?.id || 0
  vendorOpen.value = true
}

async function saveVendor() {
  if (!String(vendorForm.merchant_full_name || '').trim()) {
    ElMessage.warning('客商全称必填')
    return
  }
  saving.value = true
  try {
    const fields = { ...vendorForm }
    delete fields.id
    await request<any>({
      url: vendorForm.id ? `/weida/master/vendors/${vendorForm.id}` : '/weida/master/vendors',
      method: vendorForm.id ? 'patch' : 'post',
      data: { fields },
    })
    ElMessage.success('已保存')
    vendorOpen.value = false
    await loadVendors()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function loadPorts() {
  try {
    const res = await request<any>({
      url: '/weida/master/ports',
      method: 'get',
      params: {
        keyword: portKeyword.value || undefined,
        page: portPage.value,
        page_size: portPageSize,
      },
    })
    ports.value = res.data?.data?.items ?? []
    portTotal.value = res.data?.data?.total ?? 0
  } catch (e: any) {
    ElMessage.error(e?.message || '读取目的港失败')
  }
}

function openPort(row: Record<string, any>) {
  portForm.id = row.id
  portForm.city_name = row.city_name || ''
  portForm.city_code = row.city_code || ''
  portForm.country_code = row.country_code || ''
  portForm.dep = row.dep || ''
  portForm.export_port = row.export_port || ''
  portForm.operation_requirement = row.operation_requirement || ''
  portOpen.value = true
}

async function savePort() {
  saving.value = true
  try {
    const fields = { ...portForm }
    delete fields.id
    delete fields.city_name
    delete fields.city_code
    await request<any>({
      url: `/weida/master/ports/${portForm.id}`,
      method: 'patch',
      data: { fields },
    })
    ElMessage.success('已保存')
    portOpen.value = false
    await loadPorts()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function loadOptions() {
  try {
    const res = await request<any>({
      url: '/weida/master/options',
      method: 'get',
      params: { category: optionCategory.value || undefined },
    })
    const items = res.data?.data?.items ?? []
    options.value = items.filter((o: Record<string, any>) => o.category !== 'destination_port')
    optionCategories.value = (res.data?.data?.categories ?? []).filter((c: string) => c !== 'destination_port')
  } catch (e: any) {
    ElMessage.error(e?.message || '读取选项失败')
  }
}

function openOption(row?: Record<string, any>) {
  optionForm.id = row?.id || 0
  optionForm.category = row?.category || optionCategory.value || 'transport_clause'
  optionForm.value = row?.value || ''
  optionForm.sort = row?.sort || 0
  optionForm.active = row ? row.active : 1
  optionOpen.value = true
}

async function saveOption() {
  if (!optionForm.category || !String(optionForm.value || '').trim()) {
    ElMessage.warning('分类和值必填')
    return
  }
  saving.value = true
  try {
    const fields = { ...optionForm }
    delete fields.id
    await request<any>({
      url: optionForm.id ? `/weida/master/options/${optionForm.id}` : '/weida/master/options',
      method: optionForm.id ? 'patch' : 'post',
      data: { fields },
    })
    ElMessage.success('已保存')
    optionOpen.value = false
    await loadOptions()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeOption(row: Record<string, any>) {
  try {
    await ElMessageBox.confirm(`删除选项「${row.value}」？`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await request<any>({ url: `/weida/master/options/${row.id}`, method: 'delete' })
    ElMessage.success('已删除')
    await loadOptions()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

function fleetText(value: any): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) {
    return value
      .map((v) => (typeof v === 'object' ? v?.text || v?.name || v?.id || '' : String(v)))
      .filter(Boolean)
      .join(', ')
  }
  if (typeof value === 'object') return value.text || value.name || value.id || JSON.stringify(value)
  return String(value)
}

function openFleetEdit(row: Record<string, any>) {
  fleetEditId.value = Number(row.id)
  const data: Record<string, string> = {}
  const fields: string[] = []
  for (const c of fleetColumns.value) {
    const value = row.data?.[c]
    if (value !== null && value !== undefined && typeof value === 'object') continue
    data[c] = value === null || value === undefined ? '' : String(value)
    fields.push(c)
  }
  fleetEditFields.value = fields
  fleetEditData.value = data
  fleetEditOpen.value = true
}

async function saveFleetEdit() {
  if (fleetSaving.value) return
  fleetSaving.value = true
  try {
    await request({
      url: `/weida/fleet/${fleetKind.value}/${fleetEditId.value}`,
      method: 'patch',
      data: { fields: fleetEditData.value },
    })
    fleetEditOpen.value = false
    await loadFleet()
    ElMessage.success('车队数据已保存')
  } finally {
    fleetSaving.value = false
  }
}

async function loadFleet() {
  fleetLoading.value = true
  try {
    const res = await request<any>({
      url: `/weida/fleet/${fleetKind.value}`,
      method: 'get',
      params: { keyword: fleetKeyword.value || undefined, page: fleetPage.value, page_size: 100 },
    })
    fleetRows.value = res.data?.data?.items ?? []
    fleetColumns.value = res.data?.data?.columns ?? []
    fleetTotal.value = Number(res.data?.data?.total || 0)
  } finally {
    fleetLoading.value = false
  }
}

async function loadAutomation() {
  automationLoading.value = true
  try {
    const [rules, logs] = await Promise.all([
      request<any>({ url: '/weida/automation/rules', method: 'get' }),
      request<any>({ url: '/weida/automation/logs', method: 'get', params: { limit: 30 } }),
    ])
    automationRules.value = rules.data?.data?.items ?? []
    automationLogs.value = logs.data?.data?.items ?? []
  } finally {
    automationLoading.value = false
  }
}

async function loadMailStatus() {
  mailLoading.value = true
  try {
    const res = await request<any>({ url: '/weida/mail/status', method: 'get' })
    mailStatus.value = res.data?.data?.items ?? []
    mailCounts.value = res.data?.data?.attachment_counts ?? {}
  } catch (e: any) {
    ElMessage.error(e?.message || '加载邮件状态失败')
  } finally {
    mailLoading.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'mail') void loadMailStatus()
  if (name === 'automation') void loadAutomation()
  if (name === 'fleet') void loadFleet()
}

onMounted(() => {
  void loadVendors()
  void loadPorts()
  void loadOptions()
})
</script>

<style scoped>
.md {
  min-height: 100%;
  padding: 24px 32px;
  color: hsl(33 17% 10%);
  background: hsl(40 36% 95%);
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Tahoma,
    'PingFang SC', 'Microsoft Yahei', Arial, sans-serif;
}
.md-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid hsl(38 25% 85%);
}
.md-head h2 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 500;
}
.md-head p {
  margin: 0;
  color: hsl(34 9% 44%);
  font-size: 13px;
}
.md-tabs {
  margin-top: 12px;
}
.md-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.md-count {
  color: #8a8378;
  font-size: 13px;
}
.md-pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.md-staff-card {
  max-width: 680px;
}
.md-staff-card h3 {
  margin: 0 0 8px;
}
.md-staff-card p {
  color: #8a8378;
  font-size: 13px;
  margin: 0 0 14px;
}
.md-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}
.md-wide {
  grid-column: span 2;
}
</style>
