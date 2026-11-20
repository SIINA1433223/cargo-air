<template>
  <ElDialog
    :model-value="visible"
    title="筛选"
    width="720px"
    top="8vh"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <div class="flt-body">
      <div class="flt-logic">
        <span class="flt-label">条件之间：</span>
        <ElRadioGroup v-model="draftConjunction" size="small">
          <ElRadioButton value="and">且（同时满足）</ElRadioButton>
          <ElRadioButton value="or">或（满足其一）</ElRadioButton>
        </ElRadioGroup>
        <span class="flt-hint">共 {{ draft.length }} 个条件</span>
      </div>

      <div class="flt-rows">
        <div v-for="(cond, i) in draft" :key="cond.id" class="flt-row">
          <ElSelect v-model="cond.field" class="flt-field" size="small" filterable placeholder="选择字段">
            <ElOption v-for="opt in fieldOptions" :key="opt.value" :label="opt.label" :value="opt.value">
              <span>{{ opt.label }}</span>
              <span v-if="opt.tag" class="flt-opt-tag">{{ opt.tag }}</span>
            </ElOption>
          </ElSelect>

          <ElSelect v-model="cond.operator" class="flt-op" size="small">
            <ElOption v-for="op in FILTER_OPERATORS" :key="op.value" :label="op.label" :value="op.value" />
          </ElSelect>

          <!-- 为空/非空不看值，位置留空但保持列宽，行才不会跳 -->
          <ElInput
            v-if="needsValue(cond.operator)"
            v-model="cond.value"
            class="flt-value"
            size="small"
            clearable
            placeholder="输入要比较的值"
            @keyup.enter="apply" />
          <span v-else class="flt-value flt-value-empty">—</span>

          <ElButton link type="danger" size="small" :disabled="draft.length <= 1" @click="removeRow(i)">删除</ElButton>
        </div>

        <ElEmpty v-if="!draft.length" description="还没有条件" :image-size="50" />
      </div>

      <ElButton size="small" :icon="Plus" @click="addRow">添加条件</ElButton>

      <div class="flt-save">
        <ElInput v-model="viewName" class="flt-save-input" size="small" placeholder="视图名（如「我的待报价」）" />
        <ElButton size="small" :disabled="!draft.length" @click="saveView">保存为视图</ElButton>
      </div>
      <div class="flt-tip">
        「包含」按子串匹配（不区分大小写）；多选/人员类字段按顿号连起来匹配。 筛选范围含你自定义的拼接列。
      </div>

      <div v-if="views.length" class="flt-views">
        <span class="flt-label">已存视图：</span>
        <ElTag
          v-for="v in views"
          :key="v.id"
          class="flt-view-tag"
          :type="v.id === activeViewId ? 'primary' : 'info'"
          closable
          @click="selectView(v.id)"
          @close="emit('remove-view', v.id)">
          {{ v.name }}（{{ v.conditions.length }}）
        </ElTag>
      </div>
    </div>

    <template #footer>
      <div class="flt-footer">
        <ElButton @click="clearAll">清空</ElButton>
        <div class="flt-footer-actions">
          <ElButton @click="emit('update:visible', false)">取消</ElButton>
          <ElButton type="primary" @click="apply">应用</ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ElButton,
  ElDialog,
  ElEmpty,
  ElInput,
  ElOption,
  ElRadioButton,
  ElRadioGroup,
  ElSelect,
  ElTag,
} from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { FieldMeta } from '@/api/module_feishu/workbench'
import {
  FILTER_OPERATORS,
  blankFieldName,
  type FilterCondition,
  type FilterOperator,
  type FilterView,
  type ViewColumn,
} from '@/utils/view-config'

defineOptions({ name: 'WeidaFilterDialog' })

const props = defineProps<{
  visible: boolean
  /** 本表全部字段——「筛选内容涵盖所有个人字段」的一半 */
  fields: FieldMeta[]
  /** 当前视图的列；其中的拼接列也要能当筛选对象 */
  columns: ViewColumn[]
  conditions: FilterCondition[]
  conjunction: 'and' | 'or'
  views: FilterView[]
  activeViewId?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'apply', payload: { conditions: FilterCondition[]; conjunction: 'and' | 'or' }): void
  (e: 'save-view', payload: { name: string; conditions: FilterCondition[]; conjunction: 'and' | 'or' }): void
  (e: 'select-view', id: string): void
  (e: 'remove-view', id: string): void
}>()

const draft = ref<FilterCondition[]>([])
const draftConjunction = ref<'and' | 'or'>('and')
const viewName = ref('')

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

// 打开时把当前条件拷进草稿：编辑期间不影响列表，取消即丢弃
watch(
  () => props.visible,
  (v) => {
    if (!v) return
    draft.value = props.conditions.map((c) => ({ ...c }))
    draftConjunction.value = props.conjunction
    viewName.value = ''
  },
  { immediate: true }
)

/**
 * 空列在飞书里的真名 → 用户起的显示名。
 *
 * 真名是 `{列名}_{用户名}_{短码}`，摆到筛选下拉里就是一串看不懂的乱码，得换回来。
 * 注意 `value` 仍是真名：条件的 `field` 要拿去查行数据（`row.fields[field]`），
 * 换成显示名就一条都取不到值了。
 */
const blankLabels = computed(() => {
  const m = new Map<string, string>()
  for (const c of props.columns) {
    if (c.kind === 'blank') m.set(blankFieldName(c), c.label)
  }
  return m
})

/**
 * 可筛字段 = 全表字段 + 用户自定义的拼接列。
 *
 * 空列不需要单列出来：它就是表里的一个真字段，已经在 `fields` 里了，这里只把
 * 它的名字换回显示名。
 * 拼接列用它的 label 当 key——若某个拼接列的列名恰好与真字段重名，
 * 取值时以拼接收列为准（见 WeidaWorkbench 的 `valueOf`）。
 */
const fieldOptions = computed(() => {
  const table = props.fields
    .filter((f) => f.type !== 3001) // 按钮字段没有值
    .map((f) => ({ value: f.name, label: blankLabels.value.get(f.name) ?? f.name, tag: '' }))
  const names = new Set(table.map((t) => t.value))
  const templates = props.columns
    .filter((c): c is Extract<ViewColumn, { kind: 'template' }> => c.kind === 'template')
    .filter((c) => !names.has(c.label))
    .map((c) => ({ value: c.label, label: c.label, tag: '拼接' }))
  return [...table, ...templates]
})

function needsValue(op: FilterOperator): boolean {
  return FILTER_OPERATORS.find((o) => o.value === op)?.needsValue ?? false
}

function addRow() {
  draft.value.push({ id: newId(), field: '', operator: 'contains', value: '' })
}

function removeRow(i: number) {
  draft.value.splice(i, 1)
}

function clearAll() {
  draft.value = []
  draftConjunction.value = 'and'
}

/** 丢掉没选字段的半成品条件，别把它们当成「永远不命中」的隐式过滤器。 */
function validConditions(): FilterCondition[] {
  return draft.value.filter((c) => c.field)
}

function apply() {
  emit('apply', { conditions: validConditions(), conjunction: draftConjunction.value })
  emit('update:visible', false)
}

function saveView() {
  const name = viewName.value.trim()
  if (!name) {
    viewName.value = ''
    return
  }
  const conditions = validConditions()
  if (!conditions.length) return
  emit('save-view', { name, conditions, conjunction: draftConjunction.value })
  viewName.value = ''
}

function selectView(id: string) {
  emit('select-view', id)
}
</script>

<style scoped>
.flt-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.flt-logic {
  display: flex;
  gap: 8px;
  align-items: center;
}

.flt-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.flt-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.flt-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 40vh;
  overflow-y: auto;
}

.flt-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.flt-field {
  width: 200px;
}

.flt-op {
  width: 100px;
}

.flt-value {
  flex: 1;
}

.flt-value-empty {
  color: var(--el-text-color-placeholder);
}

.flt-opt-tag {
  float: right;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.flt-save {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.flt-save-input {
  width: 220px;
}

.flt-tip {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.flt-views {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.flt-view-tag {
  cursor: pointer;
}

.flt-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.flt-footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
