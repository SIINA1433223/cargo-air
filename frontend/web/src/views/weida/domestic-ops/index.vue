<template>
  <!--
    国内操作工作台。和国内客服是**同一个** `WeidaWorkbench`，只是 role 不同——
    索引页的能力（自定义列、筛选视图、就地编辑、填色、行高）两边天然一致。

    这个 role 的数据源是**两张表**：行来自订单表，列里掺进 iFreight 数据表按「所属订单」
    反查回来的那几列（后端 `WorkbenchConfig.related` 干的事，前端拿到的就是普通字段值，
    列名带 `iFreight·` 前缀、只读，见后端 `RelatedTable`）。
  -->
  <WeidaWorkbench role="wd_domestic_ops" />
</template>

<script setup lang="ts">
import WeidaWorkbench from '../components/WeidaWorkbench.vue'

defineOptions({ name: 'WeidaDomesticOps' })
</script>
