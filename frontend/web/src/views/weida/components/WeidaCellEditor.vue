<template>
  <!-- 整格包一层并 stop 点击：编辑格子不该顺手把详情抽屉打开 -->
  <div ref="rootRef" class="cell-editor" data-editing-cell @click.stop @keydown.enter="onEnter" @keydown.esc="onEsc">
    <ElInput
      v-if="kind === 'text'"
      ref="controlRef"
      v-model="draftText"
      size="small"
      :placeholder="`填写${meta.name}`" />
    <ElInputNumber
      v-else-if="kind === 'number'"
      ref="controlRef"
      v-model="draftNumber"
      class="cell-number"
      size="small"
      :controls="false" />
    <ElSelect
      v-else-if="kind === 'single'"
      ref="controlRef"
      v-model="draftText"
      class="cell-fill"
      size="small"
      clearable
      filterable
      placeholder="未选择"
      @change="flush">
      <ElOption v-for="opt in meta.options" :key="opt" :label="opt" :value="opt" />
    </ElSelect>
    <ElSelect
      v-else-if="kind === 'multi'"
      ref="controlRef"
      v-model="draftArray"
      class="cell-fill"
      size="small"
      multiple
      clearable
      collapse-tags
      placeholder="未选择"
      @change="flush">
      <ElOption v-for="opt in meta.options" :key="opt" :label="opt" :value="opt" />
    </ElSelect>
    <ElDatePicker
      v-else-if="kind === 'date'"
      ref="controlRef"
      v-model="draftText"
      class="cell-fill"
      size="small"
      type="datetime"
      value-format="YYYY-MM-DD HH:mm:ss"
      placeholder="选择时间" />
    <ElSwitch v-else-if="kind === 'checkbox'" ref="controlRef" v-model="draftBool" size="small" @change="flush" />
    <!-- 人员：值走 open_id，显示走姓名（`userNames` 兜住通讯录里已没有的人） -->
    <UserSelect
      v-else-if="kind === 'user'"
      :role="role"
      :model-value="meta.multiple ? draftUser : (draftUser[0] ?? '')"
      :multiple="meta.multiple"
      :fallback-names="userNames"
      placeholder="未选择"
      @update:value="onUserChange" />
    <!-- 公式/查找/关联/附件等不在格子里编辑（附件走详情页上传） -->
    <span v-else class="cell-plain">{{ display }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElDatePicker, ElInput, ElInputNumber, ElOption, ElSelect, ElSwitch } from 'element-plus'
import { type FieldMeta, type UserOption, type UserRef } from '@/api/module_feishu/workbench'
import { cellKind } from '@/utils/feishu-cell'
import { formatFieldValue } from '@/utils/view-config'
import { loadUserOptions } from '@/utils/feishu-users'
import UserSelect from '@/views/module_feishu/components/UserSelect.vue'

defineOptions({ name: 'WeidaCellEditor' })

const props = defineProps<{
  /** 底层飞书字段的元数据（空列传一个合成的文本字段元数据） */
  meta: FieldMeta
  value: unknown
  /** 打开后是否自动聚焦（表格里点开一格要能直接打字） */
  autofocus?: boolean
  /** 人员字段所属的工作台（人员控件按它取候选） */
  role?: string
  /** 人员字段当前值的 open_id（`value` 里是给人看的姓名，提交却只能用 id） */
  userValue?: string[]
  /** 人员字段当前成员的 id→姓名，仅用于显示通讯录里已没有的人 */
  userNames?: Record<string, string>
}>()

const emit = defineEmits<{
  /**
   * 提交。
   *
   * 第二个参数只有人员字段会给：飞书收的是 open_id，而格子与列表要显示姓名，
   * 父组件得同时更新两份额外维护的原始标识，光有 id 会在界面上显示成一串 `ou_...`。
   */
  (e: 'commit', value: unknown, refs?: UserRef[]): void
  /** 编辑结束（回车、失焦、ESC），请求父组件收起本格编辑器 */
  (e: 'close'): void
}>()

const kind = computed(() => cellKind(props.meta))

/** 当前渲染出来的那个控件的实例，用来聚焦（一次只挂一个，所以共用一个 ref）。 */
const controlRef = ref<{ focus?: () => void } | null>(null)
/** 编辑器自己的根节点，用来判断这次点击是不是落在格子内。 */
const rootRef = ref<HTMLElement | null>(null)

const draftText = ref('')
const draftNumber = ref<number | undefined>(undefined)
const draftArray = ref<string[]>([])
const draftBool = ref(false)
/** 人员字段选中的 open_id（单选也存成数组，提交格式与飞书一致） */
const draftUser = ref<string[]>([])
/** 打开时的原始选择，用来判断「有没有真改过」——`props.value` 存的是姓名，比不了 */
const draftUserAtOpen = ref<string[]>([])
/** 通讯录（用来把 open_id 换成姓名） */
const userDirectory = ref<UserOption[]>([])

/** 落进输入框的初值（值变了要重新灌一次，否则列表刷新后格子还显示旧值）。 */
function syncFromProps() {
  const v = props.value
  draftText.value = v === null || v === undefined ? '' : String(v)
  draftNumber.value = typeof v === 'number' ? v : undefined
  draftArray.value = Array.isArray(v) ? v.map(String) : []
  draftBool.value = v === true
  draftUser.value = [...(props.userValue ?? [])]
  draftUserAtOpen.value = [...(props.userValue ?? [])]
}

syncFromProps()
watch(() => props.value, syncFromProps)

onMounted(async () => {
  document.addEventListener('mousedown', onDocumentMouseDown, true)
  if (kind.value === 'user' && props.role) {
    // 提交时要把 open_id 换成姓名，所以这里也要一份通讯录（与下拉共用同一个缓存）
    userDirectory.value = await loadUserOptions(props.role)
  }
  if (props.autofocus === false) return
  // 等控件真正挂进 DOM 再聚焦，否则 focus() 落在一个还没渲染的实例上
  nextTick(() => controlRef.value?.focus?.())
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentMouseDown, true)
})

/**
 * 点到格子外面就收工。
 *
 * 由组件自己监听而不是让父组件管，是因为同一时刻只挂着一个编辑器，它天然知道
 * 自己是谁；父组件要管就得跨组件抓实例，反而绕。
 *
 * 下拉/日期面板是 teleport 到 body 的，不是本组件的子孙节点，所以点选项时也会
 * 走到这里——`.el-popper` 里的一律放过，否则选一个值就把面板关掉了。
 */
function onDocumentMouseDown(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  if (!target?.closest) return
  if (rootRef.value?.contains(target)) return
  if (target.closest('.el-popper')) return
  flushAndClose()
}

const display = computed(() => formatFieldValue(props.value))

/** 归一成可比字符串：数字 12 与字符串 '12' 视为没变，避免无谓的生产写入。 */
function canon(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  if (Array.isArray(v)) return JSON.stringify(v.map((i) => String(i)))
  return String(v)
}

function currentValue(): unknown {
  switch (kind.value) {
    case 'text':
    case 'single':
    case 'date':
      return draftText.value
    case 'number':
      return draftNumber.value ?? null
    case 'multi':
      return [...draftArray.value]
    case 'checkbox':
      return draftBool.value
    case 'user':
      return [...draftUser.value]
    default:
      return props.value
  }
}

/** 人员字段的姓名：优先通讯录，其次记录自带的兜底（离职的人不在通讯录里）。 */
function userRefs(ids: string[]): UserRef[] {
  return ids.map((id) => ({
    id,
    name: userDirectory.value.find((u) => u.open_id === id)?.name ?? props.userNames?.[id] ?? id,
  }))
}

function onUserChange(value: unknown): void {
  draftUser.value = (Array.isArray(value) ? value : value ? [value] : []).map(String)
  flush()
}

/**
 * 已经提交过的值。
 *
 * 有的控件 `@change` 里先提交一次，随后父组件收起编辑器时又会走一次 `flushAndClose`；
 * 不记一笔的话同一格会往飞书发两遍 PUT。值一刷新就清掉，下次编辑照常提交。
 */
let lastEmitted = ''

function flush(): boolean {
  const next = currentValue()
  const key = canon(next)
  if (key === canon(baseline()) || key === lastEmitted) return false
  lastEmitted = key
  emit('commit', next, kind.value === 'user' ? userRefs(draftUser.value) : undefined)
  return true
}

/** 用来判断「改没改过」的基准值。人员字段的值是 open_id，而 `props.value` 是姓名。 */
function baseline(): unknown {
  return kind.value === 'user' ? draftUserAtOpen.value : props.value
}

/** 回车：提交并收起。 */
function onEnter() {
  flush()
  emit('close')
}

/** ESC：放弃本次编辑，不写飞书。 */
function onEsc() {
  emit('close')
}

/** 收起前先把没提交的值落下去（点格子外面时走这里）。 */
function flushAndClose() {
  flush()
  emit('close')
}
</script>

<style scoped>
.cell-editor {
  display: flex;
  align-items: center;
  width: 100%;
}

.cell-fill,
.cell-number {
  width: 100%;
}

.cell-plain {
  color: var(--el-text-color-regular);
  word-break: break-all;
}
</style>
