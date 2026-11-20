<template>
  <ElDialog
    :model-value="visible"
    :title="attachment ? fileNameOf(attachment) : '附件预览'"
    width="82%"
    top="5vh"
    append-to-body
    destroy-on-close
    class="attachment-preview-dialog"
    @update:model-value="emit('update:visible', $event)"
    @closed="cleanup">
    <div v-loading="loading" class="preview-body">
      <ElAlert v-if="error" type="error" :closable="false" show-icon :title="error" />

      <!-- PDF：浏览器自带阅读器（§10.1 后端代理 + iframe 预览） -->
      <iframe v-else-if="kind === 'pdf' && objectUrl" :src="objectUrl" class="preview-frame" title="附件预览" />

      <!-- 图片：Image 组件展示，点开可放大 -->
      <div v-else-if="kind === 'image' && objectUrl" class="preview-image">
        <ElImage :src="objectUrl" :preview-src-list="[objectUrl]" fit="contain" />
      </div>

      <!--
        表格：SheetJS 解析成网格后自己渲染。
        不走 `sheet_to_html` + `v-html`——单元格内容来自用户上传的文件，那是一条 XSS 通道。
      -->
      <div v-else-if="kind === 'excel' && !loading" class="preview-sheet">
        <template v-if="activeCells.length">
          <!-- 多工作表才摆这一排，单表的文件（绝大多数报价单）不占这一行 -->
          <div v-if="sheets.length > 1" class="sheet-tabs">
            <ElRadioGroup v-model="activeSheet" size="small">
              <ElRadioButton v-for="(sheet, i) in sheets" :key="sheet.name || i" :value="i">
                {{ sheet.name || `工作表${i + 1}` }}
              </ElRadioButton>
            </ElRadioGroup>
          </div>

          <div class="sheet-scroll">
            <table class="sheet-table">
              <tbody>
                <tr v-for="(row, r) in activeCells" :key="r">
                  <template v-for="(cell, c) in row" :key="c">
                    <td v-if="cell" :colspan="cell.colspan" :rowspan="cell.rowspan">{{ cell.text }}</td>
                  </template>
                </tr>
              </tbody>
            </table>
          </div>

          <p v-if="truncated" class="sheet-hint">
            表格较大，只显示前 {{ MAX_PREVIEW_ROWS }} 行 / {{ MAX_PREVIEW_COLS }} 列，完整内容请下载查看
          </p>
        </template>
        <ElEmpty v-else description="这个表格里没有可显示的内容，请下载查看" />
      </div>

      <!-- 其余类型浏览器无法渲染，只提示下载（§10.1） -->
      <ElEmpty v-else-if="!loading" description="该文件类型无法在线预览，请下载查看" />
    </div>

    <template #footer>
      <div class="preview-footer">
        <span v-if="attachment" class="preview-meta" :title="metaText">{{ metaText }}</span>
        <div class="preview-actions">
          <ElButton @click="emit('update:visible', false)">关闭</ElButton>
          <ElButton type="primary" :loading="downloading" @click="handleDownload">下载</ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import FeishuWorkbenchAPI from '@/api/module_feishu/workbench'
import download from '@/utils/download'
import { attachmentKind, fileNameOf, formatFileSize, type AttachmentItem } from '@/utils/attachment'
import { MAX_PREVIEW_COLS, MAX_PREVIEW_ROWS, parseSpreadsheet, type PreviewSheet } from '@/utils/spreadsheet'

defineOptions({ name: 'FeishuAttachmentPreviewDialog' })

const props = defineProps<{
  visible: boolean
  attachment: AttachmentItem | null
  /** 附件所属的工作台；决定后端用哪个 base 取文件（威达附件必传） */
  role?: string
  /** 附件挂在哪张表上；关联表里的附件必传（飞书按 tableId 判权限） */
  tableId?: string
}>()

const emit = defineEmits<{ (e: 'update:visible', value: boolean): void }>()

const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const objectUrl = ref('')
/** 已取回的字节：预览与下载共用一次请求，点「下载」不必再拉一遍 */
let blob: Blob | null = null
/** 防止「加载中关闭弹窗」后旧请求回来把已释放的资源又挂上去 */
let fetchToken = 0

const kind = computed(() => (props.attachment ? attachmentKind(props.attachment) : 'other'))

/** 表格附件解析出来的工作表 */
const sheets = ref<PreviewSheet[]>([])
const activeSheet = ref(0)
const truncated = ref(false)
/** 当前工作表渲染的那几行（`cells[r][c] === null` 表示被合并吃掉，模板跳过） */
const activeCells = computed(() => sheets.value[activeSheet.value]?.cells ?? [])

/** 页脚左侧的「文件名 · 体积」，完整名放 title（附件名可能很长） */
const metaText = computed(() => {
  const item = props.attachment
  if (!item) return ''
  const size = formatFileSize(item.size)
  return size ? `${fileNameOf(item)} · ${size}` : fileNameOf(item)
})

watch(
  () => [props.visible, props.attachment?.file_token] as const,
  ([visible]) => {
    if (visible) {
      void load()
    } else {
      cleanup()
    }
  },
  { immediate: true }
)

async function load() {
  const target = props.attachment
  if (!target) return

  const token = ++fetchToken
  loading.value = true
  error.value = ''
  blob = null
  sheets.value = []
  activeSheet.value = 0
  truncated.value = false
  revoke()

  try {
    const res = await FeishuWorkbenchAPI.downloadFile(target.file_token, props.role, props.tableId)
    if (token !== fetchToken) return // 期间已关闭/切换，丢弃
    blob = res.data
    // 只有浏览器能渲染的类型才需要 objectURL；其余类型拉回来只是为了「下载」
    if (canRender()) objectUrl.value = URL.createObjectURL(blob)
    if (kind.value === 'excel') await loadSheets(blob, token)
  } catch (e) {
    if (token !== fetchToken) return
    error.value = e instanceof Error ? e.message : '附件加载失败'
    console.error('[Feishu] 附件预览失败', e)
  } finally {
    if (token === fetchToken) loading.value = false
  }
}

/**
 * 解析表格附件。
 *
 * 解析失败**不当成加载失败**：报错区是给「拿不到文件」用的，一个格式怪异的 xlsx
 * 不该让整个预览变成红字——用户还是能下载下来用 Excel 打开。所以这里只吞掉并留空，
 * 界面自然落到「没有可显示的内容，请下载查看」。
 */
async function loadSheets(data: Blob, token: number): Promise<void> {
  try {
    const parsed = await parseSpreadsheet(await data.arrayBuffer())
    if (token !== fetchToken) return
    sheets.value = parsed.sheets
    truncated.value = parsed.truncated
  } catch (e) {
    console.error('[Feishu] 表格附件解析失败', e)
  }
}

function canRender(): boolean {
  return kind.value === 'pdf' || kind.value === 'image'
}

async function handleDownload() {
  const target = props.attachment
  if (!target) return

  downloading.value = true
  try {
    if (!blob) {
      const res = await FeishuWorkbenchAPI.downloadFile(target.file_token, props.role, props.tableId)
      blob = res.data
    }
    download.saveAs(blob, fileNameOf(target))
  } catch (e) {
    console.error('[Feishu] 附件下载失败', e)
    ElMessage.error('下载失败，请重试')
  } finally {
    downloading.value = false
  }
}

function revoke() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
}

function cleanup() {
  fetchToken++ // 让在途请求作废
  loading.value = false
  error.value = ''
  blob = null
  sheets.value = []
  truncated.value = false
  revoke()
}

onBeforeUnmount(cleanup)
</script>

<style scoped>
.preview-body {
  min-height: 320px;
}

.preview-frame {
  width: 100%;
  height: 72vh;
  border: none;
}

.preview-image {
  display: flex;
  justify-content: center;
  height: 72vh;
}

.preview-image :deep(.el-image) {
  height: 100%;
}

.preview-sheet {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sheet-tabs {
  display: flex;
  flex-wrap: wrap;
}

/* 宽表格横向滚动而不是压缩列宽：报价单的列被挤压后完全没法读 */
.sheet-scroll {
  max-height: 68vh;
  overflow: auto;
  border: 1px solid var(--el-border-color);
}

.sheet-table {
  font-size: 12px;
  white-space: nowrap;
  border-collapse: collapse;
}

.sheet-table td {
  max-width: 320px;
  padding: 4px 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  border: 1px solid var(--el-border-color-lighter);
}

/* 表头行（Excel 里几乎总是第一行）加粗并冻结，长表格滚下去还能对上列 */
.sheet-table tr:first-child td {
  position: sticky;
  top: 0;
  font-weight: 600;
  background: var(--el-fill-color-light);
}

.sheet-hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.preview-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.preview-meta {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.preview-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  margin-left: auto;
}
</style>
