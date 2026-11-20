<template>
  <ElSelect
    :model-value="modelValue"
    :multiple="multiple"
    :disabled="disabled"
    clearable
    collapse-tags
    filterable
    :loading="loading"
    class="full-width"
    :placeholder="placeholder"
    @update:model-value="emit('update:value', $event)">
    <ElOption v-for="opt in options" :key="opt.open_id" :label="opt.name" :value="opt.open_id" />
  </ElSelect>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElOption, ElSelect } from 'element-plus'
import { type UserOption } from '@/api/module_feishu/workbench'
import { loadUserOptions } from '@/utils/feishu-users'

defineOptions({ name: 'UserSelect' })

const props = defineProps<{
  /** 工作台标识——人员候选按租户取，但接口挂在工作台下（沿用同一套权限） */
  role: string
  /** 当前值：多选是 open_id 数组，单选是单个 open_id */
  modelValue: unknown
  /** 原表里这个字段是单选还是多选；单选放开多选会被飞书整单拒掉 */
  multiple?: boolean
  disabled?: boolean
  placeholder?: string
  /**
   * 记录里已有的成员，仅用于**显示**。
   *
   * 通讯录里已经没有的人（离职）不在候选里，光有 id 的下拉会显示成 `ou_1c7e...`，
   * 看着像坏了；这里用记录自带的姓名兜住，但**不会**把它们混进候选——离职的人
   * 不该还能被选上。
   */
  fallbackNames?: Record<string, string>
}>()

const emit = defineEmits<{ (e: 'update:value', value: unknown): void }>()

const directory = ref<UserOption[]>([])
const loading = ref(false)

const options = computed<UserOption[]>(() => {
  const known = new Set(directory.value.map((u) => u.open_id))
  const extra = Object.entries(props.fallbackNames ?? {})
    .filter(([id]) => id && !known.has(id))
    .map(([id, name]) => ({ open_id: id, name }))
  return [...directory.value, ...extra]
})

/**
 * 候选一次拉齐，之后交给 ElSelect 自己过滤。
 *
 * 整租户只有十几个人，一次拉回来当场筛比每敲一个字就发一次请求跟手得多。
 * 拉不到就让下拉空着，别把整个表单拦住。同一个工作台的候选由 `loadUserOptions`
 * 在进程内共享，同页面多个人员字段不会各拉一遍。
 */
async function load(): Promise<void> {
  if (!props.role) return
  loading.value = true
  try {
    directory.value = await loadUserOptions(props.role)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.role, load)
</script>

<style scoped>
.full-width {
  width: 100%;
}
</style>
