<template>
  <div class="plan-tab">
    <ElAlert
      v-if="peerCount > 0"
      class="plan-tip"
      type="info"
      :closable="false"
      show-icon
      :title="`本票属于合并组（共 ${records.length} 票）：下发计划只勾选当前票，同组其余票仅在本系统显示为已计划。`" />

    <section v-for="table in tables" :key="table.key" class="plan-table-block">
      <div class="plan-table-title">{{ table.label }}</div>
      <ElTable :data="rows[table.key]" size="small" border class="plan-table">
        <ElTableColumn prop="invoice" label="发票号" min-width="130" />
        <ElTableColumn prop="pieces" label="件数" width="80" />
        <ElTableColumn prop="weight" label="毛重" width="90" />
        <ElTableColumn prop="volume" label="体积" width="90" />
        <ElTableColumn prop="size" label="尺寸" width="120" />
        <ElTableColumn label="tracking 时效要求" min-width="180">
          <template #default="{ row }">
            <ElInput v-model="row.tracking" size="small" placeholder="tracking 时效要求" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="备注" min-width="160">
          <template #default="{ row }">
            <ElInput v-model="row.remark" size="small" placeholder="备注" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="计划" width="80" align="center">
          <template #default="{ row }">
            <ElCheckbox :model-value="row.plan" @change="onPlanChange(row, $event)" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="80" align="center">
          <template #default="{ row }">
            <ElButton link type="primary" :loading="row.saving" @click="saveRow(row)">保存</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import FeishuWorkbenchAPI, { type BitableRecord } from '@/api/module_feishu/workbench'
import type { MergeGroupInfo } from '@/utils/merge-group'

defineOptions({ name: 'FeishuPlanDispatchTab' })

const emit = defineEmits<{
  /** 计划状态变化：让列表页把同组合并组都显示为已计划（B+A 方案，仅前端） */
  (e: 'plan-changed', value: boolean): void
}>()

const props = defineProps<{
  records: BitableRecord[]
  mergeInfo?: MergeGroupInfo | null
  masterField: string
  hawbField: string
}>()

interface PlanRow {
  recordId: string
  invoice: string
  pieces: string
  weight: string
  volume: string
  size: string
  tracking: string
  remark: string
  plan: boolean
  saving: boolean
}

const rows = reactive<Record<'master' | 'hawb' | 'single', PlanRow[]>>({
  master: [],
  hawb: [],
  single: [],
})

const peerCount = computed(() => Math.max(0, props.records.length - 1))

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map((v) => (typeof v === 'string' ? v : (v?.name ?? ''))).join('、')
  if (typeof value === 'object') return ((value as { name?: string }).name ?? '') as string
  return String(value)
}

function toRow(rec: BitableRecord): PlanRow {
  return {
    recordId: rec.record_id,
    invoice: text(rec.fields['发票号']) || '（无发票号）',
    pieces: text(rec.fields['件数']),
    weight: text(rec.fields['毛重']),
    volume: text(rec.fields['体积']),
    size: text(rec.fields['尺码']),
    tracking: text(rec.fields['tracking时效要求']),
    remark: text(rec.fields['备注']),
    plan: rec.fields['计划'] === true,
    saving: false,
  }
}

/**
 * 一个并查集组里可能同时有主单组和分单组的成员（mixed），
 * 按成员各自的关联 id 拆成两张表；只有一种时另一张不渲染。
 */
const tables = computed(() => {
  const list: { key: 'master' | 'hawb' | 'single'; label: string }[] = []
  if (rows.master.length > 1) list.push({ key: 'master', label: '合并主单' })
  if (rows.hawb.length > 1) list.push({ key: 'hawb', label: '合并分单' })
  if (!list.length) list.push({ key: 'single', label: '本票' })
  return list
})

watch(
  () => props.records,
  (records) => {
    const masterIds = new Set<string>()
    const hawbIds = new Set<string>()
    for (const rec of records) {
      for (const id of rec.link_ids?.[props.masterField] ?? []) masterIds.add(id)
      for (const id of rec.link_ids?.[props.hawbField] ?? []) hawbIds.add(id)
    }
    const master = records.filter((rec) => masterIds.has(rec.record_id)).map(toRow)
    const hawb = records.filter((rec) => hawbIds.has(rec.record_id)).map(toRow)
    rows.master = master
    rows.hawb = hawb
    rows.single = records.map(toRow)
  },
  { immediate: true, deep: false }
)

function allRows(): PlanRow[] {
  return [...rows.master, ...rows.hawb, ...rows.single]
}

function onPlanChange(row: PlanRow, value: string | number | boolean) {
  return togglePlan(row, value === true)
}

async function togglePlan(row: PlanRow, value: boolean) {
  if (row.saving) return
  if (value) {
    try {
      await ElMessageBox.confirm(
        `确认对发票号 ${row.invoice} 下发计划？确认后状态改为「已计划」，且只勾选本票。`,
        '下发计划',
        { type: 'info', confirmButtonText: '确认下发', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
  }
  row.saving = true
  try {
    await FeishuWorkbenchAPI.updateRecord('sys2_price', row.recordId, { 计划: value }, false)
    syncRow(row.recordId, { plan: value })
    emit('plan-changed', value)
    ElMessage.success(value ? '已下发计划' : '已取消计划')
  } catch (e) {
    console.error('[Feishu] 计划下发失败', e)
    ElMessage.error('操作失败，请重试')
  } finally {
    row.saving = false
  }
}

async function saveRow(row: PlanRow) {
  row.saving = true
  try {
    await FeishuWorkbenchAPI.updateRecord('sys2_price', row.recordId, {
      'tracking时效要求': row.tracking || null,
      备注: row.remark || null,
    }, false)
    ElMessage.success('已保存')
  } catch (e) {
    console.error('[Feishu] 保存失败', e)
    ElMessage.error('保存失败，请重试')
  } finally {
    row.saving = false
  }
}

/** 同一票可能在主单/分单两张表里都出现，改一张时另一张跟着变 */
function syncRow(recordId: string, patch: Partial<PlanRow>) {
  for (const row of allRows()) {
    if (row.recordId === recordId) Object.assign(row, patch)
  }
}
</script>

<style scoped>
.plan-tip {
  margin-bottom: 12px;
}

.plan-table-block {
  margin-bottom: 16px;
}

.plan-table-title {
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 600;
}

.plan-table :deep(.el-table__cell) {
  padding: 3px 0;
}
</style>
