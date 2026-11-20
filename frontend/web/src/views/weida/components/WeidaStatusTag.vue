<template>
  <!--
    「单票状态」徽章。列表的固定列和详情抽屉的表头都用它——两处各写一份映射的话，
    改一次颜色要记得改两遍，漏掉的那处就一直是旧颜色。
  -->
  <ElTag v-if="badge" :type="badge.type" size="small" effect="light">{{ badge.label }}</ElTag>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElTag } from 'element-plus'
import { singleStatusBadge } from '@/utils/status-badge'

defineOptions({ name: 'WeidaStatusTag' })

const props = defineProps<{
  /** 后端算好的状态原文（`BitableRecord.single_status`）；空 = 没开这个开关，不渲染 */
  status?: string
}>()

const badge = computed(() => singleStatusBadge(props.status))
</script>
