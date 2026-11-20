<template>
  <ElDrawer
    :model-value="visible"
    size="62%"
    :title="config?.create_label ?? '新建记录'"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <div v-loading="loading" class="create-body">
      <ElForm label-position="top">
        <ElFormItem
          v-for="field in form"
          :key="field.name"
          :class="{ 'is-wide': isWide(field) }"
          :required="field.required && field.editable"
          :error="errors[field.name]">
          <!-- 飞书里给字段写的说明：悬停在字段名上看，不再铺在标签下方（会把两行标签压散） -->
          <template #label>
            <WeidaFieldLabel :name="field.title" :description="field.description" variant="form" />
          </template>
          <!-- 字段多，两列排；长文本类（说明/地址）占整行 -->
          <div class="field-cell">
            <ElInput
              v-if="field.control === 'text'"
              :model-value="textOf(field)"
              clearable
              :placeholder="`请输入${field.title}`"
              @update:model-value="setValue(field.name, $event)" />
            <ElInputNumber
              v-else-if="field.control === 'number'"
              :model-value="numberOf(field)"
              :controls="false"
              class="full-width"
              :placeholder="`请输入${field.title}`"
              @update:model-value="setValue(field.name, $event)" />
            <ElSelect
              v-else-if="field.control === 'single'"
              :model-value="textOf(field)"
              clearable
              filterable
              class="full-width"
              :placeholder="`请选择${field.title}`"
              @update:model-value="setValue(field.name, $event)">
              <ElOption v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
            </ElSelect>
            <ElSelect
              v-else-if="field.control === 'multi'"
              :model-value="arrayOf(field)"
              multiple
              clearable
              collapse-tags
              filterable
              class="full-width"
              :placeholder="`请选择${field.title}`"
              @update:model-value="setValue(field.name, $event)">
              <ElOption v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
            </ElSelect>
            <ElDatePicker
              v-else-if="field.control === 'date'"
              :model-value="textOf(field)"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              class="full-width"
              :placeholder="`请选择${field.title}`"
              @update:model-value="setValue(field.name, $event)" />
            <ElSwitch
              v-else-if="field.control === 'checkbox'"
              :model-value="values[field.name] === true"
              @update:model-value="setValue(field.name, $event)" />
            <!--
              关联字段：候选记录来自它指向的那张表（「客户」→ 客户表 192 行）。
              显示主字段文本、提交 record_id 数组——飞书要的就是 id，不是文本。
            -->
            <ElSelect
              v-else-if="field.control === 'link'"
              :model-value="arrayOf(field)"
              multiple
              clearable
              collapse-tags
              filterable
              :loading="linkLoading[field.name]"
              class="full-width"
              :placeholder="`请选择${field.title}`"
              @update:model-value="setValue(field.name, $event)">
              <ElOption
                v-for="opt in linkOptions[field.name] ?? []"
                :key="opt.record_id"
                :label="opt.text || '(未命名)'"
                :value="opt.record_id" />
            </ElSelect>
            <AttachmentField
              v-else-if="field.control === 'attachment'"
              :value="values[field.name]"
              :role="role"
              @update:value="setValue(field.name, $event)" />
            <!--
              人员字段：候选来自飞书通讯录，提交的是 open_id。
              飞书网页里点这个格子只能从它自己的选人浮层里挑，没有别处可填——所以本系统
              必须能选，不能让用户「去飞书填」。单选/多选跟着飞书字段属性走
              （本表「负责航线」多选、「商机负责人」单选），一律放开的话单选字段选两个人
              会被飞书整单拒掉。
            -->
            <UserSelect
              v-else-if="field.control === 'user'"
              :role="role"
              :model-value="field.multiple ? arrayOf(field) : textOf(field)"
              :multiple="field.multiple"
              :placeholder="`请选择${field.title}`"
              @update:value="setValue(field.name, $event)" />
            <!-- 只读：查找引用/公式等飞书自己算的，照飞书表单一样摆在原位但不可改 -->
            <span v-else class="readonly-hint">
              需在飞书里填
              <span v-if="field.ui_type" class="readonly-type">（{{ field.ui_type }}）</span>
            </span>
          </div>
        </ElFormItem>

        <ElEmpty
          v-if="!loading && !form.length"
          class="form-empty"
          description="这张表没有配置新建表单"
          :image-size="60" />
      </ElForm>
    </div>

    <template #footer>
      <div class="create-footer">
        <span v-if="error" class="create-error">{{ error }}</span>
        <div class="create-actions">
          <ElButton @click="emit('update:visible', false)">取消</ElButton>
          <ElButton type="primary" :loading="submitting" :disabled="!form.length" @click="submit">
            {{ config?.create_label ?? '新建记录' }}
          </ElButton>
        </div>
      </div>
    </template>
  </ElDrawer>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  ElButton,
  ElDatePicker,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElSwitch,
} from 'element-plus'
import FeishuWorkbenchAPI, {
  type CreateFormField,
  type LinkOption,
  type WorkbenchConfig,
} from '@/api/module_feishu/workbench'
import AttachmentField from '@/views/module_feishu/components/AttachmentField.vue'
import UserSelect from '@/views/module_feishu/components/UserSelect.vue'
import WeidaFieldLabel from './WeidaFieldLabel.vue'
import { seedFormValues } from '@/utils/create-form'

defineOptions({ name: 'WeidaRecordCreateDrawer' })

const props = defineProps<{
  visible: boolean
  /** 工作台标识（决定往哪个 base 的哪张表写） */
  role: string
  config: WorkbenchConfig | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  /** 建好了，调用方据此刷新列表 */
  (e: 'created', recordId: string): void
}>()

/** 表单字段定义，顺序即飞书表单里的顺序 */
const form = ref<CreateFormField[]>([])
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
/** 逐字段的必填提示（提交时才算，别一边填一边红） */
const errors = ref<Record<string, string>>({})

const values = ref<Record<string, unknown>>({})
/** 关联字段的候选记录：字段名 → 选项 */
const linkOptions = ref<Record<string, LinkOption[]>>({})
const linkLoading = ref<Record<string, boolean>>({})

/** 说明/地址这类长文本占整行，短字段两列并排 */
const WIDE_FIELDS = new Set(['派送地址', '货源地', '客户核心关注点', '路线要求', '尺寸（长*宽*高 * 数量）'])
function isWide(field: CreateFormField): boolean {
  return WIDE_FIELDS.has(field.name)
}

function textOf(field: CreateFormField): string {
  const v = values.value[field.name]
  return v === null || v === undefined ? '' : String(v)
}

function numberOf(field: CreateFormField): number | undefined {
  const v = values.value[field.name]
  return typeof v === 'number' ? v : undefined
}

function arrayOf(field: CreateFormField): string[] {
  const v = values.value[field.name]
  return Array.isArray(v) ? (v as string[]) : []
}

function setValue(name: string, value: unknown): void {
  values.value = { ...values.value, [name]: value }
  if (errors.value[name]) {
    const next = { ...errors.value }
    delete next[name]
    errors.value = next
  }
}

/**
 * 关联字段的候选记录一次拉齐，之后交给 ElSelect 自己过滤。
 *
 * 候选表都是几百行的主数据表（客户表 192 行），一次拉回来当场筛，比每敲一个字就发一次
 * 请求跟手得多；接口上的 `search` 留给将来候选表变大时用。
 */
async function loadLinkOptions(fields: CreateFormField[]): Promise<void> {
  const links = fields.filter((f) => f.control === 'link')
  await Promise.all(
    links.map(async (field) => {
      linkLoading.value = { ...linkLoading.value, [field.name]: true }
      try {
        const res = await FeishuWorkbenchAPI.getLinkOptions(props.role, field.name)
        linkOptions.value = { ...linkOptions.value, [field.name]: res.data.data }
      } catch {
        // 拉不到候选就让下拉空着，别把整个表单拦住——其他字段照样能填
        linkOptions.value = { ...linkOptions.value, [field.name]: [] }
      } finally {
        linkLoading.value = { ...linkLoading.value, [field.name]: false }
      }
    })
  )
}

// 每次打开都重新拉定义并清空：上一次填了一半被取消的内容不该带到下一次
watch(
  () => props.visible,
  async (v) => {
    if (!v) return
    form.value = []
    values.value = {}
    errors.value = {}
    linkOptions.value = {}
    error.value = ''
    submitting.value = false
    loading.value = true
    try {
      const res = await FeishuWorkbenchAPI.getCreateForm(props.role)
      form.value = res.data.data
      // 预选值来自表单定义，得等定义回来才知道要填什么——所以放在这里而不是上面清空那几行
      values.value = seedFormValues(form.value)
      // 人员候选由 UserSelect 自己取（同一工作台在进程内共享），这里只管关联字段
      await loadLinkOptions(form.value)
    } catch {
      // 具体报错由拦截器弹出；这里保持空表单，用户再打开一次即可
      form.value = []
    } finally {
      loading.value = false
    }
  },
  { immediate: true }
)

/** 空值不发：文本字段送 `''` 会在飞书里真写一个空串；勾选框送 `false` 等于替用户做决定。 */
function isEmpty(v: unknown): boolean {
  if (v === null || v === undefined || v === '') return true
  return Array.isArray(v) && v.length === 0
}

/** 必填校验只看**本系统能填的**字段：只读的必填项（如人员）我们替不了用户。 */
function validate(): boolean {
  const next: Record<string, string> = {}
  const missing: string[] = []
  for (const field of form.value) {
    if (!field.required || !field.editable) continue
    if (!isEmpty(values.value[field.name])) continue
    next[field.name] = '必填'
    missing.push(field.title)
  }
  errors.value = next
  if (missing.length) {
    error.value = `还有必填项没填：${missing.join('、')}`
    return false
  }
  error.value = ''
  return true
}

async function submit(): Promise<void> {
  if (!validate()) return

  const fields: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values.value)) {
    if (!isEmpty(value)) fields[name] = value
  }

  submitting.value = true
  try {
    const res = await FeishuWorkbenchAPI.createRecord(props.role, fields)
    emit('created', res.data.data.record_id)
    emit('update:visible', false)
    // 成功提示由 http 拦截器统一弹（AGENTS.md：非 GET 成功不在业务代码里手动提示）
  } catch {
    // 具体报错（字段校验、无权限等）由拦截器弹出，这里只留住抽屉别丢用户填的内容
    error.value = '新建失败，请按提示修改后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.create-body {
  min-height: 240px;
}

.create-body :deep(.el-form) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
}

.create-body :deep(.el-form-item) {
  display: block;
  margin-bottom: 16px;
}

.create-body :deep(.el-form-item__label) {
  display: block;
  width: auto;
  height: auto;
  padding: 0 0 4px;
  line-height: 22px;
  text-align: left;
}

.create-body :deep(.el-form-item__content) {
  display: block;
  margin-left: 0;
}

/* 长文本类字段与空态占整行 */
.create-body :deep(.el-form-item.is-wide),
.create-body :deep(.form-empty) {
  grid-column: span 2;
}

.full-width {
  width: 100%;
}

.readonly-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.readonly-type {
  color: var(--el-text-color-placeholder);
}

.create-footer {
  display: flex;
  gap: 12px;
  align-items: center;
}

.create-error {
  color: var(--el-color-danger);
}

.create-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
