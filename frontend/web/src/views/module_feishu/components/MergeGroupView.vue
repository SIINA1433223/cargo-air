<template>
  <div class="mg-wrap">
    <ElEmpty v-if="!blocks.length" description="暂无数据" />

    <ElCard
      v-for="(block, idx) in blocks"
      :key="block.group?.groupId ?? `unmerged-${idx}`"
      class="mg-card"
      shadow="never">
      <template #header>
        <div class="mg-header">
          <div class="mg-header-left">
            <ElTag v-if="block.group" :type="block.group.tagType" size="small" effect="dark">
              {{ typeLabel(block.group) }}
            </ElTag>
            <span class="mg-title">
              <template v-if="block.group">组 {{ block.group.repInvoiceNo || '（无发票号）' }}</template>
              <template v-else>未合并</template>
            </span>
            <span class="mg-count">共 {{ block.items.length }} 票</span>
          </div>
          <ElButton v-if="block.group" size="small" :disabled="!canUpdate" @click="emitUnmerge(block)">
            移出此合并组
          </ElButton>
        </div>
      </template>

      <div class="mg-rows">
        <button
          v-for="row in block.items"
          :key="row.record_id"
          type="button"
          class="mg-row"
          @click="emit('open-detail', row)">
          <span class="mg-cell mg-invoice">
            <i v-if="!readIds.has(row.record_id)" class="mg-dot" />
            {{ text(row.fields['发票号']) || '（无发票号）' }}
          </span>
          <span class="mg-cell mg-dept">{{ text(row.fields['部门']) }}</span>
          <span class="mg-cell mg-product">{{ product(row) }}</span>
          <span class="mg-cell mg-time">{{ text(row.fields[PICKUP_TIME_FIELD]) }}</span>
          <span class="mg-cell mg-status">
            <ElTag :type="bookingStatusBadge(row.fields).type" size="small">
              {{ bookingStatusBadge(row.fields).label }}
            </ElTag>
          </span>
        </button>
      </div>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import type { BitableRecord } from '@/api/module_feishu/workbench'
import { bookingStatusBadge } from '@/utils/status-badge'
import { PICKUP_TIME_FIELD, type MergeBlock, type MergeGroupInfo } from '@/utils/merge-group'

defineOptions({ name: 'FeishuMergeGroupView' })

defineProps<{
  blocks: MergeBlock[]
  readIds: Set<string>
  canUpdate: boolean
}>()

const emit = defineEmits<{
  (e: 'open-detail', record: BitableRecord): void
  (e: 'unmerge', record: BitableRecord): void
}>()

/** 组类型文案（对应旧系统 MergeGroupView 的 typeLabel） */
function typeLabel(group: MergeGroupInfo): string {
  if (group.mergeType === 'master') return '主单合并'
  if (group.mergeType === 'hawb') return '分单合并'
  return '合并组'
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  if (Array.isArray(value)) return value.length ? value.join('、') : ''
  return String(value)
}

/** 品名：中文优先，退回英文（两者都没有时显示占位） */
function product(row: BitableRecord): string {
  return text(row.fields['中文品名']) || text(row.fields['英文品名']) || '—'
}

/**
 * 「移出此合并组」作用于组内**最小发票号**的那一票——与合并时选主票的规则一致
 * （`buildMergeGroups` 的 repInvoiceNo 也是取最小发票号）。
 */
function emitUnmerge(block: MergeBlock): void {
  const rep = [...block.items]
    .filter((r) => text(r.fields['发票号']))
    .sort((a, b) => text(a.fields['发票号']).localeCompare(text(b.fields['发票号'])))[0]
  const target = rep ?? block.items[0]
  if (target) emit('unmerge', target)
}
</script>

<style scoped>
.mg-wrap {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mg-card {
  border: 1px solid var(--el-border-color-lighter);
}

.mg-header {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.mg-header-left {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.mg-title {
  font-size: 15px;
  font-weight: 600;
}

.mg-count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.mg-rows {
  display: flex;
  flex-direction: column;
}

.mg-row {
  display: flex;
  gap: 16px;
  align-items: center;
  width: 100%;
  padding: 8px 4px;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.mg-row:last-child {
  border-bottom: 0;
}

.mg-row:hover {
  background: var(--el-fill-color-light);
}

.mg-cell {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  white-space: nowrap;
}

.mg-invoice {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 150px;
  font-weight: 600;
}

.mg-dot {
  flex: none;
  width: 6px;
  height: 6px;
  background: var(--el-color-danger);
  border-radius: 50%;
}

.mg-dept {
  width: 80px;
  color: var(--el-text-color-regular);
}

.mg-product {
  flex: 1;
  min-width: 0;
}

.mg-time {
  width: 150px;
  color: var(--el-text-color-regular);
}

.mg-status {
  width: 84px;
}
</style>
