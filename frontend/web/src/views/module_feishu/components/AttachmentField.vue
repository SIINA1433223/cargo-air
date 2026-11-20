<template>
  <div class="attachment-field">
    <div v-for="item in items" :key="item.file_token" class="attachment-row">
      <!--
        刚上传、还没保存的附件不给点：飞书只在附件**属于某条记录**时才允许下载，
        裸的 file_token 下载是 403（实测）。所以这里置灰，避免用户点了报错。
      -->
      <ElTooltip
        :content="isPending(item) ? '已上传，保存后即可预览与下载' : fileNameOf(item)"
        placement="top"
        :show-after="300">
        <span v-if="isPending(item)" class="attachment-name attachment-name-pending">
          <ElIcon><Document /></ElIcon>
          <span class="attachment-text">{{ shortFileName(fileNameOf(item)) }}</span>
        </span>
        <ElLink v-else class="attachment-name" type="primary" :underline="false" @click="openPreview(item)">
          <ElIcon><Document /></ElIcon>
          <span class="attachment-text">{{ shortFileName(fileNameOf(item)) }}</span>
        </ElLink>
      </ElTooltip>

      <span v-if="formatFileSize(item.size)" class="attachment-size">{{ formatFileSize(item.size) }}</span>
      <ElTag v-if="isPending(item)" size="small" type="warning" effect="plain">待保存</ElTag>

      <div class="attachment-actions">
        <ElButton link type="primary" size="small" :disabled="isPending(item)" @click="openPreview(item)">
          预览
        </ElButton>
        <ElButton link type="primary" size="small" :disabled="isPending(item)" @click="handleDownload(item)">
          下载
        </ElButton>
        <ElButton v-if="!disabled" link type="danger" size="small" @click="handleRemove(item)">删除</ElButton>
      </div>
    </div>

    <span v-if="!items.length" class="attachment-empty">—</span>

    <ElUpload
      v-if="!disabled"
      ref="uploadRef"
      class="attachment-upload"
      :auto-upload="false"
      :show-file-list="false"
      :disabled="uploading"
      :on-change="onFileChange">
      <ElButton size="small" :loading="uploading">上传附件</ElButton>
    </ElUpload>

    <AttachmentPreviewDialog
      v-model:visible="previewVisible"
      :attachment="previewTarget"
      :role="role"
      :table-id="tableId" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElLink, ElMessage, ElMessageBox, type UploadFile, type UploadInstance } from 'element-plus'
import FeishuWorkbenchAPI from '@/api/module_feishu/workbench'
import download from '@/utils/download'
import {
  addAttachments,
  fileNameOf,
  formatFileSize,
  removeAttachment,
  shortFileName,
  toAttachmentList,
  type AttachmentItem,
} from '@/utils/attachment'
import AttachmentPreviewDialog from './AttachmentPreviewDialog.vue'

defineOptions({ name: 'FeishuAttachmentField' })

const props = defineProps<{
  value: unknown
  /** true 时只读（无权限或字段本身不可编辑） */
  disabled?: boolean
  /** 附件所属的工作台；决定后端用哪个 base 取文件（威达附件必传） */
  role?: string
  /** 附件挂在哪张表上；关联表里的附件必传（飞书按 tableId 判权限） */
  tableId?: string
}>()

const emit = defineEmits<{ (e: 'update:value', value: AttachmentItem[]): void }>()

/** 与后端 service._MAX_UPLOAD_BYTES 保持一致，先在本地拦掉，省一次白传 */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

const uploadRef = ref<UploadInstance>()
const uploading = ref(false)
const previewVisible = ref(false)
const previewTarget = ref<AttachmentItem | null>(null)
/** 本次会话里上传成功的附件：保存前不可预览/下载，界面据此提示「待保存」 */
const pendingTokens = ref<Set<string>>(new Set())

const items = computed(() => toAttachmentList(props.value))

function isPending(item: AttachmentItem): boolean {
  return pendingTokens.value.has(item.file_token)
}

function openPreview(item: AttachmentItem) {
  previewTarget.value = item
  previewVisible.value = true
}

async function handleDownload(item: AttachmentItem) {
  try {
    const res = await FeishuWorkbenchAPI.downloadFile(item.file_token, props.role, props.tableId)
    download.saveAs(res.data, fileNameOf(item))
  } catch (e) {
    console.error('[Feishu] 附件下载失败', e)
    ElMessage.error('下载失败，请重试')
  }
}

async function handleRemove(item: AttachmentItem) {
  try {
    await ElMessageBox.confirm(`确定删除附件「${fileNameOf(item)}」？保存后生效。`, '删除附件', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  // 只改本地值，写飞书由抽屉的「保存」统一提交——删除是可撤销的误操作高发区
  emit('update:value', removeAttachment(items.value, item.file_token))
}

async function onFileChange(file: UploadFile) {
  const raw = file.raw
  uploadRef.value?.clearFiles() // 清掉内部列表，否则再选同一个文件不会再触发 change
  if (!raw) return

  if (raw.size > MAX_UPLOAD_BYTES) {
    ElMessage.error(`文件 ${(raw.size / 1024 / 1024).toFixed(1)}MB 超过 ${MAX_UPLOAD_BYTES / 1024 / 1024}MB 上限`)
    return
  }

  uploading.value = true
  try {
    const res = await FeishuWorkbenchAPI.uploadFile(raw, props.role)
    const uploaded = res.data.data
    const item: AttachmentItem = {
      file_token: uploaded.file_token,
      name: uploaded.name,
      size: uploaded.size,
      type: uploaded.type,
    }
    pendingTokens.value = new Set(pendingTokens.value).add(item.file_token)
    emit('update:value', addAttachments(items.value, [item]))
  } catch (e) {
    console.error('[Feishu] 附件上传失败', e)
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.attachment-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.attachment-row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

/*
  文件名**必须肯让步**：这一行里有「文件名 + 大小 + 预览/下载」，在详情页那种窄格子
  （一两百像素宽）里放不下。原先写死 flex-shrink:0，等于逼着整行撑到 320px 往外溢，
  压到右边一格上——看起来就是两个字段的内容叠在一起。让它收缩、名字走省略号。
*/
.attachment-name {
  display: inline-flex;
  flex-shrink: 1;
  gap: 4px;
  align-items: center;
  min-width: 0;
  max-width: 320px;
  font-size: 13px;
}

.attachment-name-pending {
  color: var(--el-color-warning);
  cursor: default;
}

.attachment-text {
  /* min-width:0 单独写一行不是多余的：flex 子项默认 min-width:auto 就是内容宽度，
     不归零的话上面那句 overflow:hidden 永远不会触发（行还是会往外顶）。 */
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-size {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.attachment-actions {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
  margin-left: auto;
}

.attachment-empty {
  color: var(--el-text-color-placeholder);
}

.attachment-upload {
  align-self: flex-start;
}
</style>
