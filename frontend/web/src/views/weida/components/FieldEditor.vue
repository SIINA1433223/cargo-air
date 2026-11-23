<template>
  <el-select
    v-if="field.editable && field.column === 'opportunity_person_in_charge'"
    :model-value="singlePersonValue"
    filterable
    clearable
    placeholder="选择商机负责人"
    style="width: 100%"
    @update:model-value="setSinglePerson">
    <el-option v-for="p in personOptions" :key="personKey(p)" :label="p.name" :value="personValue(p)" />
  </el-select>
  <el-select
    v-else-if="field.editable && field.column === 'responsible_route'"
    :model-value="personValues"
    multiple
    filterable
    collapse-tags
    placeholder="选择负责航线人员"
    style="width: 100%"
    @update:model-value="setPeople">
    <el-option v-for="p in personOptions" :key="personKey(p)" :label="p.name" :value="personValue(p)" />
  </el-select>
  <el-select
    v-else-if="field.editable && field.column === 'domestic_operation'"
    v-model="model"
    filterable
    clearable
    placeholder="选择国内操作人员"
    style="width: 100%">
    <el-option v-for="p in personOptions" :key="personKey(p)" :label="p.name" :value="p.name" />
  </el-select>
  <el-select
    v-else-if="field.editable && (field as any).options?.length"
    v-model="model"
    filterable
    clearable
    placeholder="请选择"
    style="width: 100%">
    <el-option v-for="o in (field as any).options" :key="o" :label="o" :value="o" />
  </el-select>
  <el-select
    v-else-if="field.editable && ['customer_name', 'supplier', 'foreign_agent_name'].includes(field.column)"
    v-model="model"
    filterable
    clearable
    placeholder="请选择"
    style="width: 100%">
    <el-option v-for="v in vendorOptions || []" :key="v.id" :label="v.name" :value="v.name" />
  </el-select>
  <el-switch
    v-else-if="field.editable && field.type === 'checkbox'"
    v-model="model"
    :active-value="1"
    :inactive-value="0" />
  <el-date-picker
    v-else-if="field.editable && field.type === 'date'"
    v-model="model"
    type="date"
    value-format="YYYY-MM-DD"
    placeholder="选择日期"
    style="width: 100%" />
  <div v-else-if="field.editable && field.type === 'datetime'" class="wb-dt-pair">
    <el-date-picker
      :model-value="datePart"
      type="date"
      value-format="YYYY-MM-DD"
      placeholder="选择日期"
      style="width: 55%"
      @update:model-value="setDatePart" />
    <el-time-select
      :model-value="timePart"
      start="00:00"
      step="00:30"
      end="23:30"
      placeholder="选择时间"
      style="width: 43%"
      @update:model-value="setTimePart" />
  </div>
  <el-input-number
    v-else-if="field.editable && field.type === 'number'"
    v-model="model"
    :controls="false"
    style="width: 100%" />
  <el-input
    v-else-if="field.editable && (field.type === 'textarea' || field.type === 'json')"
    v-model="model"
    type="textarea"
    :rows="3" />
  <el-input v-else-if="field.editable" v-model="model" />
  <span v-else class="field-readonly">{{ display(readonlyValue, field.type) }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

type Field = {
  column: string
  label: string
  type: string
  editable: boolean
  options?: string[]
}

defineOptions({ name: 'WeidaFieldEditor' })

type PersonOption = {
  id?: number
  name: string
  role?: string
  suda_user_id?: string | null
}

const props = defineProps<{
  field: Field
  readonlyValue?: any
  personOptions?: PersonOption[]
  vendorOptions?: Array<{ id: number; name: string }>
}>()
const model = defineModel<any>()

const datePart = computed(() => String(model.value || '').slice(0, 10))
const timePart = computed(() => {
  const raw = String(model.value || '')
  return raw.length >= 16 ? raw.slice(11, 16) : ''
})
function setDatePart(value: string | null) {
  model.value = `${value || ''} ${timePart.value || '00:00'}:00`
}
function setTimePart(value: string | null) {
  model.value = `${datePart.value || new Date().toISOString().slice(0, 10)} ${value || '00:00'}:00`
}

function personValue(p: PersonOption) {
  return p.suda_user_id || p.name
}

function personKey(p: PersonOption) {
  return `${p.id || ''}:${p.suda_user_id || p.name}`
}

function parsePeople(value: any): string[] {
  if (!value) return []
  if (Array.isArray(value)) return value.map(String)
  const raw = String(value)
  const ids = [...raw.matchAll(/\d{10,}/g)].map((m) => m[0])
  if (ids.length) return ids
  return raw
    .replace(/[{}()]/g, '')
    .split(/[,，、]/)
    .map((v) => v.trim())
    .filter(Boolean)
    .map((name) => {
      const hit = (props.personOptions || []).find((p) => p.name === name)
      return hit ? personValue(hit) : name
    })
}

const personValues = computed(() => parsePeople(model.value))
const singlePersonValue = computed(() => parsePeople(model.value)[0] || '')

function setSinglePerson(value: string) {
  model.value = value ? `(${value})` : ''
}

function setPeople(values: string[]) {
  model.value = values.length ? `{(${values.join('),(')})}` : ''
}

function display(v: any, type: string): string {
  if (v === null || v === undefined || v === '') return '—'
  if (type === 'checkbox') return v === 1 || v === true || v === '1' ? '是' : '否'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  if (type === 'date' && typeof v === 'string') return v.slice(0, 10)
  if (type === 'datetime' && typeof v === 'string') return v.replace('T', ' ').slice(0, 19)
  return String(v)
}
</script>

<style scoped>
.wb-dt-pair {
  display: flex;
  gap: 6px;
  width: 100%;
}
.field-readonly {
  display: block;
  min-height: 32px;
  padding: 5px 0;
  color: #2b2a28;
  word-break: break-all;
}
</style>
