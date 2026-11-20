<template>
  <div class="fa-full-height">
    <ElCard class="fa-table-card wb-card" shadow="never">
      <!-- 单行工具栏：标题 + 搜索放一行，业务数据上方不铺大面积标题区 -->
      <div class="wb-header">
        <!-- 专注模式没有侧栏，工作台切换放在页面顶部 -->
        <div class="wb-switcher">
          <RouterLink
            v-for="tab in workbenchTabs"
            :key="tab.path"
            :to="tab.path"
            class="wb-switch-item"
            :class="{ active: route.path === tab.path }">
            {{ tab.label }}
          </RouterLink>
        </div>
        <div class="wb-title-block">
          <span class="wb-title">{{ config?.name ?? '工作台' }}</span>
          <span v-if="config?.description" class="wb-subtitle">{{ config.description }}</span>
        </div>
        <div class="wb-header-actions">
          <span v-if="loading" class="wb-loading-hint">
            加载中 {{ loadedCount }}/{{ fullCount || '…' }}
          </span>
          <ElInput
            v-model="searchInput"
            class="wb-search"
            placeholder="搜索发票号 / 分单号 / 头程主单号 / 品名"
            clearable
            @keyup.enter="applyFilters"
            @clear="applyFilters" />
          <ElButton type="primary" @click="applyFilters">查询</ElButton>
          <ElButton :icon="Refresh" :loading="loading" @click="reload()">刷新</ElButton>
        </div>
      </div>

      <!-- 筛选栏：全量数据已在前端，这些筛选都是本地计算，点击即时生效、不再打飞书 -->
      <div class="wb-filters">
        <ElButton size="small" :icon="Sort" @click="toggleSort">
          {{ sortOrder === 'desc' ? '提货时间新→旧' : '提货时间旧→新' }}
        </ElButton>
        <ElButton size="small" :type="onlyUnread ? 'primary' : 'default'" :icon="View" @click="toggleUnread">
          只看未读
        </ElButton>
        <ElButton size="small" :type="onlyThirdParty ? 'primary' : 'default'" @click="toggleThirdParty">
          只看出口
        </ElButton>
        <div class="wb-filter-item">
          <span class="wb-filter-label">合并状态：</span>
          <ElSelect v-model="mergeFilter" size="small" class="wb-filter-select" @change="resetPage">
            <ElOption label="全部" value="all" />
            <ElOption label="已合并主单" value="merged_master" />
            <ElOption label="已合并分单" value="merged_hawb" />
            <ElOption label="未合并" value="unmerged" />
          </ElSelect>
        </div>
        <ElRadioGroup v-model="viewMode" size="small" class="wb-view-toggle">
          <ElRadioButton value="list">列表视图</ElRadioButton>
          <ElRadioButton value="group">合并组视图</ElRadioButton>
        </ElRadioGroup>
      </div>

      <!-- 待办计数收成一行小胶囊：点击即筛该项未勾选的票 -->
      <div v-if="stats" class="wb-stats">
        <button class="wb-stat" :class="{ 'is-active': !pendingFilter }" @click="setPending('')">
          全部 <b>{{ stats.total }}</b>
        </button>
        <button
          v-for="item in stats.pending"
          :key="item.field"
          class="wb-stat"
          :class="{ 'is-active': pendingFilter === item.field }"
          @click="setPending(item.field)">
          待{{ item.field }} <b class="wb-stat-warn">{{ item.pending }}</b>
          <span class="wb-stat-sub">/{{ item.done }}</span>
        </button>
      </div>

      <div v-if="viewMode === 'list' && selectedRows.length" class="wb-merge-bar">
        <span class="wb-merge-hint">已选 {{ selectedRows.length }} 票</span>
        <ElButton size="small" type="primary" :disabled="selectedRows.length < 2" @click="handleMerge('master_awb')">
          合并主单
        </ElButton>
        <ElButton size="small" type="warning" :disabled="selectedRows.length < 2" @click="handleMerge('hawb')">
          合并分单
        </ElButton>
        <ElButton size="small" @click="clearSelection">取消选择</ElButton>
      </div>

      <ElTable
        v-if="viewMode === 'list'"
        ref="tableRef"
        v-loading="loading"
        :data="pagedRecords"
        size="small"
        class="wb-table"
        border
        :row-class-name="rowClassName"
        style="width: 100%"
        @selection-change="onSelectionChange">
        <ElTableColumn type="selection" width="46" fixed="left" />
        <ElTableColumn
          v-for="col in config?.columns ?? []"
          :key="col"
          :label="col"
          :min-width="columnWidth(col)"
          :fixed="col === '发票号' ? 'left' : col === '合出主单' || col === '合出分单' ? 'right' : undefined">
          <template #default="{ row }">
            <!-- 发票号：未读点 + 可点开详情 + 合并徽标 -->
            <div v-if="col === '发票号'" class="cell-invoice">
              <i v-if="!readIds.has(row.record_id)" class="cell-unread-dot" />
              <ElLink type="primary" :underline="false" @click="openDetail(row)">
                {{ (row.fields['发票号'] as string) || '（无发票号）' }}
              </ElLink>
              <ElTooltip v-if="mergeGroupOf(row)" :content="mergeBadgeTooltip(mergeGroupOf(row)!)" placement="top">
                <ElTag :type="mergeGroupOf(row)!.tagType" size="small" effect="dark">
                  {{ mergeBadgeLabel(mergeGroupOf(row)!) }}
                </ElTag>
              </ElTooltip>
            </div>
            <!-- 货物数据：件数 / 毛重 / 体积 / 尺码 聚合 -->
            <ElTooltip v-else-if="col === '货物数据'" :content="cargoTooltip(row)" placement="top">
              <span class="cell-cargo">{{ cargoCompact(row) }}</span>
            </ElTooltip>
            <!-- 品名：中文为主，悬浮显示英文 -->
            <ElTooltip v-else-if="col === '中文品名'" :content="productTooltip(row)" placement="top">
              <span class="cell-ellipsis">{{ text(row.fields['中文品名']) }}</span>
            </ElTooltip>
            <!-- 合并主单/分单：关联字段原值是 record_id，显示成发票号才看得懂 -->
            <ElTooltip
              v-else-if="col === '合出主单' || col === '合出分单'"
              :content="mergePeerInvoices(row, col) || '—'"
              placement="top">
              <span class="cell-ellipsis">{{ mergePeerInvoices(row, col) || '—' }}</span>
            </ElTooltip>
            <!-- 状态列：由勾选框反推 -->
            <ElTag v-else-if="col === '订舱状态'" :type="bookingStatusBadge(row.fields).type" size="small">
              {{ bookingStatusBadge(row.fields).label }}
            </ElTag>
            <ElTag v-else-if="col === '计划状态'" :type="planBadgeOf(row).type" size="small">
              {{ planBadgeOf(row).label }}
            </ElTag>
            <!-- 普通文本：单行省略，悬停弹出完整内容且可选中复制（§5.1 行高与显示） -->
            <ElPopover
              v-else
              trigger="hover"
              placement="top"
              :width="360"
              :show-after="350"
              popper-class="wb-copy-pop">
              <template #reference>
                <span class="cell-ellipsis">{{ text(row.fields[col]) }}</span>
              </template>
              <div class="cell-copy-text">{{ text(row.fields[col]) }}</div>
            </ElPopover>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <div class="cell-actions">
              <ElLink
                v-if="hasAuth('module_feishu:record:detail')"
                type="primary"
                :underline="false"
                @click="openDetail(row)">
                详情/编辑
              </ElLink>
              <!-- 「拆出」放这里而不是发票号列：那是 fixed 列，宽度固定，塞进
                   未读点+发票号+组徽标+按钮会溢出被裁掉，按钮点不到 -->
              <ElButton
                v-if="mergeGroupOf(row)"
                link
                size="small"
                type="danger"
                :disabled="!hasAuth('module_feishu:record:update')"
                @click.stop="handleUnmerge(row)">
                拆出
              </ElButton>
            </div>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :description="emptyText" />
        </template>
      </ElTable>

      <div v-else v-loading="loading" class="wb-group-view">
        <MergeGroupView
          :blocks="pagedBlocks"
          :read-ids="readIds"
          :can-update="hasAuth('module_feishu:record:update')"
          @open-detail="openDetail"
          @unmerge="handleUnmerge" />
      </div>

      <div class="wb-footer">
        <span class="wb-footer-total">
          共 {{ filteredRecords.length }} 条
          <template v-if="pendingFilter">（筛选：待{{ pendingFilter }}）</template>
          <template v-if="loadedCount && loadedCount < fullCount">
            （已加载 {{ loadedCount }}/{{ fullCount }}）
          </template>
        </span>
        <ElPagination
          v-model:current-page="pageIndex"
          v-model:page-size="pageSize"
          class="wb-footer-pager"
          layout="sizes, prev, pager, next, jumper"
          :page-sizes="[20, 50, 100, 200, 500]"
          :total="filteredRecords.length"
          background
          size="small" />
      </div>
    </ElCard>

    <RecordDetailDrawer
      v-model:visible="drawerVisible"
      :role="role"
      :config="config"
      :record="currentRecord"
      :merge-info="currentRecord ? mergeGroupOf(currentRecord) : null"
      :group-records="currentGroupRecords"
      @saved="onSaved" />
  </div>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onMounted, ref, type VNode } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, Sort, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import FeishuWorkbenchAPI, {
  type BitableRecord,
  type MergeType,
  type WorkbenchConfig,
} from '@/api/module_feishu/workbench'
import { bookingStatusBadge, planStatusBadge } from '@/utils/status-badge'
import {
  assignGroupPalette,
  buildMergeBlocks,
  buildMergeGroups,
  mergeBadgeLabel,
  mergeBadgeTooltip,
  sortByMergeGroup,
  type MergeBlock,
  type MergeGroupInfo,
  type SortDirection,
} from '@/utils/merge-group'
import { getReadOrderIds, markOrderRead } from '@/utils/unread'
import { useAuth } from '@/hooks/core/useAuth'
import MergeGroupView from './MergeGroupView.vue'
import RecordDetailDrawer from './RecordDetailDrawer.vue'

defineOptions({ name: 'FeishuWorkbenchPage' })

const props = defineProps<{ role: string }>()

const route = useRoute()

/** 顶部工作台切换（专注模式下没有侧栏菜单） */
const workbenchTabs = [
  { path: '/workbench/price', label: '价格' },
  { path: '/workbench/operator', label: '操作' },
  { path: '/workbench/fleet', label: '车队' },
]

const { hasAuth } = useAuth()

/** 全量拉取用的页大小。飞书 `page_size` 上限就是 500，实测 50/页要 40 次请求、500/页只要 4 次。 */
const PAGE_SIZE = 500
/** 翻页次数上限，防止后端 `has_more` 异常时死循环 */
const MAX_PAGES = 40

/**
 * 内存级数据缓存（按工作台）。
 *
 * 价格/操作/车队切换、或从详情返回时同一个角色往往马上又要用全量，
 * 60 秒内直接复用，不再重新打 5 页接口。刷新按钮和任何写操作都走强制重拉。
 */
// 与后端整页缓存 15 分钟对齐：切页面/切标签回来不重拉，手动「刷新」仍强制绕过。
const RECORDS_CACHE_TTL = 15 * 60_000
const recordsCache = new Map<string, { at: number; records: BitableRecord[]; total: number }>()

const SORT_STORAGE_KEY = 'cs_sort_order'
const UNREAD_STORAGE_KEY = 'cs_unread_filter'

type MergeFilter = 'all' | 'merged_master' | 'merged_hawb' | 'unmerged'
type ViewMode = 'list' | 'group'

const config = ref<WorkbenchConfig | null>(null)
/** 全量记录（一次拉完），排序/筛选/翻页都在本地做 */
const allRecords = ref<BitableRecord[]>([])
const loading = ref(false)
const loadedCount = ref(0)
const fullCount = ref(0)

const searchInput = ref('')
const appliedSearch = ref('')
const pendingFilter = ref('')

const sortOrder = ref<SortDirection>(localStorage.getItem(SORT_STORAGE_KEY) === 'asc' ? 'asc' : 'desc')
const onlyUnread = ref(localStorage.getItem(UNREAD_STORAGE_KEY) === 'true')
const onlyThirdParty = ref(false)
const mergeFilter = ref<MergeFilter>('all')
const viewMode = ref<ViewMode>('list')
const readIds = ref<Set<string>>(new Set())

const pageIndex = ref(1)
const pageSize = ref(50)

const tableRef = ref<TableInstance>()
const selectedRows = ref<BitableRecord[]>([])

const drawerVisible = ref(false)
const currentRecord = ref<BitableRecord | null>(null)
/**
 * 详情抽屉要用的「合并组全部成员」。
 *
 * 分组关系来自全量记录（`mergeGroups` 基于 `allRecords` 算），这里按 record_id 把
 * 组员取回整条记录，供「方案计划下发」Tab 展示合并主单/分单。未成组时就是当前票。
 */
const currentGroupRecords = computed<BitableRecord[]>(() => {
  const rec = currentRecord.value
  if (!rec) return []
  const info = mergeGroupOf(rec)
  if (!info) return [rec]
  const ids = new Set(info.memberIds)
  return allRecords.value.filter((r) => ids.has(r.record_id))
})
/**
 * 「计划」的**前端本地覆盖**（B+A 方案，§5.5）。
 *
 * 计划下发时飞书只勾当前票，但要求同组所有票在界面上都显示为已计划。飞书那边
 * 组内其余票确实没勾，刷新后自然回落；这里只维持**本次会话内**的同组显示。
 */
const plannedOverrideIds = ref<Set<string>>(new Set())
/** 刚操作过的那票：滚到它、并短暂高亮，操作完 2.5 秒清掉（见 `revealRecord`） */
const anchorId = ref<string | null>(null)

/** 合并组的**成员关系**：基于全量记录计算，组员永远不会因为分页/筛选被拆开 */
const rawGroups = computed<Map<string, MergeGroupInfo>>(() => {
  if (!config.value) return new Map()
  return buildMergeGroups(allRecords.value, config.value.merge_master_field, config.value.merge_hawb_field)
})

/** 待办统计：全量已在本地，直接算，不用再打一次统计接口 */
const stats = computed(() => {
  if (!config.value) return null
  const pending = config.value.todo_checkboxes.map((field) => {
    let done = 0
    for (const rec of allRecords.value) if (rec.fields[field] === true) done += 1
    return { field, pending: allRecords.value.length - done, done }
  })
  return { total: allRecords.value.length, pending }
})

/** 排序：整组按组内最大提货时间定位，组员相邻不被拆散 */
const sortedRecords = computed(() => sortByMergeGroup(allRecords.value, rawGroups.value, sortOrder.value))

/**
 * 合并组 + 组色。颜色按**列表显示顺序**发（见 `assignGroupPalette`），
 * 否则相邻显示的两个组可能撞成同色，看起来就像「没按组上色」。
 */
const mergeGroups = computed<Map<string, MergeGroupInfo>>(() =>
  assignGroupPalette(sortedRecords.value, rawGroups.value)
)

const filteredRecords = computed(() => {
  const keyword = appliedSearch.value.trim().toLowerCase()
  return sortedRecords.value.filter((rec) => {
    if (pendingFilter.value && rec.fields[pendingFilter.value] === true) return false
    if (onlyUnread.value && readIds.value.has(rec.record_id)) return false
    if (onlyThirdParty.value && String(rec.fields['三方进口'] ?? '') !== '出口') return false
    if (!matchesMergeFilter(rec)) return false
    if (keyword && !matchesKeyword(rec, keyword)) return false
    return true
  })
})

/** 本地切片分页：有全量数据，翻页是真的跳页，不再是游标上一页/下一页 */
const pagedRecords = computed(() => {
  const start = (pageIndex.value - 1) * pageSize.value
  return filteredRecords.value.slice(start, start + pageSize.value)
})

/** 合并组视图：同样本地切片，避免一次渲染上千张卡 */
const pagedBlocks = computed(() => {
  const start = (pageIndex.value - 1) * pageSize.value
  let offset = 0
  const blocks = buildMergeBlocks(filteredRecords.value, mergeGroups.value)
  const out: MergeBlock[] = []
  for (const block of blocks) {
    const end = offset + block.items.length
    if (end > start && offset < start + pageSize.value) {
      out.push({
        group: block.group,
        items: block.items.slice(Math.max(0, start - offset), start + pageSize.value - offset),
      })
    }
    offset = end
    if (offset >= start + pageSize.value) break
  }
  return out
})

const emptyText = computed(() => {
  if (appliedSearch.value) return '没有匹配的记录'
  if (pendingFilter.value) return `没有待${pendingFilter.value}的记录`
  if (onlyUnread.value || onlyThirdParty.value || mergeFilter.value !== 'all') return '当前筛选条件下没有记录'
  return '暂无数据'
})

function mergeGroupOf(row: BitableRecord): MergeGroupInfo | null {
  return mergeGroups.value.get(row.record_id) ?? null
}

/** 合并状态筛选：直接看关联 id 有没有值，不必依赖是否成组（组内至少 2 票） */
function matchesMergeFilter(rec: BitableRecord): boolean {
  if (mergeFilter.value === 'all') return true
  const master = rec.link_ids[config.value?.merge_master_field ?? '合出主单'] ?? []
  const hawb = rec.link_ids[config.value?.merge_hawb_field ?? '合出分单'] ?? []
  if (mergeFilter.value === 'merged_master') return master.length > 0
  if (mergeFilter.value === 'merged_hawb') return hawb.length > 0
  return master.length === 0 && hawb.length === 0
}

function matchesKeyword(rec: BitableRecord, keyword: string): boolean {
  const fields = ['发票号', '分单号', '头程主单号', '中文品名', '英文品名']
  return fields.some((name) =>
    String(rec.fields[name] ?? '')
      .toLowerCase()
      .includes(keyword)
  )
}

/**
 * 全量拉取。
 *
 * 必须先拉全再排序：合并组可能跨游标页，只排当前页会把组劈开；筛选也不再走飞书，
 * 拉一次之后翻页/筛选/切排序都是零请求。
 */
async function loadAll(force = false) {
  readIds.value = getReadOrderIds(props.role)
  const cached = recordsCache.get(props.role)
  if (!force && cached && Date.now() - cached.at < RECORDS_CACHE_TTL) {
    allRecords.value = cached.records
    loadedCount.value = cached.records.length
    fullCount.value = cached.total
    return
  }

  loading.value = true
  loadedCount.value = 0
  try {
    const acc: BitableRecord[] = []
    let token: string | undefined
    let pages = 0
    do {
      const res = await FeishuWorkbenchAPI.getRecords(props.role, { page_token: token, page_size: PAGE_SIZE })
      const data = res.data.data
      acc.push(...data.items)
      loadedCount.value = acc.length
      fullCount.value = data.total
      // 每拉到一页就先渲染：第一页约 5~6 秒，比等全部 5 页（20~30 秒）才出表格好得多。
      // 列表随后会随着后续页到达重新排序，合并组可能短暂跨页，最终结果不受影响。
      allRecords.value = [...acc]
      token = data.has_more && data.page_token ? data.page_token : undefined
      pages += 1
    } while (token && pages < MAX_PAGES)
    recordsCache.set(props.role, { at: Date.now(), records: acc, total: fullCount.value })
  } catch (e) {
    console.error('[Feishu] 加载工作台记录失败', e)
    ElMessage.error('加载失败，请重试')
  } finally {
    loading.value = false
  }
}

async function loadConfig() {
  const res = await FeishuWorkbenchAPI.getConfig(props.role)
  config.value = res.data.data
}

/**
 * 重新拉全量。
 *
 * ``anchorId`` 用于「合并/拆出/保存后留在原地」：不传就回到第一页（刷新按钮的语义），
 * 传了就定位到该票**新**所在的页码并滚到它。合并会改变组，排序位置随之变化，
 * 原来无条件 `pageIndex = 1` 会把用户直接甩到列表最上面，看不到自己刚改的那组。
 */
async function reload(anchorId?: string) {
  // 刷新按钮/保存/合并/拆出：一律绕过 60 秒缓存，确保看到的是刚写回的结果
  await loadAll(true)
  if (anchorId) {
    await revealRecord(anchorId)
    return
  }
  pageIndex.value = 1
}

/** 把视图挪到指定记录：先算它在第几页，翻过去再滚到视野中间并高亮一下 */
async function revealRecord(recordId: string) {
  anchorId.value = recordId
  const index = filteredRecords.value.findIndex((row) => row.record_id === recordId)
  pageIndex.value = index >= 0 ? Math.floor(index / pageSize.value) + 1 : 1
  await nextTick()
  // 合并组视图的行没有这个类，查不到就只是不滚动，不影响功能
  document.querySelector('.wb-anchor-row')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  window.setTimeout(() => {
    anchorId.value = null
  }, 2500)
}

/**
 * 计划状态：本组本次会话内下发过计划的，整组显示「已计划」（飞书只有当前票有勾）。
 * 取消勾选时也从覆盖里移除，让同组回落为待下发。
 */
function planBadgeOf(row: BitableRecord) {
  if (plannedOverrideIds.value.has(row.record_id)) return { label: '已计划', type: 'success' as const }
  return planStatusBadge(row.fields)
}

/** 详情抽屉保存后：留在刚保存的那票上（提货时间被改过时排序会变位置） */
function onSaved(payload?: { planState?: boolean }) {
  if (payload?.planState !== undefined && currentRecord.value) {
    const info = mergeGroupOf(currentRecord.value)
    const ids = info?.memberIds ?? [currentRecord.value.record_id]
    const next = new Set(plannedOverrideIds.value)
    for (const id of ids) {
      if (payload.planState) next.add(id)
      else next.delete(id)
    }
    plannedOverrideIds.value = next
  }
  return reload(currentRecord.value?.record_id)
}

function applyFilters() {
  appliedSearch.value = searchInput.value
  pageIndex.value = 1
}

function resetPage() {
  pageIndex.value = 1
}

function toggleSort() {
  sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  localStorage.setItem(SORT_STORAGE_KEY, sortOrder.value)
  pageIndex.value = 1
}

function toggleUnread() {
  onlyUnread.value = !onlyUnread.value
  localStorage.setItem(UNREAD_STORAGE_KEY, String(onlyUnread.value))
  pageIndex.value = 1
}

function toggleThirdParty() {
  onlyThirdParty.value = !onlyThirdParty.value
  pageIndex.value = 1
}

function setPending(field: string) {
  pendingFilter.value = field === pendingFilter.value ? '' : field
  pageIndex.value = 1
}

function onSelectionChange(rows: BitableRecord[]) {
  selectedRows.value = rows
}

function clearSelection() {
  tableRef.value?.clearSelection()
  selectedRows.value = []
}

function rowClassName({ row }: { row: BitableRecord }): string {
  const groupClass = mergeGroupOf(row)?.rowClass ?? ''
  // 刚操作过的票额外挂一个类，供 revealRecord 滚动定位 + 闪一下
  return row.record_id === anchorId.value ? `${groupClass} wb-anchor-row`.trim() : groupClass
}

function openDetail(row: BitableRecord) {
  // 点开即已读：写完立刻刷新本地集合，未读点即时消失
  markOrderRead(props.role, row.record_id)
  readIds.value = getReadOrderIds(props.role)
  // 全量数据已在本地（列表接口投影了详情所需的全部字段），省掉一次 3~5 秒的详情请求
  currentRecord.value = row
  drawerVisible.value = true
}

async function handleMerge(mergeType: MergeType) {
  const ids = selectedRows.value.map((r) => r.record_id)
  if (ids.length < 2) return
  if (!(await confirmCrossGroupMerge())) return
  // 点按钮即合并，不再弹框问「合并单号」：成组的依据是关联字段，
  // 「合并主单」/「合并分单」单选字段本就是选填，留空即可（后端 target_no 可省）。
  await FeishuWorkbenchAPI.merge(ids, mergeType)
  // 合并会改变分组与排序位置，锚在第一票上，避免被甩回列表最上面
  await reload(ids[0])
  // 合并后必须清空勾选，否则残留的选中会让下一次点击又合并一遍
  clearSelection()
}

/** 合并组类型的中文说法（与详情抽屉里的徽标口径一致） */
function groupTypeLabel(info: MergeGroupInfo): string {
  if (info.mergeType === 'hawb') return '分单组'
  if (info.mergeType === 'master') return '主单组'
  return '主分单组'
}

/**
 * 跨组重组确认（§4.6）。
 *
 * 选中的票若已在别的合并组里，合并会先把它们从原组摘出去——这是**不可逆的组关系变更**
 * （原组可能因此只剩一票而解散），必须先说清「哪票在哪个组」再让用户确认。
 *
 * 全是散票时不弹框：普通合并没有副作用，§4.4 的「合并前弹窗」在取消填写目标单号后
 * 已按用户要求去掉，这里只保留跨组这一种真需要提醒的情形。
 *
 * 注意消息体用 VNode 而不是 HTML 字符串：发票号/品名都是飞书来的业务数据，
 * 走 `dangerouslyUseHTMLString` 等于把表里的内容当 HTML 解析。
 */
async function confirmCrossGroupMerge(): Promise<boolean> {
  const inGroup = selectedRows.value
    .map((row) => ({ row, info: mergeGroupOf(row) }))
    .filter((item): item is { row: BitableRecord; info: MergeGroupInfo } => item.info !== null)
  if (!inGroup.length) return true

  const children: (string | VNode)[] = ['以下票已在合并组中，合并会先把它们从原组拆出：']
  for (const { row, info } of inGroup) {
    const invoice = String(row.fields['发票号'] ?? '（无发票号）')
    const members = info.visibleInvoiceNos.join('、') || '其他成员不在本页'
    children.push(h('br'), `· ${invoice} — ${groupTypeLabel(info)}，共 ${info.memberIds.length} 票（${members}）`)
  }
  children.push(h('br'), h('br'), '是否确认拆出后合并？')

  try {
    await ElMessageBox.confirm(h('div', children), '跨组重组确认', {
      confirmButtonText: '确认合并',
      cancelButtonText: '取消',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

async function handleUnmerge(row: BitableRecord) {
  const info = mergeGroupOf(row)
  if (!info) return
  // 主单组拆出会同时解除分单合并（与旧系统规则一致）
  const mergeType: MergeType = info.mergeType === 'hawb' ? 'hawb' : 'master_awb'
  const typeLabel = mergeType === 'hawb' ? '分单组' : '主单组'
  try {
    await ElMessageBox.confirm(
      `将该票从${typeLabel}中拆出${mergeType === 'master_awb' ? '（同时解除分单合并）' : ''}，组内其他票保持合并，是否确认？`,
      '确认拆出',
      { confirmButtonText: '确认拆出', cancelButtonText: '取消', type: 'warning' }
    )
    await FeishuWorkbenchAPI.unmerge([row.record_id], mergeType)
    // 锚在刚拆出来那票上：它可能因为不再是组员而换了排序位置
    await reload(row.record_id)
  } catch {
    // 用户取消
  }
}

// ---- 单元格格式化 ----

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (value === true) return '是'
  if (value === false) return '否'
  if (Array.isArray(value)) return value.length ? value.join('、') : '—'
  return String(value)
}

function textOrEmpty(value: unknown): string {
  const s = text(value)
  return s === '—' ? '' : s
}

function cargoCompact(row: BitableRecord): string {
  const parts = ['件数', '毛重', '体积', '尺码'].map((f) => textOrEmpty(row.fields[f])).filter(Boolean)
  return parts.length ? parts.join(' / ') : '—'
}

function cargoTooltip(row: BitableRecord): string {
  return ['件数', '毛重', '体积', '尺码'].map((f) => `${f}：${textOrEmpty(row.fields[f]) || '—'}`).join('\n')
}

function productTooltip(row: BitableRecord): string {
  const cn = textOrEmpty(row.fields['中文品名'])
  const en = textOrEmpty(row.fields['英文品名'])
  return en ? `${cn}\n${en}` : cn || '—'
}

/** record_id → 发票号（全量数据里都有，合并列据此把技术 ID 翻成人看的号） */
const invoiceById = computed(() => {
  const map = new Map<string, string>()
  for (const rec of allRecords.value) {
    const invoice = textOrEmpty(rec.fields['发票号'])
    if (invoice) map.set(rec.record_id, invoice)
  }
  return map
})

/**
 * 合并主单/分单列显示的是**组内其他票**的发票号。
 *
 * 关联字段的原始值含自身 record_id，全列出来等于把当前票重复一遍；
 * 解析不到的成员（飞书返回了 id 但不在本表数据里）退回显示原 id，至少不丢信息。
 */
function mergePeerInvoices(row: BitableRecord, col: string): string {
  const ids = (row.link_ids[col] ?? []).filter((id) => id !== row.record_id)
  if (!ids.length) return ''
  return ids.map((id) => invoiceById.value.get(id) ?? id).join('、')
}

function columnWidth(col: string): number {
  // 260：未读点 + 10 位发票号 + 「组 · 10 位发票号（N票）」徽标，都要放得下且不裁切
  if (col === '发票号') return 260
  if (col === '货物数据') return 190
  if (col === '中文品名') return 160
  if (col === '预计提货时间') return 140
  if (col === '订舱状态' || col === '计划状态') return 100
  if (col === '分单号' || col === '订舱代理') return 130
  return 110
}

onMounted(async () => {
  try {
    await loadConfig()
  } catch (e) {
    console.error('[Feishu] 加载工作台配置失败', e)
    ElMessage.error('工作台配置加载失败')
    return
  }
  await loadAll()
})
</script>

<style scoped>
/* 业务页以表格为主：卡片内边距压到 12px，给数据多留几行 */
.wb-card :deep(.el-card__body) {
  padding: 12px;
}

.wb-header {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.wb-switcher {
  display: flex;
  flex: none;
  gap: 2px;
  padding: 2px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
}

.wb-switch-item {
  padding: 3px 14px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  text-decoration: none;
  border-radius: 6px;
  transition: all 0.15s;
}

.wb-switch-item.active {
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-bg-color);
  box-shadow: 0 1px 2px rgb(0 0 0 / 6%);
}

.wb-title-block {
  display: flex;
  flex: 1;
  gap: 10px;
  align-items: baseline;
  min-width: 0;
}

.wb-title {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}

.wb-subtitle {
  overflow: hidden;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-header-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.wb-search {
  width: 260px;
}

.wb-loading-hint {
  align-self: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.wb-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 14px;
}

.wb-filter-item {
  display: flex;
  gap: 6px;
  align-items: center;
}

.wb-filter-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.wb-filter-select {
  width: 140px;
}

.wb-view-toggle {
  margin-left: auto;
}

.wb-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.wb-stat {
  display: inline-flex;
  gap: 4px;
  align-items: baseline;
  padding: 2px 10px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  cursor: pointer;
  background: var(--el-fill-color-lighter);
  border: 1px solid transparent;
  border-radius: 12px;
}

.wb-stat:hover {
  background: var(--el-fill-color-light);
}

.wb-stat.is-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
}

.wb-stat b {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.wb-stat-warn {
  color: var(--el-color-warning);
}

.wb-stat-sub {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.wb-merge-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: var(--el-color-primary-light-9);
  border-radius: 6px;
}

.wb-merge-hint {
  font-size: 13px;
  font-weight: 600;
}

.wb-group-view {
  min-height: 200px;
}

.cell-invoice {
  display: flex;
  gap: 6px;
  align-items: center;
}

.cell-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.cell-unread-dot {
  flex: none;
  width: 6px;
  height: 6px;
  background: var(--el-color-danger);
  border-radius: 50%;
}

.cell-cargo {
  display: block;
  overflow: hidden;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.wb-footer-total {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

/* 紧凑行高：一屏尽量多放票（§5.1） */
.wb-table :deep(.el-table__cell) {
  padding: 3px 0;
}

.wb-table :deep(.el-table__row) {
  height: 34px;
}

/* 单元格单行省略；完整内容在悬停浮层里，可选中复制 */
.cell-ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<style>
/* 悬停浮层被 teleport 到 body，scoped 样式命不中 */
.wb-copy-pop .cell-copy-text {
  max-height: 280px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  user-select: text;
  cursor: text;
}
</style>

<style>
/* 合并组同色行背景：用非 scoped 样式，因为类名由 JS 动态返回给 ElTable。
 *
 * ⚠️ 表格**不能开 `stripe`**。Element Plus 的斑马纹规则是
 *   `.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell { background: var(--el-fill-color-lighter) }`
 * 特异性 (0,4,2) 且直接设 background，会压过下面只设 `--el-table-tr-bg-color` 变量（特异性 (0,1,0)）
 * 的规则 —— 于是同组里奇数行是组色、偶数行被斑马纹灰底盖掉，看起来就是「同组不是一个颜色」。
 * 组色本身已经承担了行的视觉分组，斑马纹与之语义重叠，故直接去掉。 */
.merge-row-0 {
  --el-table-tr-bg-color: var(--el-color-success-light-9);
}

.merge-row-1 {
  --el-table-tr-bg-color: var(--el-color-primary-light-9);
}

.merge-row-2 {
  --el-table-tr-bg-color: var(--el-color-warning-light-9);
}

.merge-row-3 {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}

.merge-row-4 {
  --el-table-tr-bg-color: var(--el-color-info-light-9);
}

.merge-row-5 {
  --el-table-tr-bg-color: var(--el-color-success-light-8);
}

.merge-row-6 {
  --el-table-tr-bg-color: var(--el-color-primary-light-8);
}

.merge-row-7 {
  --el-table-tr-bg-color: var(--el-color-warning-light-8);
}

/* 刚合并/拆出那票闪一下，让「改动落在哪一行」不用靠猜。
 * 动画直接写单元格背景（行背景是 tr 上的变量，盖不住），结束时不设 fill-mode，
 * 于是动画一停就回到组色/默认底色，不会残留。 */
.wb-anchor-row > td.el-table__cell {
  animation: wb-anchor-flash 2.2s ease-out;
}

@keyframes wb-anchor-flash {
  0%,
  35% {
    background-color: var(--el-color-primary-light-5);
  }

  100% {
    background-color: transparent;
  }
}
</style>
