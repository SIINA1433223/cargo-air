<template>
  <!-- 可编辑：按飞书字段类型选控件 -->
  <ElInput
    v-if="isFormField && (field.type === FIELD_TYPE.TEXT || field.type === FIELD_TYPE.PHONE)"
    :model-value="textValue"
    :type="textarea ? 'textarea' : 'text'"
    :rows="textarea ? 2 : undefined"
    :autosize="textarea ? { minRows: 2, maxRows: 6 } : undefined"
    clearable
    :disabled="disabled"
    :placeholder="`请输入${field.name}`"
    @update:model-value="onTextInput"
    @change="emitNow" />
  <ElInputNumber
    v-else-if="isFormField && field.type === FIELD_TYPE.NUMBER"
    :model-value="numberValue"
    :controls="false"
    :disabled="disabled"
    class="full-width"
    :placeholder="`请输入${field.name}`"
    @update:model-value="onNumberInput"
    @change="emitNow" />
  <ElSelect
    v-else-if="isFormField && field.type === FIELD_TYPE.SINGLE_SELECT"
    :model-value="textValue"
    clearable
    filterable
    :disabled="disabled"
    class="full-width"
    :placeholder="`请选择${field.name}`"
    @update:model-value="emit('update:value', $event)">
    <ElOption v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
  </ElSelect>
  <ElSelect
    v-else-if="isFormField && field.type === FIELD_TYPE.MULTI_SELECT"
    :model-value="arrayValue"
    multiple
    clearable
    :disabled="disabled"
    class="full-width"
    :placeholder="`请选择${field.name}`"
    @update:model-value="emit('update:value', $event)">
    <ElOption v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
  </ElSelect>
  <ElDatePicker
    v-else-if="isFormField && field.type === FIELD_TYPE.DATE"
    :model-value="textValue"
    type="datetime"
    value-format="YYYY-MM-DD HH:mm:ss"
    :disabled="disabled"
    class="full-width"
    :placeholder="`请选择${field.name}`"
    @update:model-value="emit('update:value', $event)" />
  <ElSwitch
    v-else-if="isFormField && field.type === FIELD_TYPE.CHECKBOX"
    :model-value="boolValue"
    :disabled="disabled"
    @update:model-value="emit('update:value', $event)" />
  <!-- 附件：上传 / 下载 / 预览 / 删除（§10），只读时仅保留预览与下载 -->
  <AttachmentField
    v-else-if="field.type === FIELD_TYPE.ATTACHMENT"
    :value="value"
    :role="role"
    :table-id="tableId"
    :disabled="disabled || !field.editable"
    @update:value="emit('update:value', $event)" />
  <!--
    人员：值就是 open_id。`value` 用 `user_refs` 来的 id 而不是 `fields` 里的姓名——
    姓名改不了人（重名时更没法猜），提交回去的也必须是 id。单选/多选跟着原表字段走。
  -->
  <UserSelect
    v-else-if="isFormField && field.type === FIELD_TYPE.USER"
    :role="role"
    :model-value="value"
    :multiple="field.multiple"
    :disabled="disabled"
    :fallback-names="fallbackNames"
    :placeholder="`请选择${field.name}`"
    @update:value="emit('update:value', $event)" />
  <!--
    只读：公式 / 查找 / 自动编号，以及**关联字段**。
    关联（合出主单、合出分单）本期只展示——改关联走列表页的合并/拆出按钮，不在这里编辑。
  -->
  <span v-else class="readonly-value" :title="readonlyText">{{ readonlyText || '—' }}</span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FIELD_TYPE, type FieldMeta } from '@/api/module_feishu/workbench'
import AttachmentField from './AttachmentField.vue'
import UserSelect from './UserSelect.vue'

defineOptions({ name: 'RecordFieldControl' })

const props = defineProps<{
  field: FieldMeta
  value: unknown
  disabled?: boolean
  /** 人员字段所属的工作台（人员候选按它取）；附件字段也要它来定 base */
  role?: string
  /**
   * 附件字段挂在哪张表上。
   *
   * 只有**关联表的字段**才需要：飞书按 tableId 判附件权限，而关联表不属于本工作台，
   * 后端默认用工作台主表去取会 403。本工作台的字段不传即可（后端回落到自己的表）。
   */
  tableId?: string
  /** 人员字段已有成员的 id→姓名，仅供显示离职等不在通讯录里的人 */
  fallbackNames?: Record<string, string>
  /**
   * 文本/数字**失焦才回传**（默认逐键回传）。
   *
   * 详情页是「改完即写飞书」，逐键回传等于每敲一个字发一次 PUT。打开这个开关后
   * 组件内部自存草稿（见下面的 draftText/draftNumber），回车或失焦才 emit 一次。
   * 别的控件（下拉/日期/勾选/人员/附件）本来就是离散事件，不受影响。
   */
  commitOnChange?: boolean
  /**
   * 文本字段用**多行**输入框（备注这类可能要换行的）。
   *
   * 单行 `<input>` 按回车会直接触发 `change` 把值交出去，想换行只能另找地方写；
   * 换成 textarea 后回车就是换行，失焦才落值——正好是「备注」要的行为。
   */
  textarea?: boolean
}>()

const emit = defineEmits<{ (e: 'update:value', value: unknown): void }>()

/** 关联字段（单向/双向）没有本期的编辑控件，一律按只读显示 */
const LINK_TYPES: readonly number[] = [FIELD_TYPE.SINGLE_LINK, FIELD_TYPE.DUPLEX_LINK]

/** 该字段在本期是否真的提供输入控件（editable 且不是关联类型） */
const isFormField = computed(() => props.field.editable && !LINK_TYPES.includes(props.field.type))

/**
 * 文本/数字的**本地草稿**。
 *
 * 只在 `commitOnChange` 模式下被控件读；否则控件直接读 `props.value`。
 * 不自己存一份的话，受控输入框读的仍是父组件里的旧值（父组件要等失焦才更新），
 * 表现是「打一个字就被弹回去」，看着像控件坏了。
 */
const draftText = ref('')
const draftNumber = ref<number | undefined>(undefined)

function syncDraft(): void {
  draftText.value = props.value === null || props.value === undefined ? '' : String(props.value)
  draftNumber.value = typeof props.value === 'number' ? props.value : undefined
}

syncDraft()
// 只在值**真的变了**时重新灌草稿：否则正在打字时被父组件的重渲染冲掉
watch(() => props.value, syncDraft)

function onTextInput(value: string | number): void {
  const text = String(value)
  if (props.commitOnChange) draftText.value = text
  else emit('update:value', text)
}

function onNumberInput(value: number | undefined): void {
  if (props.commitOnChange) draftNumber.value = value
  else emit('update:value', value)
}

/** 失焦/回车：把草稿落下去（仅 commitOnChange 模式；另一模式早已逐键发过了）。 */
function emitNow(): void {
  if (!props.commitOnChange) return
  emit('update:value', props.field.type === FIELD_TYPE.NUMBER ? (draftNumber.value ?? null) : draftText.value)
}

const textValue = computed(() =>
  props.commitOnChange ? draftText.value : props.value === null || props.value === undefined ? '' : String(props.value)
)
const numberValue = computed(() =>
  props.commitOnChange ? draftNumber.value : typeof props.value === 'number' ? props.value : undefined
)
const boolValue = computed(() => props.value === true)
const arrayValue = computed(() => (Array.isArray(props.value) ? props.value : []))

const readonlyText = computed(() => {
  const v = props.value
  if (v === null || v === undefined || v === '') return ''
  if (props.field.type === FIELD_TYPE.CHECKBOX) return v === true ? '是' : '否'
  if (Array.isArray(v)) return v.length ? v.map(itemText).join('、') : ''
  if (typeof v === 'object') return itemText(v)
  return String(v)
})

/**
 * 附件字段（type 17）后端是原样透传的飞书对象数组
 * （``{file_token, name, size, url, ...}``），直接 join 会得到 "[object Object]"。
 * 这里取文件名；取不到名字时退回类型占位，避免把内部 token 暴露到界面上。
 */
function itemText(item: unknown): string {
  if (item === null || item === undefined) return ''
  if (typeof item !== 'object') return String(item)
  const obj = item as Record<string, unknown>
  const name = obj.name ?? obj.text
  if (typeof name === 'string' && name) return name
  return props.field.type === FIELD_TYPE.ATTACHMENT ? '（附件）' : ''
}
</script>

<style scoped>
.full-width {
  width: 100%;
}

.readonly-value {
  color: var(--el-text-color-regular);
  word-break: break-all;

  /*
    `pre-wrap`：只读的文本也得认换行。默认的 `normal` 会把 `\n` 折成一个空格，
    像「货物数据」那种一行一条的值会连成一长串，看着就是错的。`pre-wrap` 保留换行的同时
    照常按容器宽度折行（`pre` 不折，长尺寸串会顶出格子），配合 `break-all` 兜住超长串。
  */
  white-space: pre-wrap;
}
</style>
