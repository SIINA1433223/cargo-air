<template>
  <ElDialog
    :model-value="visible"
    :title="dialogTitle"
    width="1160px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <div v-loading="loading" class="cost-body">
      <!--
        顶部一条工具栏，不是表单标题：明细是一行一条的记录，没有「整张表填完再交」这一步
        （每格失焦即写飞书），所以这里只放「加一行」和条数。
      -->
      <div class="cost-toolbar">
        <span class="cost-count">共 {{ rows.length }} 条明细</span>
        <ElButton size="small" type="primary" plain :loading="adding" :disabled="!canUpdate" @click="addRow">
          ＋ 添加一行
        </ElButton>
      </div>

      <div v-if="columns.length" class="cost-grid" :style="gridStyle">
        <div v-for="col in columns" :key="col.name" class="cost-head" :title="col.name">{{ col.name }}</div>
        <div class="cost-head cost-head-act">操作</div>

        <template v-for="row in rows" :key="row.record_id">
          <div
            v-for="col in columns"
            :key="col.name"
            class="cost-cell"
            :class="`state-${cellState[cellKey(row, col)] ?? ''}`">
            <RecordFieldControl
              :field="col"
              :value="row.fields[col.name]"
              :role="role"
              :table-id="childTableId"
              :disabled="!canUpdate || savingCell === cellKey(row, col)"
              :textarea="isMemo(col)"
              commit-on-change
              @update:value="(v) => commit(row, col, v)" />
          </div>
          <div class="cost-cell cost-cell-act">
            <ElButton
              link
              type="danger"
              size="small"
              :loading="removingId === row.record_id"
              :disabled="!canUpdate"
              @click="removeRow(row)">
              删除
            </ElButton>
          </div>
        </template>
      </div>

      <!--
        一条明细都还没有时**没有列可画**：列的定义（字段名 + 43 个费用类别选项）只随
        `linked-records` 一起下发，没有关联记录就没有元数据。所以先请用户点「添加一行」——
        建出第一条后元数据就到了（见 `addRow`）。
      -->
      <ElEmpty v-else-if="!loading" :description="emptyHint" />
    </div>
  </ElDialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import FeishuWorkbenchAPI, { FIELD_TYPE, type BitableRecord, type FieldMeta } from '@/api/module_feishu/workbench'
import { useAuth } from '@/hooks/core/useAuth'
import RecordFieldControl from '@/views/module_feishu/components/RecordFieldControl.vue'

defineOptions({ name: 'WeidaCostDetailDialog' })

const props = defineProps<{
  visible: boolean
  /** 工作台标识（决定往哪个 base 写） */
  role: string
  /**
   * 父记录：当前这张对客方案。它自己的 `link_ids[linkField]` 就是已有的费用明细 id。
   */
  parent: BitableRecord | null
  /**
   * 父记录所在的表（对客方案表）。
   *
   * `linkField` 长在**这张**表上，不是工作台表（商机主表）上——后端默认只扫工作台表，
   * 漏传会得到「飞书中不存在字段「费用明细」」。这是二级关联必须显式告诉服务端的事。
   */
  parentTableId: string
  /** 子记录挂在父记录上的关联字段名（对客方案上的「费用明细」） */
  linkField: string
  /** 新子记录**回指**父记录的字段名（费用明细上的「关联的对客方案」） */
  backRefField: string
  /**
   * 父记录的显示名（如方案编号 NO.033）。
   *
   * 标题里带上它：同一屏可能开着好几个方案的明细，只写「费用明细」分不清是给哪个方案加的。
   */
  parentLabel?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  /** 增删过明细（结构变了）——调用方据此重拉父表那一块，让父记录的关联 id 跟上 */
  (e: 'changed'): void
}>()

const { hasAuth } = useAuth()
const canUpdate = computed(() => hasAuth('module_feishu:record:update'))

/** 标题带上是哪条方案：同屏可能开着好几个方案的明细，只写「费用明细」分不清给谁加的 */
const dialogTitle = computed(() => (props.parentLabel ? `对客方案费用明细 · ${props.parentLabel}` : '对客方案费用明细'))

const loading = ref(false)
const adding = ref(false)
const rows = ref<BitableRecord[]>([])
/** 子表的字段定义（**随记录一起**下发，见 `linked_records`：子表不属于任何工作台） */
const fields = ref<FieldMeta[]>([])
/** 子表 id：附件下载要按表判权限 */
const childTableId = ref('')
/**
 * 本次会话里**新建**出来的明细 id。
 *
 * 新建的子记录由飞书自己补上父记录那一侧的双向关联，但**我们手里这份父记录是旧的**
 * （`link_ids` 里没有它）。合并进来，关掉再打开才看得见刚建的那几条。
 */
const extraIds = ref<string[]>([])

/** 正在写的那一格（`record_id|字段名`），写的时候只禁用它自己 */
const savingCell = ref('')
const removingId = ref('')
const cellState = ref<Record<string, 'saving' | 'saved' | 'error'>>({})

const EMPTY_PARENT = '请先选一条对客方案'
const emptyHint = computed(() => (props.parent ? '还没有费用明细，点「＋ 添加一行」开始录入' : EMPTY_PARENT))

/** 关联字段（单向/双向）不在这里编辑：它由「添加一行」自动建立，摆个选择器只会让人误改归属 */
const LINK_TYPES: readonly number[] = [FIELD_TYPE.SINGLE_LINK, FIELD_TYPE.DUPLEX_LINK]

/**
 * 表格列 = 子表里**能改**的字段，按飞书表顺序（费用类别 → 币种 → 计费单位 → 金额 → 备注 → 分组）。
 *
 * 现算而不是写死六个名字：飞书那边加一个「税率」列，这里自动多一列；只读的自动编号、
 * 查找来的商机负责人、以及那个关联字段都不会混进来。
 */
const columns = computed(() => fields.value.filter((f) => f.editable && !LINK_TYPES.includes(f.type)))

/**
 * 「备注」这类自由文本给**多行**输入框：单行框里按回车是提交，想换行没地方按
 * （用户提的就是这个）。按类型判断而不是按字段名——这张表里只有一个是文本字段。
 */
function isMemo(field: FieldMeta): boolean {
  return field.type === FIELD_TYPE.TEXT
}

/** 备注要拿去换行的，给它多一点宽度；其余等宽 */
const WIDE_COLUMNS = new Set(['备注'])

const gridStyle = computed<Record<string, string>>(() => ({
  // 末尾定宽那一列是「删除」。下限用 0 让它能被压缩（列多了也不会撑出对话框）。
  gridTemplateColumns: `${columns.value
    .map((col) => (WIDE_COLUMNS.has(col.name) ? 'minmax(0, 1.6fr)' : 'minmax(0, 1fr)'))
    .join(' ')} 64px`,
}))

function cellKey(row: BitableRecord, col: FieldMeta): string {
  return `${row.record_id}|${col.name}`
}

function setCellState(key: string, state: '' | 'saving' | 'saved' | 'error'): void {
  const next = { ...cellState.value }
  if (state) next[key] = state
  else delete next[key]
  cellState.value = next
  if (state === 'saved') {
    // 绿一下再退回去：常驻的话下次改同一格看不出「刚存过」
    window.setTimeout(() => {
      if (cellState.value[key] === 'saved') setCellState(key, '')
    }, 1500)
  }
}

/** 父记录上已有的明细 id + 本次新建的（父记录那份是旧的，见 `extraIds`） */
function currentIds(): string[] {
  const known = props.parent?.link_ids?.[props.linkField] ?? []
  return [...new Set([...known, ...extraIds.value])]
}

async function load(): Promise<void> {
  if (!props.parent) {
    rows.value = []
    fields.value = []
    return
  }
  loading.value = true
  try {
    const res = await FeishuWorkbenchAPI.getLinkedRecords(
      props.role,
      props.linkField,
      currentIds(),
      props.parentTableId
    )
    const data = res.data.data
    fields.value = data.fields
    rows.value = data.records
    childTableId.value = data.table_id
  } catch {
    // 拉不到就当空的：界面还能用「添加一行」，比整块卡住强
    fields.value = []
    rows.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.visible, props.parent?.record_id] as const,
  ([visible]) => {
    if (!visible) return
    // 每次打开都重来：上一次的增删可能已经改变飞书那边的内容
    rows.value = []
    fields.value = []
    childTableId.value = ''
    extraIds.value = []
    cellState.value = {}
    savingCell.value = ''
    removingId.value = ''
    void load()
  },
  { immediate: true }
)

/**
 * 加一行：先在飞书建一条**只带回指**的空明细，再用它的 id 问一次拿回「这一条的字段定义」。
 *
 * 为什么不能先建本地空行：列的定义（含 43 个费用类别选项）只有 `linked-records` 会给，
 * 而它要求至少有一个 id。建完立刻按新 id 问，元数据就跟着第一条记录一起到了。
 *
 * 代价是点了就落库——和抽屉里「新增航线报价」一样，关掉前不填就是一条空明细，
 * 用右边的「删除」清掉即可。
 */
async function addRow(): Promise<void> {
  const parentRec = props.parent
  if (!parentRec || adding.value || !canUpdate.value) return
  adding.value = true
  try {
    const created = await FeishuWorkbenchAPI.createLinkedRecord(
      props.role,
      props.linkField,
      { [props.backRefField]: [parentRec.record_id] },
      props.parentTableId
    )
    const newId = created.data.data.record_id
    extraIds.value = [...extraIds.value, newId]

    const got = await FeishuWorkbenchAPI.getLinkedRecords(props.role, props.linkField, [newId], props.parentTableId)
    fields.value = got.data.data.fields
    childTableId.value = got.data.data.table_id
    rows.value = [...rows.value, ...got.data.data.records]
    emit('changed')
  } catch {
    // 失败提示由拦截器弹；这里什么都不加，界面上就是「没反应」，不会留下半条假行
  } finally {
    adding.value = false
  }
}

/** 就地改一格：乐观更新 + 失败回滚，成功/失败都只在这格上反馈（不弹 toast） */
async function commit(row: BitableRecord, col: FieldMeta, value: unknown): Promise<void> {
  const key = cellKey(row, col)
  const previous = row.fields[col.name]
  row.fields[col.name] = value

  savingCell.value = key
  setCellState(key, 'saving')
  try {
    await FeishuWorkbenchAPI.updateLinkedRecord(
      props.role,
      props.linkField,
      row.record_id,
      { [col.name]: value },
      props.parentTableId
    )
    setCellState(key, 'saved')
  } catch {
    row.fields[col.name] = previous
    setCellState(key, 'error')
  } finally {
    savingCell.value = ''
  }
}

/**
 * 删一条明细。**不可逆**，所以先问一次；取消（ElMessageBox 抛异常）就什么都不做。
 *
 * 先本地摘掉再发请求，失败再放回去——表格里那一行立刻消失，不用等一个来回。
 */
async function removeRow(row: BitableRecord): Promise<void> {
  if (!props.parent || removingId.value || !canUpdate.value) return
  try {
    await ElMessageBox.confirm('删除这条费用明细？删除后无法恢复。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户点了取消
  }

  removingId.value = row.record_id
  const snapshot = rows.value
  rows.value = rows.value.filter((item) => item.record_id !== row.record_id)
  try {
    await FeishuWorkbenchAPI.deleteLinkedRecord(props.role, props.linkField, row.record_id, props.parentTableId)
    extraIds.value = extraIds.value.filter((id) => id !== row.record_id)
    emit('changed')
  } catch {
    rows.value = snapshot
  } finally {
    removingId.value = ''
  }
}
</script>

<style scoped>
.cost-body {
  min-height: 200px;
}

.cost-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.cost-count {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

/*
  明细表：一行一条，格子就是飞书那张表的列。
  列模板由 `gridStyle` 现算（跟着子表字段走），这里只定行内共同的排版。
*/
.cost-grid {
  display: grid;
  align-items: start;
  border-top: 1px solid var(--el-border-color);
  border-left: 1px solid var(--el-border-color);
}

.cost-head {
  padding: 6px 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  white-space: nowrap;
  border-right: 1px solid var(--el-border-color);
  border-bottom: 1px solid var(--el-border-color);
}

.cost-head-act {
  text-align: center;
}

.cost-cell {
  /* 与详情页同一套兜底：内容不肯收时裁掉，别横着压到隔壁格上 */
  min-width: 0;
  padding: 4px 6px 6px;
  overflow: hidden;
  border-right: 1px solid var(--el-border-color-lighter);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.cost-cell-act {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 保存反馈与详情页同一套语义（在存 / 存好了 / 失败） */
.cost-cell.state-saving {
  background: var(--el-color-primary-light-9);
}

.cost-cell.state-saved {
  background: var(--el-color-success-light-9);
}

.cost-cell.state-error {
  background: var(--el-color-danger-light-9);
}
</style>
