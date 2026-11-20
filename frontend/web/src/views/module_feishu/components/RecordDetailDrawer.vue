<template>
  <ElDialog
    :model-value="visible"
    width="72%"
    top="4vh"
    draggable
    align-center
    class="record-detail-dialog"
    :title="drawerTitle"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <template #header>
      <div class="drawer-header">
        <span class="drawer-title">{{ drawerTitle }}</span>
        <ElTag v-if="mergeInfo" :type="mergeInfo.tagType" size="small" effect="dark">
          {{ mergeBadgeLabel(mergeInfo) }}
        </ElTag>
      </div>
    </template>

    <!-- 基础信息：常驻页头，与下方 Tab 无关，看单时不必先切 Tab -->
    <ElCard v-if="baseFields.length" class="base-card" shadow="never">
      <template #header>
        <div class="base-card-header">
          <span class="base-card-title">基础信息</span>
          <div class="base-card-badges">
            <ElTag :type="bookingBadge.type" size="small">{{ bookingBadge.label }}</ElTag>
            <ElTag :type="planBadge.type" size="small">{{ planBadge.label }}</ElTag>
            <ElTag :type="pickupBadge.type" size="small">{{ pickupBadge.label }}</ElTag>
          </div>
        </div>
      </template>
      <ElForm label-width="118px" label-position="right">
        <div class="form-grid form-grid-3">
          <ElFormItem
            v-for="field in baseFields"
            :key="`base-${field.name}`"
            :label="field.name"
            :class="itemClass(field)">
            <RecordFieldControl
              :field="field"
              :value="form[field.name]"
              :role="role"
              :fallback-names="userFallbackNames(field.name)"
              :disabled="!canUpdate"
              @update:value="form[field.name] = $event" />
          </ElFormItem>
        </div>
      </ElForm>
    </ElCard>

    <ElAlert
      v-if="mergeInfo && mergeInfo.memberIds.length > 2"
      class="merge-tip"
      type="info"
      :closable="false"
      show-icon
      :title="`该票属于合并组（共 ${mergeInfo.memberIds.length} 票）：${mergeInfo.visibleInvoiceNos.join('、') || '其他成员不在本页'}`" />

    <ElTabs v-model="activeTab" class="detail-tabs">
      <ElTabPane v-for="group in config?.groups ?? []" :key="group.label" :label="group.label" :name="group.label">
        <ElForm label-width="118px" label-position="right" class="detail-form">
          <div class="form-grid">
            <ElFormItem v-for="field in group.fields" :key="field.name" :label="field.name" :class="itemClass(field)">
              <RecordFieldControl
                :field="field"
                :value="form[field.name]"
                :role="role"
                :fallback-names="userFallbackNames(field.name)"
                :disabled="!canUpdate"
                @update:value="form[field.name] = $event" />
              <!-- §6.7：品名里出现「风扇」时逐项红色加粗示警（鉴定环节重点核对） -->
              <div v-if="fanSegments(field).length" class="fan-preview">
                <span v-for="(seg, i) in fanSegments(field)" :key="i" :class="{ 'fan-hit': seg.includes('风扇') }">
                  {{ seg }}
                </span>
              </div>
            </ElFormItem>
          </div>
        </ElForm>
      </ElTabPane>
      <!-- 方案计划下发：价格工作台「详情」里的一个 Tab，不是独立菜单页 -->
      <ElTabPane v-if="showPlanTab" label="方案计划下发" name="__plan__">
        <PlanDispatchTab
          :records="planRecords"
          :merge-info="mergeInfo"
          :master-field="config?.merge_master_field ?? '合出主单'"
          :hawb-field="config?.merge_hawb_field ?? '合出分单'"
          @plan-changed="emit('saved', { planState: $event })" />
      </ElTabPane>
    </ElTabs>

    <template #footer>
      <div class="drawer-footer">
        <ElCheckbox v-if="showSyncGroupOption" v-model="syncGroup" class="sync-group-check">
          同步订舱信息到同组合并组（{{ mergeInfo?.memberIds.length ?? 1 }} 票）
        </ElCheckbox>
        <div class="footer-actions">
          <ElButton @click="emit('update:visible', false)">关闭</ElButton>
          <ElButton v-if="canUpdate" type="primary" :loading="saving" :disabled="!dirtyCount" @click="handleSave">
            保存{{ dirtyCount ? `（${dirtyCount} 项改动）` : '' }}
          </ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import FeishuWorkbenchAPI, {
  FIELD_TYPE,
  MERGE_SYNC_FIELDS,
  type BitableRecord,
  type FieldMeta,
  type UserRef,
  type WorkbenchConfig,
} from '@/api/module_feishu/workbench'
import { bookingStatusBadge, pickupStatusBadge, planStatusBadge } from '@/utils/status-badge'
import { mergeBadgeLabel, type MergeGroupInfo } from '@/utils/merge-group'
import { useAuth } from '@/hooks/core/useAuth'
import RecordFieldControl from './RecordFieldControl.vue'
import PlanDispatchTab from './PlanDispatchTab.vue'

defineOptions({ name: 'FeishuRecordDetailDrawer' })

const props = defineProps<{
  visible: boolean
  role: string
  config: WorkbenchConfig | null
  record: BitableRecord | null
  mergeInfo?: MergeGroupInfo | null
  /** 合并组全部成员（由列表页从全量数据里取），方案计划下发按组合并主/分单展示 */
  groupRecords?: BitableRecord[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  /**
   * ``planState``：本次保存是否动了「计划」勾选框，以及改成什么。
   *
   * 计划下发走 B+A 方案：飞书只写当前票，同组其余票由列表页**本地**显示为已计划。
   * 所以保存完要把这个状态交给父组件，由它维护一份仅前端的覆盖，刷新后自然回落。
   */
  (e: 'saved', payload?: { planState?: boolean }): void
}>()

const { hasAuth } = useAuth()
const canUpdate = computed(() => hasAuth('module_feishu:record:update'))

const activeTab = ref('')
const saving = ref(false)
const syncGroup = ref(false)
const form = reactive<Record<string, any>>({})
/** 打开时的原始值，用于只提交改动的字段，避免把没动过的字段一起写回飞书 */
const original = new Map<string, unknown>()

const drawerTitle = computed(() => {
  const invoice = props.record?.fields['发票号']
  return invoice ? `记录详情 · ${invoice}` : '记录详情'
})

/** 页头基础信息字段（配置未定义时该卡片整块不渲染） */
const baseFields = computed(() => props.config?.base_fields ?? [])

/** 方案计划下发只在价格工作台出现，且始终基于当前票（未合并时就是它自己） */
const planRecords = computed<BitableRecord[]>(() =>
  props.groupRecords?.length ? props.groupRecords : props.record ? [props.record] : []
)
const showPlanTab = computed(() => props.role === 'sys2_price' && planRecords.value.length > 0)

const fields = computed(() => props.record?.fields ?? {})
const bookingBadge = computed(() => bookingStatusBadge(fields.value))
const planBadge = computed(() => planStatusBadge(fields.value))
const pickupBadge = computed(() => pickupStatusBadge(fields.value))

/** 会填长文本的字段占满整行，其余按列排；否则一行里塞不满反而更难读 */
const WIDE_FIELDS = new Set(['中文品名', '英文品名', '送货地址', '备注', '投保品名', '英文品名投保', '条款'])

function itemClass(field: FieldMeta): string {
  const wide =
    WIDE_FIELDS.has(field.name) ||
    field.type === FIELD_TYPE.MULTI_SELECT ||
    field.type === FIELD_TYPE.URL ||
    field.type === FIELD_TYPE.ATTACHMENT
  return wide ? 'form-item form-item-wide' : 'form-item'
}

/**
 * 只比「可编辑字段」（即 original 里登记过的），不走 Object.keys(form)——
 * form 里还放着只读字段的展示值，它们没有基准值，会被误判成改动。
 */
const dirtyFields = computed(() => {
  const changed: string[] = []
  for (const [key, before] of original) {
    const after = normalizeForCompare(form[key])
    if (JSON.stringify(before ?? null) !== JSON.stringify(after)) changed.push(key)
  }
  return changed
})
const dirtyCount = computed(() => dirtyFields.value.length)
const allowGroupSync = computed(() => props.config?.allow_group_sync !== false)
const showSyncGroupOption = computed(
  () =>
    allowGroupSync.value &&
    dirtyFields.value.some((f) => MERGE_SYNC_FIELDS.includes(f)) &&
    (props.mergeInfo?.memberIds.length ?? 1) > 1
)

/** 本次改动的订舱类字段（会整组同步的那几个） */
const sharedDirty = computed(() => dirtyFields.value.filter((f) => MERGE_SYNC_FIELDS.includes(f)))
const planChanged = computed(() => dirtyFields.value.includes('计划') && form['计划'] === true)

/**
 * 「有订舱单文件即已订舱」：只在**本次新加了文件**时自动勾上「订舱单」，
 * 不动历史记录里本来就没勾的（那些是业务有意留的状态）。
 */
watch(
  () => form['订舱单文件'],
  (files) => {
    const added = dirtyFields.value.includes('订舱单文件')
    if (added && Array.isArray(files) && files.length > 0 && form['订舱单'] !== true) {
      form['订舱单'] = true
      // 订舱单勾上即整组已订舱（§5.4），有合并组时默认同步
      if (allowGroupSync.value && (props.mergeInfo?.memberIds.length ?? 1) > 1) syncGroup.value = true
    }
  },
  { deep: true }
)

/** 手动勾「订舱单」也默认整组同步（§5.4：点击订舱单按钮后同组都显示已订舱） */
watch(
  () => form['订舱单'],
  (value) => {
    if (value === true && allowGroupSync.value && (props.mergeInfo?.memberIds.length ?? 1) > 1) {
      syncGroup.value = true
    }
  }
)

/** 计划预览里展示的订舱信息（§5.5：点击计划先弹预览，确认后才下发） */
const PLAN_PREVIEW_FIELDS = ['发票号', '分单号', 'ETD', '订舱代理', '航司', '操作日', '价格', '代理deadline', '启运港', '目的港']

function planPreviewHtml(): string {
  const rows = PLAN_PREVIEW_FIELDS.map((name) => {
    const value = form[name]
    const shown = value === null || value === undefined || value === '' ? '—' : String(value)
    return `<tr><td class="k">${name}</td><td>${escapeHtml(shown)}</td></tr>`
  }).join('')
  const peer = (props.mergeInfo?.memberIds.length ?? 1) - 1
  const groupNote =
    peer > 0
      ? `<p class="note">同组合并组另有 ${peer} 票：确认后<b>仅本票写入飞书</b>，同组其余票在系统中同步显示为「已计划」（B+A 方案）。</p>`
      : ''
  return `<table class="plan-preview">${rows}</table>${groupNote}`
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

async function confirmBeforeSave(): Promise<boolean> {
  // 已合并的票改订舱信息：先说清楚是哪几票合并了主单/分单（§4.11）
  if (sharedDirty.value.length && (props.mergeInfo?.memberIds.length ?? 1) > 1) {
    const peers = (props.mergeInfo?.memberIds.length ?? 1) - 1
    const kind =
      props.mergeInfo?.mergeType === 'master' ? '主单' : props.mergeInfo?.mergeType === 'hawb' ? '分单' : '主单/分单'
    const syncText = syncGroup.value
      ? `保存后这些订舱信息会同步到同组全部 ${peers + 1} 票。`
      : '本次仅修改当前票的订舱信息。'
    try {
      await ElMessageBox.confirm(
        `本票与另外 ${peers} 票合并了${kind}。${syncText}是否确定修改？`,
        '修改合并单订舱信息',
        { type: 'warning', confirmButtonText: '确定修改', cancelButtonText: '取消' }
      )
    } catch {
      return false
    }
  }

  if (planChanged.value) {
    try {
      await ElMessageBox.confirm(planPreviewHtml(), '下发计划预览', {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '确认下发',
        cancelButtonText: '取消',
        customClass: 'plan-preview-box',
      })
    } catch {
      return false
    }
  }
  return true
}

/**
 * 鉴定板块的品名逐项拆开，用于红色加粗标出含「风扇」的品名。
 * 只对操作工作台生效——其余工作台的中文品名不该被这段样式干扰。
 */
function fanSegments(field: FieldMeta): string[] {
  if (props.role !== 'operator' || field.name !== '中文品名') return []
  const raw = form[field.name]
  if (typeof raw !== 'string' || !raw.includes('风扇')) return []
  return raw
    .split(/[\n、,，;；]+/)
    .map((seg) => seg.trim())
    .filter(Boolean)
}

function normalizeForCompare(value: unknown): unknown {
  if (value === '' || value === undefined) return null
  if (Array.isArray(value) && value.length === 0) return null
  return value
}

/** 关联字段本期只读（改关联走列表页的合并/拆出），不进表单、也不参与脏值比较 */
const LINK_TYPES: readonly number[] = [FIELD_TYPE.SINGLE_LINK, FIELD_TYPE.DUPLEX_LINK]

function isFormField(field: FieldMeta): boolean {
  return field.editable && !LINK_TYPES.includes(field.type)
}

/**
 * 按字段类型给出表单初始值（复选框默认 false，多选默认空数组）。
 *
 * 人员字段**不能**用 `fields` 里的值——那里是给人看的姓名，而改人只能用 open_id
 * （重名时靠姓名根本认不出是谁，提交回去飞书也不收）。所以改取 `user_refs`。
 */
function initialValue(field: FieldMeta, raw: unknown, refs: UserRef[]): unknown {
  if (field.type === FIELD_TYPE.CHECKBOX) return raw === true
  if (field.type === FIELD_TYPE.USER) {
    const ids = refs.map((r) => r.id)
    return field.multiple ? ids : (ids[0] ?? '')
  }
  if (field.type === FIELD_TYPE.MULTI_SELECT) return Array.isArray(raw) ? raw : []
  if (raw === undefined || raw === null) return null
  return raw
}

/**
 * 人员字段已有成员的 id→姓名，只给下拉**显示**用。
 *
 * 通讯录里已经没有的人（离职）不在候选里，光有 id 会显示成 `ou_1c7e...`，看着像坏了。
 */
function userFallbackNames(name: string): Record<string, string> {
  return Object.fromEntries((props.record?.user_refs?.[name] ?? []).map((r) => [r.id, r.name]))
}

watch(
  () => [props.visible, props.record?.record_id] as const,
  ([visible]) => {
    if (!visible || !props.record) return
    activeTab.value = props.config?.groups[0]?.label ?? ''
    syncGroup.value = false
    original.clear()
    Object.keys(form).forEach((k) => delete form[k])
    const all = [...(props.config?.base_fields ?? []), ...(props.config?.groups ?? []).flatMap((g) => g.fields)]
    for (const field of all) {
      const raw = props.record.fields[field.name]
      if (isFormField(field)) {
        form[field.name] = initialValue(field, raw, props.record.user_refs?.[field.name] ?? [])
        original.set(field.name, normalizeForCompare(form[field.name]))
      } else {
        // 只读字段直接取原值，交给子组件格式化展示
        form[field.name] = raw
      }
    }
  },
  { immediate: true }
)

async function handleSave() {
  if (!props.record) return
  if (!(await confirmBeforeSave())) return
  saving.value = true
  const planDirty = dirtyFields.value.includes('计划')
  try {
    const payload: Record<string, unknown> = {}
    for (const name of dirtyFields.value) payload[name] = normalizeForCompare(form[name])
    // 操作工作台不允许整组同步，syncGroup 勾选状态也不会显示
    const sync = allowGroupSync.value && syncGroup.value
    await FeishuWorkbenchAPI.updateRecord(props.role, props.record.record_id, payload, sync)
    emit('saved', planDirty ? { planState: form['计划'] === true } : undefined)
    emit('update:visible', false)
  } catch (e) {
    console.error('[Feishu] 保存失败', e)
    ElMessage.error('保存失败，请重试')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.drawer-header {
  display: flex;
  gap: 10px;
  align-items: center;
}

.drawer-title {
  font-size: 16px;
  font-weight: 600;
}

.merge-tip {
  margin-bottom: 12px;
}

.detail-tabs {
  height: 100%;
}

.base-card {
  margin-bottom: 12px;
}

.base-card-header {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.base-card-title {
  font-size: 15px;
  font-weight: 600;
}

.base-card-badges {
  display: flex;
  gap: 6px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 16px;
}

/* 基础信息字段多且短，用三列排布，减少页头高度 */
.form-grid-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.form-item {
  margin-bottom: 14px;
}

.form-item-wide {
  grid-column: span 2;
}

.fan-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.fan-hit {
  font-weight: 700;
  color: var(--el-color-danger);
}

.form-grid-3 .form-item-wide {
  grid-column: span 3;
}

.full-width {
  width: 100%;
}

.readonly-value {
  color: var(--el-text-color-regular);
  word-break: break-all;
  white-space: pre-wrap;
}

.drawer-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.sync-group-check {
  font-size: 13px;
}

.footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

/* 弹窗被 teleport 到 body，scoped 选择器命不中，使用 :global */
:global(.record-detail-dialog .el-dialog__body) {
  max-height: 76vh;
  overflow-y: auto;
}

:global(.record-detail-dialog .el-dialog__header) {
  cursor: move;
  margin-right: 0;
}

/* 计划下发预览（ElMessageBox 挂在 body 上，样式也要 :global） */
:global(.plan-preview-box .plan-preview) {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

:global(.plan-preview-box .plan-preview td) {
  padding: 4px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

:global(.plan-preview-box .plan-preview td.k) {
  width: 110px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

:global(.plan-preview-box .note) {
  margin: 10px 0 0;
  color: var(--el-color-warning);
  font-size: 12px;
}
</style>
