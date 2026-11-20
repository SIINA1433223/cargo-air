<template>
  <!--
    航线价格工作台。和国内客服是**同一个** `WeidaWorkbench`，只是 role 不同——
    索引页的能力（自定义列、筛选视图、就地编辑、填色、行高）两边天然一致，
    差别全在后端配置（数据源、列、排序、详情分组）里。
  -->
  <WeidaWorkbench role="wd_pricing" />
</template>

<script setup lang="ts">
import WeidaWorkbench from '../components/WeidaWorkbench.vue'

defineOptions({ name: 'WeidaPricing' })
</script>
