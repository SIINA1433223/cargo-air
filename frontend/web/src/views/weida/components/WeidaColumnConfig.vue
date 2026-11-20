<template>
  <ElDialog
    :model-value="visible"
    title="自定义列"
    width="880px"
    top="6vh"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <div class="cfg-body">
      <!-- 左：可选字段 -->
      <div class="cfg-pane">
        <div class="pane-header">
          <span class="pane-title">可选字段</span>
          <span class="pane-hint">{{ available.length }}</span>
        </div>
        <ElInput v-model="fieldKeyword" class="pane-search" placeholder="搜索字段名" clearable :prefix-icon="Search" />
        <div class="pane-list">
          <!--
            mousedown.prevent 让点击不抢走表达式输入框的焦点：光标还在拼接列里时，
            点字段是「插入占位符」；没在编辑任何拼接列时，点字段是「新增一列」。
          -->
          <button
            v-for="f in available"
            :key="f.name"
            type="button"
            class="field-chip"
            :title="`${f.name}（${f.ui_type}）`"
            @mousedown.prevent
            @click="addField(f.name)">
            <span class="chip-name">{{ f.name }}</span>
            <ElIcon class="chip-icon"><Plus /></ElIcon>
          </button>
          <ElEmpty v-if="!available.length" description="没有匹配的字段" :image-size="60" />
        </div>
      </div>

      <!-- 右：已选列 -->
      <div class="cfg-pane">
        <div class="pane-header">
          <span class="pane-title">已选列（{{ draft.length }}）</span>
          <div class="pane-actions">
            <ElButton size="small" @click="addTemplate">添加拼接列</ElButton>
            <ElButton size="small" @click="startAddBlank">添加空列</ElButton>
            <ElButton size="small" type="danger" plain :disabled="!draft.length" @click="resetToDefault">
              恢复默认
            </ElButton>
          </div>
        </div>

        <!--
          新建空列的内联输入：不用 ElMessageBox 之类的程序化弹窗——本项目约定
          所有 Dialog/Modal 都拆成独立 .vue 组件，这里用一个内联行最省事。
        -->
        <div v-if="blankFormVisible" class="blank-create">
          <ElInput
            v-model="blankName"
            class="blank-create-input"
            size="small"
            placeholder="新列名——会在飞书表里新建一个文本字段"
            @keyup.enter="confirmAddBlank" />
          <ElButton size="small" type="primary" :loading="creating" @click="confirmAddBlank">新建</ElButton>
          <ElButton size="small" @click="cancelAddBlank">取消</ElButton>
        </div>

        <div class="pane-list pane-list-rows">
          <div
            v-for="(col, i) in draft"
            :key="colKey(col, i)"
            class="col-row"
            :class="{ 'col-row-block': col.kind === 'template' }">
            <template v-if="col.kind === 'field'">
              <ElIcon class="col-drag"><Grid /></ElIcon>
              <span class="col-name">{{ col.name }}</span>
              <div class="col-actions">
                <ElButton link size="small" :disabled="i === 0" @click="move(i, -1)">上移</ElButton>
                <ElButton link size="small" :disabled="i === draft.length - 1" @click="move(i, 1)">下移</ElButton>
                <ElButton link type="danger" size="small" @click="removeColumn(i)">移除</ElButton>
              </div>
            </template>

            <!-- 拼接列：多行编辑区 + 分隔符快插，独立成块，避免被单行输入框挤扁 -->
            <template v-else-if="col.kind === 'template'">
              <div class="template-head">
                <ElTag class="col-tag" size="small" type="warning" effect="plain">拼接</ElTag>
                <ElInput v-model="col.label" class="col-label-input" size="small" placeholder="列名" />
                <div class="col-actions">
                  <ElButton link size="small" :disabled="i === 0" @click="move(i, -1)">上移</ElButton>
                  <ElButton link size="small" :disabled="i === draft.length - 1" @click="move(i, 1)">下移</ElButton>
                  <ElButton link type="danger" size="small" @click="removeColumn(i)">移除</ElButton>
                </div>
              </div>

              <ElInput
                v-model="col.expression"
                class="col-expr-input"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 8 }"
                resize="vertical"
                placeholder="点左侧字段名插入占位符；也可直接打分隔文字，回车即换行"
                @focus="onExprFocus($event, col)"
                @blur="onExprBlur" />

              <div class="sep-bar">
                <span class="sep-label">插入分隔符：</span>
                <!-- mousedown.prevent：让点击不把光标从文本框抢走，插入才能落在光标处 -->
                <ElButton
                  v-for="sep in SEPARATORS"
                  :key="sep.label"
                  size="small"
                  plain
                  @mousedown.prevent
                  @click="insertText(sep.value)">
                  {{ sep.label }}
                </ElButton>
              </div>
            </template>

            <!-- 空列：列名可改（保存时同步改飞书字段名），单元格里直接敲内容 -->
            <template v-else>
              <ElTag class="col-tag" size="small" type="success" effect="plain" :title="`飞书字段名：${col.original}`">
                空列
              </ElTag>
              <ElInput v-model="col.label" class="col-label-input" size="small" placeholder="列名" />
              <span v-if="draftFieldName(col) !== col.original" class="col-rename-hint">保存时改飞书字段名</span>
              <div class="col-actions">
                <ElButton link size="small" :disabled="i === 0" @click="move(i, -1)">上移</ElButton>
                <ElButton link size="small" :disabled="i === draft.length - 1" @click="move(i, 1)">下移</ElButton>
                <ElButton link type="danger" size="small" @click="removeColumn(i)">移除</ElButton>
              </div>
            </template>
          </div>

          <ElEmpty v-if="!draft.length" description="还没有选任何列，从左侧点字段加入" :image-size="60" />
        </div>

        <div class="pane-tip">
          <template v-if="hasTemplate">
            在拼接列的文本框里点左侧字段名，即在光标处插入
            <code>{字段名}</code>
            ；分隔符按钮同理。文本框里直接回车就是换行。拼接列只读——要改内容请改组成它的原字段。
          </template>
          <template v-if="hasBlank">
            空列会在飞书表里新建一个文本字段，值写进飞书、所有人可见。字段真名是
            <code>列名_用户名_短码</code>
            （这样各人建的列不会撞名，列里填的仍是「列名」），
            改列名即改真名的前半截。字段一旦建出就留在飞书表里了——点「取消」或从视图移除空列
            都**不会**删掉它，要删得去飞书。
          </template>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="cfg-footer">
        <span v-if="error" class="cfg-error">{{ error }}</span>
        <div class="cfg-footer-actions">
          <ElButton @click="emit('update:visible', false)">取消</ElButton>
          <ElButton type="primary" :loading="saving" @click="handleSave">保存</ElButton>
        </div>
      </div>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElButton, ElDialog, ElEmpty, ElIcon, ElInput, ElTag } from 'element-plus'
import { Grid, Plus, Search } from '@element-plus/icons-vue'
import FeishuWorkbenchAPI, { type FieldMeta } from '@/api/module_feishu/workbench'
import {
  blankFieldName,
  defaultColumns,
  makeBlankSuffix,
  suffixFromFieldName,
  type ViewColumn,
} from '@/utils/view-config'

defineOptions({ name: 'WeidaColumnConfig' })

const props = defineProps<{
  visible: boolean
  /** 工作台标识（建字段/改字段名要按工作台解析数据源） */
  role: string
  /** 本表全部字段（含不在任何分组里的），由后端 config.fields 下发 */
  fields: FieldMeta[]
  /** 当前生效的列 */
  columns: ViewColumn[]
  /** 后端默认列名，用于「恢复默认」 */
  defaultColumnNames: string[]
  /** 当前登录用户的显示名，用于拼空列的飞书字段名后缀 */
  userName: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'save', columns: ViewColumn[]): void
  (e: 'reset'): void
  /** 飞书表里新增/改名了一个字段，调用方据此刷新 config.fields */
  (e: 'field-created', name: string): void
}>()

/**
 * 编辑中的列。比 `ViewColumn` 多一个 `original`：空列改名要落到飞书上，
 * 得记住**飞书里现在的字段名**才知道要不要改、改哪个。
 *
 * 注意 `label` 与 `original` 是两回事：`label` 是用户起的显示名（列头显示的就是它），
 * `original` 是飞书里那一长串 `{label}_{用户名}_{短码}`。两者相等只说明还没改过名。
 */
type DraftColumn =
  | { kind: 'field'; name: string }
  | { kind: 'template'; label: string; expression: string }
  | { kind: 'blank'; label: string; suffix: string; original: string }

const draft = ref<DraftColumn[]>([])
const fieldKeyword = ref('')
const error = ref('')
/** 当前正在编辑的拼接列（用于「点字段/点分隔符插入到它」） */
const activeExpr = ref<{ expression: string } | null>(null)
/** 该拼接列的 textarea 元素，用来按光标位置插入（而不是只会追加到末尾） */
let activeInput: HTMLTextAreaElement | null = null

/** 新建空列的输入行 */
const blankFormVisible = ref(false)
const blankName = ref('')
const creating = ref(false)
const saving = ref(false)

/** 分隔符快捷键。换行用 `\n`，表格单元格按 pre-line 渲染成真正的换行。 */
const SEPARATORS: readonly { label: string; value: string }[] = [
  { label: '空格', value: ' ' },
  { label: '·', value: ' · ' },
  { label: '顿号', value: '、' },
  { label: '斜杠', value: ' / ' },
  { label: '短横', value: ' - ' },
  { label: '竖线', value: ' | ' },
  { label: '换行', value: '\n' },
]

function onExprFocus(e: FocusEvent, col: { expression: string }) {
  activeExpr.value = col
  activeInput = e.target as HTMLTextAreaElement
}

function onExprBlur() {
  activeExpr.value = null
  activeInput = null
}

/**
 * 在当前光标处插入文本；文本框没聚焦时追加到末尾。
 *
 * 插入后把光标挪到插入内容之后，用户可以接着点下一个字段/分隔符。
 */
function insertText(text: string) {
  const target = activeExpr.value
  if (!target) return
  const el = activeInput
  if (!el) {
    target.expression += text
    return
  }
  const start = el.selectionStart ?? target.expression.length
  const end = el.selectionEnd ?? start
  target.expression = target.expression.slice(0, start) + text + target.expression.slice(end)
  const caret = start + text.length
  // 等 v-model 把新值刷进 DOM 再定位光标，否则会被重置到末尾
  void nextTick(() => {
    el.focus()
    el.setSelectionRange(caret, caret)
  })
}

function toDraft(c: ViewColumn): DraftColumn {
  if (c.kind === 'blank') return { kind: 'blank', label: c.label, suffix: c.suffix, original: blankFieldName(c) }
  if (c.kind === 'template') return { kind: 'template', label: c.label, expression: c.expression }
  return { kind: 'field', name: c.name }
}

function toColumn(c: DraftColumn): ViewColumn {
  if (c.kind === 'blank') return { kind: 'blank', label: c.label.trim(), suffix: c.suffix }
  if (c.kind === 'template') return { kind: 'template', label: c.label.trim(), expression: c.expression }
  return { kind: 'field', name: c.name }
}

/**
 * 草稿里的空列按当前显示名算出来的飞书字段名——建字段/改字段名都发这个。
 *
 * 显示名里的下划线换成横线：后缀靠下划线拆出「用户名」与「短码」，显示名自带
 * 下划线会把结构搅乱（`suffixFromFieldName` 就切不准了）。
 */
function draftFieldName(col: Extract<DraftColumn, { kind: 'blank' }>): string {
  return blankFieldName({ label: col.label.trim().replaceAll('_', '-'), suffix: col.suffix })
}

// 打开时把当前列拷进草稿：编辑期间不影响列表，取消即丢弃
watch(
  () => props.visible,
  (v) => {
    if (!v) return
    draft.value = props.columns.map(toDraft)
    fieldKeyword.value = ''
    error.value = ''
    activeExpr.value = null
    blankFormVisible.value = false
    blankName.value = ''
  },
  { immediate: true }
)

/**
 * 已被占用的飞书字段名，避免同一个字段被选两遍。
 *
 * 空列要同时记两个名字：`original` 是它在飞书里**现在**叫什么，`draftFieldName` 是
 * 改名后**将要**叫什么。只记后者的话，用户在编辑框里改了名、还没保存时，左边「可选
 * 字段」里会冒出一个同名字段（那是同一列的两个名字），点一下就成了重复列。
 */
const chosenNames = computed(() => {
  const s = new Set<string>()
  for (const c of draft.value) {
    if (c.kind === 'field') s.add(c.name)
    else if (c.kind === 'blank') {
      s.add(c.original)
      s.add(draftFieldName(c))
    }
  }
  return s
})

const available = computed(() => {
  const kw = fieldKeyword.value.trim().toLowerCase()
  return props.fields
    .filter((f) => !chosenNames.value.has(f.name))
    .filter((f) => !kw || f.name.toLowerCase().includes(kw))
})

const hasTemplate = computed(() => draft.value.some((c) => c.kind === 'template'))
const hasBlank = computed(() => draft.value.some((c) => c.kind === 'blank'))

/**
 * 列表 key。空列/拼接列用**下标**而不是名字：这两类的名字可以在行内直接编辑，
 * 用名字当 key 的话每打一个字 key 就变，输入框会被重建、光标丢失。
 */
function colKey(col: DraftColumn, i: number): string {
  return col.kind === 'field' ? `f-${col.name}` : `${col.kind}-${i}`
}

function addField(name: string) {
  // 光标在某个拼接列的表达式里时，「点字段」的意图是插入占位符而不是新增一列
  if (activeExpr.value) {
    insertText(`{${name}}`)
    return
  }
  draft.value.push({ kind: 'field', name })
}

function addTemplate() {
  const col: DraftColumn = { kind: 'template', label: '新拼接列', expression: '' }
  draft.value.push(col)
  activeExpr.value = col
}

function startAddBlank() {
  blankFormVisible.value = true
  blankName.value = ''
  error.value = ''
}

function cancelAddBlank() {
  blankFormVisible.value = false
  blankName.value = ''
}

/**
 * 建空列 = 往飞书表里真加一个文本字段。
 *
 * 用户输入的是**显示名**，飞书里那个字段叫 `{显示名}_{用户名}_{短码}`（见
 * `blankFieldName`）。所以这里**不做重名预检查**：拼上自己的用户名和随机短码
 * 之后，用户打「备注」压根不会撞上表里原有的「备注」——早先那道人肉拦截
 * 反倒正是挡着用户建列的原因。真撞上（同一用户同一短码，概率可忽略）后端那道
 * 检查会拦下来并给出明确提示。
 */
async function confirmAddBlank() {
  const name = blankName.value.trim()
  if (!name) {
    error.value = '新列名不能为空'
    return
  }
  // 显示名里带下划线会和后缀里的下划线混起来（后缀靠下划线拆用户名与短码），换成横线
  const label = name.replaceAll('_', '-')
  const suffix = makeBlankSuffix(props.userName)
  creating.value = true
  try {
    const res = await FeishuWorkbenchAPI.createField(props.role, blankFieldName({ label, suffix }))
    const created = res.data.data.name
    // 后缀以飞书返回的名字为准：field_name 是飞书说了算的（去空白/截断都可能），
    // 万一它改过，切出来的后缀才是字段真名里那一段，改列名时才拼得回去
    draft.value.push({ kind: 'blank', label, suffix: suffixFromFieldName(created, label) || suffix, original: created })
    emit('field-created', created)
    cancelAddBlank()
    error.value = ''
    // 成功提示由 http 拦截器统一弹（AGENTS.md：非 GET 成功不在业务代码里手动提示），
    // 所以上面那句 createField 不传 showSuccessMessage: false
  } catch {
    // 具体报错（重名、无权限等）由 http 拦截器统一弹出，这里只留住对话框
    error.value = '新建列失败，请按提示修改列名后重试'
  } finally {
    creating.value = false
  }
}

function move(i: number, delta: number) {
  const j = i + delta
  if (j < 0 || j >= draft.value.length) return
  const next = [...draft.value]
  ;[next[i], next[j]] = [next[j], next[i]]
  draft.value = next
}

function resetToDefault() {
  draft.value = defaultColumns(props.defaultColumnNames).map(toDraft)
  activeExpr.value = null
  error.value = ''
}

function removeColumn(i: number) {
  if (draft.value[i] === activeExpr.value) activeExpr.value = null
  draft.value.splice(i, 1)
}

async function handleSave() {
  if (!draft.value.length) {
    error.value = '至少要保留一列'
    return
  }
  const badTemplate = draft.value.find((c) => c.kind === 'template' && (!c.label.trim() || !c.expression.trim()))
  if (badTemplate) {
    error.value = '拼接列的列名与表达式都不能为空'
    return
  }
  if (draft.value.some((c) => c.kind === 'blank' && !c.label.trim())) {
    error.value = '空列的列名不能为空'
    return
  }

  saving.value = true
  try {
    // 空列改了显示名 → 同步改飞书字段名（`{显示名}_{用户名}_{短码}`，后缀不动，
    // 所以只改前半截）。这是改生产表结构，逐个来、失败即中止，免得出现
    // 「一半改了名一半没改」的半吊子状态。
    for (const col of draft.value) {
      if (col.kind !== 'blank') continue
      const next = draftFieldName(col)
      if (next === col.original) continue
      await FeishuWorkbenchAPI.renameField(props.role, col.original, next)
      col.original = next
      emit('field-created', next)
    }
    error.value = ''
    emit('save', draft.value.map(toColumn))
    emit('update:visible', false)
  } catch {
    error.value = '改列名失败，已中止保存'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.cfg-body {
  display: flex;
  gap: 12px;
  height: 58vh;
}

.cfg-pane {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}

.pane-header {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}

.pane-title {
  font-weight: 600;
}

.pane-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.pane-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.pane-search {
  margin-bottom: 8px;
}

.pane-list {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  align-content: flex-start;
  overflow-y: auto;
}

.pane-list-rows {
  flex-flow: column nowrap;
}

.blank-create {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 8px;
}

.blank-create-input {
  flex: 1;
}

.field-chip {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 3px 8px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  cursor: pointer;
  background: var(--el-fill-color-light);
  border: 1px solid transparent;
  border-radius: 4px;
}

.field-chip:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.chip-icon {
  font-size: 12px;
}

.col-row {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 4px 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.col-drag {
  color: var(--el-text-color-placeholder);
}

.col-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-tag {
  flex-shrink: 0;
}

.col-label-input {
  width: 140px;
}

.col-rename-hint {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--el-color-warning);
}

/* 拼接列独立成块：上排「标签 + 列名 + 操作」，下面是多行编辑区与分隔符条 */
.col-row-block {
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
  padding: 8px 6px;
}

.template-head {
  display: flex;
  gap: 6px;
  align-items: center;
}

.col-expr-input {
  width: 100%;
}

.col-expr-input :deep(.el-textarea__inner) {
  font-family: inherit;
  line-height: 1.6;
}

.sep-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.sep-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.col-actions {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
  margin-left: auto;
}

.pane-tip {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.cfg-footer {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.cfg-error {
  font-size: 12px;
  color: var(--el-color-danger);
}

.cfg-footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
