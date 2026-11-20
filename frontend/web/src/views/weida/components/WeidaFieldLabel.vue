<template>
  <!--
    字段名 + 说明。说明**不摆在字段名旁边**：详情页的字段格子本来就只有一列宽，说明一长
    就被截成几个字，还把字段名挤走——两样都没读全。改成悬停在字段名上看提示。

    有说明的字段名底下加一条虚线（`has-desc`）：不加这点提示，没人知道这里能悬停。
  -->
  <ElTooltip :disabled="!description" :content="description" placement="top" :show-after="150">
    <span class="field-label">
      <span class="field-name" :class="{ 'is-form': variant === 'form', 'has-desc': description }">
        {{ name }}
      </span>
    </span>
  </ElTooltip>
</template>

<script setup lang="ts">
import { ElTooltip } from 'element-plus'

defineOptions({ name: 'WeidaFieldLabel' })

defineProps<{
  name: string
  description?: string
  /**
   * 摆在哪：`field` = 详情抽屉的字段格子（12px 次要色），`form` = 新建表单的 ElFormItem 标签
   * （14px 常规色，跟 Element Plus 的表单标签一个样）。
   */
  variant?: 'field' | 'form'
}>()
</script>

<style scoped>
/*
  **必须 `display: flex`（块级），不能是 `inline-flex`**。字段的控件有好几个是**行内**的
  （关联字段是裸 `<span>`、日期是 Element Plus 的 `.el-date-editor`，那个是 `inline-flex`），
  标签一旦也是行内的，就会跟值挤在同一行——「供应商」的值跑到标签右边、「航班」的日期框
  更是跟标签抢一行，跟别的字段对不齐。

  `width: fit-content` 把悬停区收窄到字段名这一小块：块级本来就撑满一整行，不收窄的话
  鼠标扫过整行都会弹提示。
*/
.field-label {
  display: flex;
  align-items: baseline;
  width: fit-content;
  margin-bottom: 2px;
}

.field-name {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.field-name.is-form {
  font-size: var(--el-font-size-base);
  line-height: var(--el-form-label-height, 32px);
  color: var(--el-text-color-regular);
}

.field-name.has-desc {
  cursor: help;
  border-bottom: 1px dashed var(--el-border-color);
}
</style>
