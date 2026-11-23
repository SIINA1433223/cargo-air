<template>
  <div class="weida-workbench">
    <ElCard shadow="never" class="wb-card">
      <div class="wb-toolbar">
        <!--
          新建放在最左：录入是本页最高频的入口，不该混在搜索/筛选那排工具里。
          后端没配飞书表单视图（`can_create` 为假）时不显示——新建抽屉是照那个视图的字段
          渲染的，没有视图就是一个空抽屉，点开只会一脸茫然。
        -->
        <ElButton v-if="config?.can_create" type="primary" :icon="Plus" @click="createVisible = true">
          {{ config?.create_label ?? '新建记录' }}
        </ElButton>

        <ElInput
          v-model="searchInput"
          class="wb-search"
          placeholder="搜索"
          clearable
          :prefix-icon="Search"
          @keyup.enter="applySearch"
          @clear="applySearch" />

        <ElSelect
          v-if="views.length"
          v-model="activeViewId"
          class="wb-view-select"
          clearable
          placeholder="筛选视图"
          @change="onViewChange">
          <ElOption v-for="v in views" :key="v.id" :label="v.name" :value="v.id" />
        </ElSelect>

        <ElButton :icon="Filter" @click="filterVisible = true">
          筛选
          <template v-if="conditions.length">（{{ conditions.length }}）</template>
        </ElButton>
        <ElButton :icon="Refresh" :loading="loading" @click="reload">刷新</ElButton>
        <ElButton :icon="Setting" @click="configVisible = true">自定义列</ElButton>
        <ElButton :icon="Brush" :type="paintVisible ? 'primary' : 'default'" @click="paintVisible = !paintVisible">
          填色
        </ElButton>

        <!-- 填色工具：纯度、颜色、橡皮，只在填色模式下出现，平时不占地方 -->
        <span v-if="paintVisible" class="wb-paint">
          <ElRadioGroup v-model="paintScope" size="small">
            <ElRadioButton value="cell">单元格</ElRadioButton>
            <ElRadioButton value="row">整行</ElRadioButton>
            <ElRadioButton value="column">整列</ElRadioButton>
          </ElRadioGroup>
          <span
            v-for="c in PAINT_PRESETS"
            :key="c"
            class="paint-swatch"
            :class="{ 'is-active': paintColor === c }"
            :style="{ backgroundColor: c }"
            :title="c"
            @click="paintColor = c" />
          <ElColorPicker v-model="paintColor" size="small" />
          <ElButton link type="danger" size="small" @click="clearPaint">清空涂色</ElButton>
          <span class="paint-tip">点格子刷色，同色再点即擦掉</span>
        </span>

        <span class="wb-count">
          共 {{ filteredRecords.length }} 条
          <template v-if="truncated">（仅加载前 {{ allRecords.length }} 条）</template>
        </span>
      </div>

      <ElTable
        v-loading="loading"
        :data="pagedRecords"
        class="wb-table"
        :class="{ 'is-painting': paintVisible }"
        stripe
        border
        size="small"
        row-key="record_id"
        :row-style="rowStyle"
        :cell-style="cellStyle"
        @cell-click="onStatusCellClick">
        <!--
          行高把手列：贴在最左，不显示表头、不参与列拖序。
          单独占一列是因为「拖行高」需要一个不在文本里的抓手，直接拖单元格边框会和
          选择文字打架。
        -->
        <ElTableColumn width="28" fixed="left" class-name="col-grip" label-class-name="col-grip">
          <template #header>
            <span class="grip-hint" title="按住每行左侧的 ⠿ 上下拖动，可单独调这一行的高度">⇕</span>
          </template>
          <template #default="{ row }">
            <!-- `@click.stop`：拖行高松手会补一个 click，别让它被当成点格子 -->
            <span
              class="row-grip"
              title="按住上下拖动，调整本行高度"
              @mousedown.stop.prevent="onRowResizeStart($event, row)"
              @click.stop>
              ⠿
            </span>
          </template>
        </ElTableColumn>

        <!--
          「单票状态」固定列（只有开着 `single_status` 的后端才下发这个值）。

          固定在最左、排在**用户自定义列之前**：这是催办用的列，跟着列配置被拖到看不见的
          地方就白做了。也因此不进 `WeidaColumnConfig` 的可选字段——它是派生值，不是飞书字段，
          让人把「状态」当列挑出来一份，就会出现两个状态列。
        -->
        <ElTableColumn
          v-if="hasStatus"
          width="110"
          fixed="left"
          label="单票状态"
          :cell-class-name="alertCellClass">
          <template #default="{ row }">
            <WeidaStatusTag :status="row.single_status" />
          </template>
        </ElTableColumn>

        <ElTableColumn
          v-for="(col, i) in columns"
          :key="columnKeys[i]"
          :label="columnLabel(col)"
          :property="colId(col)"
          :width="resolvedWidth(col)"
          :resizable="false"
          :show-overflow-tooltip="isStaticCell(col)">
          <!--
            自定义表头：列名可拖动改列序，右缘有条细把手可拖宽。
            关掉 ElTable 自带的 `resizable`——它也能拖宽，但拖完只改 DOM 不落盘，
            刷新就回去了，两个把手并排只会让人以为坏了。
          -->
          <template #header>
            <div
              class="col-head"
              draggable="true"
              @dragstart="onColDragStart(i)"
              @dragover.prevent="onColDragOver(i)"
              @drop.prevent="onColDrop"
              @dragend="onColDragEnd">
              <span class="col-head-label">{{ columnLabel(col) }}</span>
              <span
                class="col-resizer"
                title="拖动调整列宽"
                @mousedown.stop.prevent="onResizeStart($event, col)"
                @click.stop />
            </div>
          </template>
          <template #default="{ row }">
            <!--
              整格一个点击区：填色模式下点就是刷色，可编辑格点开编辑器。
              这两种都要吞掉点击（否则会顺带把详情抽屉打开）；**只读格不吞**，
              让点击冒泡到整行，沿用「点一行开详情」的老操作。
            -->
            <div class="cell-wrap" @click="onCellClick($event, row, col)">
              <!-- 拼接列：模板插值，只读（拼接串拆不回原字段，改内容请改组成它的字段） -->
              <span v-if="col.kind === 'template'" class="cell-template">
                {{ renderTemplate(col.expression, row.fields) || '—' }}
              </span>
              <!--
                可编辑字段：**点一下才挂控件**。
                过去每格常驻一个 Element Plus 表单控件（50 行 × 若干列 ≈ 三百个），
                光是初始化就把浏览器主线程占死、页面卡到打不开，所以改成点开才渲染，
                平时只显示文本。
              -->
              <div
                v-else-if="isInlineEditable(nameOf(col))"
                class="cell-edit"
                :class="[
                  cellClass(row.record_id, nameOf(col)),
                  { 'is-editing': isEditing(row.record_id, nameOf(col)) },
                ]">
                <WeidaCellEditor
                  v-if="isEditing(row.record_id, nameOf(col))"
                  :meta="metaOf(nameOf(col))"
                  :value="row.fields[nameOf(col)]"
                  :role="role"
                  :user-value="userCellValue(row, nameOf(col))"
                  :user-names="userCellNames(row, nameOf(col))"
                  @commit="(v, refs) => commitCell(row, nameOf(col), v, refs)"
                  @close="stopEdit" />
                <span v-else class="cell-display" :class="{ 'cell-empty': isEmpty(row.fields[nameOf(col)]) }">
                  {{ formatFieldValue(row.fields[nameOf(col)]) }}
                </span>
              </div>
              <!--
                「分单号」：订单表上这一列是**只读的查找引用**——飞书把挂在这张订单上的每条
                iFreight 的分单号 `LISTCOMBINE` 成逗号串（`TAOW044871,TAOW044872`），
                那份值**不带 record_id**，拆不回原记录。所以这一列不摆只读文本，点开是个
                逐条改的小面板：一张订单挂两条时，两个输入框各写各的 iFreight 记录
                （见 `loadFendan` / `commitFendan`），改完订单表这列由飞书公式自动刷新。
                只对配了 iFreight 反查页的工作台生效（`isFendanColumn`）。
              -->
              <ElPopover
                v-else-if="isFendanColumn(col)"
                :width="340"
                :persistent="false"
                trigger="click"
                placement="bottom-start"
                popper-class="fendan-popper"
                @show="loadFendan(row)">
                <template #reference>
                  <span class="cell-fendan" :class="{ 'cell-empty': isEmpty(row.fields[nameOf(col)]) }">
                    {{ formatFieldValue(row.fields[nameOf(col)]) || '—' }}
                    <EditPen class="cell-fendan-icon" />
                  </span>
                </template>
                <div class="fendan-panel">
                  <div class="fendan-title">分单号 · 改的是 iFreight 表里每条记录</div>
                  <div v-if="fendanLoading" class="fendan-empty">读取中…</div>
                  <div v-else-if="!fendanRows.length" class="fendan-empty">这张订单还没有 iFreight 数据</div>
                  <div v-for="(fr, seq) in fendanRows" v-else :key="fr.recordId" class="fendan-row">
                    <span class="fendan-no">第 {{ seq + 1 }} 条</span>
                    <ElInput
                      :model-value="fr.value"
                      size="small"
                      placeholder="分单号"
                      :loading="fendanSaving === fr.recordId"
                      @change="(v: string) => commitFendan(row, fr, v)" />
                  </div>
                </div>
              </ElPopover>
              <!-- 其余（公式/查找/自动编号/关联/附件）只读展示 -->
              <span v-else :class="{ 'cell-empty': isEmpty(row.fields[nameOf(col)]) }">
                {{ formatFieldValue(row.fields[nameOf(col)]) }}
              </span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="" width="80" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click.stop="openDetail(row)">查看</ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :description="allRecords.length ? '没有符合筛选条件的记录' : '暂无记录'" />
        </template>
      </ElTable>

      <ElPagination
        v-if="filteredRecords.length > pageSize"
        v-model:current-page="pageIndex"
        class="wb-pagination"
        layout="total, prev, pager, next, sizes"
        :total="filteredRecords.length"
        :page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        @size-change="pageIndex = 1" />
    </ElCard>

    <ElDialog v-model="alertDialogVisible" title="这票的提醒" width="480px">
      <div v-if="alertDialogRow" class="wb-alerts">
        <div
          v-for="(a, i) in alertsOf(alertDialogRow)"
          :key="i"
          class="wb-alert-item"
          :class="a.severity">
          <div class="wb-alert-head">
            <span class="wb-alert-title">{{ a.title }}</span>
            <span class="wb-alert-badge">{{
              a.severity === 'critical' ? '失败/待处理' : a.severity === 'warning' ? '待处理' : '已完成'
            }}</span>
          </div>
          <div class="wb-alert-detail">{{ a.detail }}</div>
          <ElButton
            v-if="!isDismissed(alertDialogRow, a)"
            size="small"
            text
            @click="dismissAlert(alertDialogRow, a)">
            取消提醒（数据再变会回来）
          </ElButton>
          <span v-else class="wb-alert-done">已取消</span>
        </div>
      </div>
    </ElDialog>

    <!--
      `link-blocks`：详情页要**单独成一个子详情**的关联字段。商机上挂着的航线报价 /
      对客方案（一票一般一两条），在飞书里也是各占一个 Tab，索引页看不到，只有这里能看。
      由页面按业务点名，抽屉本身不认字段名。
    -->
    <WeidaRecordDrawer
      v-model:visible="drawerVisible"
      :role="role"
      :config="config"
      :record="currentRecord"
      :link-blocks="DETAIL_LINK_BLOCKS"
      :link-columns="LINK_COLUMNS"
      :link-detail-hidden="LINK_DETAIL_HIDDEN"
      :link-create-back-refs="LINK_CREATE_BACK_REFS"
      :link-action-buttons="LINK_ACTION_BUTTONS"
      :link-action-fields="LINK_ACTION_FIELDS"
      :link-button-dialogs="LINK_BUTTON_DIALOGS"
      :link-field-splits="LINK_FIELD_SPLITS"
      :link-option-filters="LINK_OPTION_FILTERS" />

    <WeidaRecordCreateDrawer
      v-model:visible="createVisible"
      :role="role"
      :config="config"
      @created="handleRecordCreated" />

    <WeidaColumnConfig
      v-model:visible="configVisible"
      :role="role"
      :fields="allFields"
      :columns="columns"
      :default-column-names="config?.columns ?? []"
      :user-name="userName"
      @save="handleSaveColumns"
      @reset="handleResetColumns"
      @field-created="handleFieldCreated" />

    <WeidaFilterDialog
      v-model:visible="filterVisible"
      :fields="allFields"
      :columns="columns"
      :conditions="conditions"
      :conjunction="conjunction"
      :views="views"
      :active-view-id="activeViewId"
      @apply="handleApplyFilter"
      @save-view="handleSaveView"
      @select-view="handleSelectView"
      @remove-view="handleRemoveView" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  ElButton,
  ElCard,
  ElColorPicker,
  ElEmpty,
  ElInput,
  ElMessage,
  ElOption,
  ElPagination,
  ElPopover,
  ElRadioButton,
  ElRadioGroup,
  ElSelect,
  ElTable,
  ElTableColumn,
} from 'element-plus'
import { Brush, EditPen, Filter, Plus, Refresh, Search, Setting } from '@element-plus/icons-vue'
import FeishuWorkbenchAPI, {
  FIELD_TYPE,
  type BitableRecord,
  type FieldMeta,
  type LinkOptionFilter,
  type UserRef,
  type WorkbenchConfig,
} from '@/api/module_feishu/workbench'
import { useUserStore } from '@/store/modules/user.store'
import {
  COLUMN_WIDTH_MAX,
  COLUMN_WIDTH_MIN,
  ROW_HEIGHT_MAX,
  ROW_HEIGHT_MIN,
  blankFieldName,
  cellColorKey,
  clampSize,
  colId,
  columnTitle,
  defaultColumns,
  formatFieldValue,
  getViewConfig,
  matchesFilter,
  renderTemplate,
  saveViewConfig,
  type FilterCondition,
  type FilterView,
  type ViewColumn,
} from '@/utils/view-config'
import WeidaCellEditor from './WeidaCellEditor.vue'
import WeidaColumnConfig from './WeidaColumnConfig.vue'
import WeidaFilterDialog from './WeidaFilterDialog.vue'
import WeidaRecordCreateDrawer from './WeidaRecordCreateDrawer.vue'
import WeidaRecordDrawer from './WeidaRecordDrawer.vue'
import WeidaStatusTag from './WeidaStatusTag.vue'

defineOptions({ name: 'WeidaWorkbench' })

const props = defineProps<{ role: string }>()

const userStore = useUserStore()
/** 自定义列配置按「用户 × 工作台」分桶，共享机器上换人登录互不干扰 */
const userId = computed(() => userStore.info.id ?? 'anonymous')
/**
 * 当前用户的显示名。建空列时拼进飞书字段名（`{列名}_{用户名}_{短码}`），用来区分
 * 「这列是谁建的」；`name` 是昵称，为空就退回登录名。
 */
const userName = computed(() => userStore.info.name || userStore.info.username || '用户')

/** 一次拉取的页大小。飞书 `page_size` 上限即 500。 */
const PAGE_SIZE = 500
/** 翻页上限，防止后端 `has_more` 异常时死循环 */
const MAX_PAGES = 40

/** 能在表格格子里直接编辑的字段类型（附件/关联要专门控件，留到详情页）。 */
const INLINE_TYPES: readonly number[] = [
  FIELD_TYPE.TEXT,
  FIELD_TYPE.NUMBER,
  FIELD_TYPE.SINGLE_SELECT,
  FIELD_TYPE.MULTI_SELECT,
  FIELD_TYPE.DATE,
  FIELD_TYPE.CHECKBOX,
  FIELD_TYPE.PHONE,
  FIELD_TYPE.URL,
  // 人员：控件是下拉，格子放得下；值用 open_id（见 userCellValue）
  FIELD_TYPE.USER,
]

/** 字段不在本表元数据里时的占位（只读），避免到处判空。 */
const UNKNOWN_META: FieldMeta = {
  name: '',
  type: 0,
  ui_type: 'Unknown',
  read_only: true,
  editable: false,
  options: [],
  multiple: true,
  description: '',
}

/**
 * 详情页要**单独成一个子详情**的关联字段（见抽屉的 `linkBlocks`）。
 *
 * 只放「挂在商机上、索引页看不到」的那种关联：航线报价与对客方案各是自己一张表，
 * 飞书里嵌在商机详情面板下方、各自一个 Tab。**不含**合出主单/合出分单——那两个是合并
 * 算法的工作区（值就是同组的兄弟单），铺在详情页里既长又没意义。
 *
 * 「订单表」放在最后：它不是一张要逐条看的子表，而是**这一票的订单本身**（见
 * `LINK_FIELD_SPLITS`，它会被拆成「订仓单」「交接操作」两页）。索引页上点「确认订单」建出
 * 一条订单之后，商机详情里就能顺着这个关联字段直接改那张单。
 *
 * 顺序即子详情的顺序，排在抽屉自己的字段页（商机详情 / 货物详情）之后。
 */
const DETAIL_LINK_BLOCKS: readonly string[] = ['航线报价', '对客方案', '订单表']

/**
 * 把一个关联字段拆成几页字段页（见抽屉的 `linkFieldSplits`）。
 *
 * 「订仓单」「交接操作」看的是**同一条订单**（商机上的「订单表」双向关联），只是一个管头程
 * 订舱、一个管交接与国内操作，要摆的字段不同。不拆的话它们只能挤成一个叫「订单表」的页——
 * 68 个字段一坨铺开，两拨人各找各的，谁都得在一屏里翻。
 *
 * 字段名逐字照**订单表** `tblqNRxcBz4HRbY6` 的 `field_name`（改名前先回飞书对一遍）。
 * 写错的会被跳过，不会让整页打不开，但那一格就悄悄没了。
 *
 * 两页的清单与顺序都来自业务方（口头报的字段名，照下面对齐到订单表的 `field_name`）。
 * 「供应商」两页都有：订舱看的是订舱代理那几家，交接看的是同一批人，筛法一样（见
 * `LINK_OPTION_FILTERS`）。
 *
 * 有意不收进来的：三个 Stage 字段（`国内客服`/`操作`/`航线`，type 24 读不出值，摆上就是空白格）。
 */
const LINK_FIELD_SPLITS: Record<string, readonly { label: string; fields: readonly string[] }[]> = {
  订单表: [
    {
      // 字段与顺序均为业务方给的口径（与飞书「订舱单」那个节点一致）。
      // 几处名字与口头说法不同，照的是订单表的 field_name：
      //   生成订仓单     → `生成订舱单`（**复选框**，业务方把原来那个 3001 按钮换掉了：
      //                    按钮在飞书里点一次触发自动化，本系统里没接口能按，一直是灰的；
      //                    改复选框后这一格能直接勾，勾完由飞书自动化接手。那个按钮现在改名
      //                    `生成订舱单 按钮`，不再收进任何清单）
      //   订仓单附件     → `订舱单`（附件字段本身就叫这个）
      //   等级比例要求   → `等级、比例要求`
      //   如仓操作时间   → `入仓操作时间`
      //   启运地         → `起运地`
      //   货物品名       → `货物品名（货物特殊属性）`（表里另有一个 `货物特殊属性`，不是这个）
      label: '订仓单',
      fields: [
        '关联商机',
        '对客方案',
        '生成订舱单',
        '订舱单',
        '成本金额',
        '等级、比例要求',
        '成本币种',
        '航司',
        '入仓操作时间',
        '操作要求',
        '特别注意',
        '国内操作',
        '供应商',
        '起运地',
        '目的地',
        '航班',
        '货物数据',
        '货物品名（货物特殊属性）',
      ],
    },
    {
      // 字段与顺序均为业务方给的口径（与飞书「交接操作」那个节点一致）。
      // 只有一处名字与口头说法不同，照的是订单表的 field_name：
      //   客户 ref no. → `客户 REF NO.`（表里是全大写）
      label: '交接操作',
      fields: [
        '客户 REF NO.',
        '交接给国内操作',
        '国内操作',
        '供应商',
        '国外代理',
        '业务员',
        '客户核心关注点',
        '备注',
        '提货车队',
        '提货信息',
        '提货费',
        '相关资料',
        '代理deadline',
        '最晚到港时间',
        '要求到门时间',
        '主单号',
      ],
    },
  ],
}

/**
 * 二级关联字段下拉里，候选自身的业务约束（见抽屉的 `linkOptionFilters`）。
 *
 * 「供应商」是订单表上一个指向客商表的单向关联，客商表里客户、报关行、车队、国外代理都
 * 混在一张表上（「客商角色」是多选，一家客商可以身兼数职），而这里的供应商只认**订舱代理**。
 * 不筛的话下拉里 192 家客商全在里面。
 *
 * 值照飞书 `客商角色` 的选项原文写——是**订舱**代理，不是口头常说的「订仓代理」。
 *
 * 「国外代理」同表同列，只是认的另**一个**选项。它俩指向的是同一张客商表（订单表上两个
 * 单向关联），不各自点名的话谁也筛不出来。角色是多选，一家客商可以既是订舱代理又是国外
 * 代理，两边都该出现——按「含有其中一项」判，不会漏。
 */
const LINK_OPTION_FILTERS: Record<string, LinkOptionFilter> = {
  供应商: { field: '客商角色', values: ['订舱代理'] },
  国外代理: { field: '客商角色', values: ['国外代理'] },
}

/**
 * 关联记录渲染成**表格**时的列（见抽屉的 `linkColumns`）。
 *
 * 航线报价那张表 28 个字段，整排铺开一屏放不完、格子还被拉得又宽又空。表格里只摆这几个
 * 关键字段，一行一条报价；点格子就地改，只有最右边的箭头才展开其余 20 个字段。
 * 顺序即列序（先看有报价没、再看给谁做、多少钱）。
 *
 * 「对客方案」也走表格：那张表 25 个字段里，一票方案真正要动的是「给客户报了多少、
 * 什么时候到期」，其余是审批流转与附件——折叠卡片要一条条点开才看得见报价，比表格慢。
 *
 * 「对客方案」这一组里，四处**本来就点不动或改不了**：`添加费用明细` 是自动化按钮（3001，
 * 本系统给它配了对话框，见 `LINK_BUTTON_DIALOGS`），`启运港`/`目的港`/`最早航班时间` 是查找列
 * （19，飞书侧也只读）。摆出来是因为业务看方案时一次要看全，表格里它们只是占位。
 *
 * 能改的：`方案名称`、`对客报价`、`报价币种`、`报价有效期至` 这四列，以及 `生成报价单`
 * ——它**以前是 3001 按钮**（在飞书里点一次触发自动化，本系统里一直是灰的），业务方后来
 * 把它换成了**复选框**，于是表格单元格那套 `CHECKBOX → ElCheckbox` 渲染自动把它变成可勾的。
 * 旧按钮还在表里，改名 `生成报价单 按钮`，不在这份清单里，不会出现。
 *
 * 「iFreight」是**反查页**（`RelatedBlock`，不来自本表的关联字段），走的是同一套表格模式：
 * 一行一条 iFreight，点最右箭头展开看其余字段。这一页的字段清单在后端 `_WD_OP_RELATED`
 * （33 个），操作一眼要的是「同步成没成、单号是什么、走哪条线、发的谁的货」，其余 27 个
 * （航线城市、件重体、付款方式、一堆当事人名称、飞书那边「复制提单编组」「投保编组」里的
 * 复制提单/投保条款/币制/报关金额…）展开再看不迟——展开体里的控件是点开才挂的，折叠时
 * 这一页只有文本。
 */
const LINK_COLUMNS: Record<string, readonly string[]> = {
  航线报价: [
    '供应商报价信息',
    '生成对客方案初稿',
    '供应商',
    '路线/中转点',
    '成本金额',
    '等级、比例要求',
    '成本币种',
    '最早航班时间',
  ],
  对客方案: [
    '方案编号',
    '对客报价',
    '报价币种',
    '报价有效期至',
    '方案名称',
    '添加费用明细',
    '启运港',
    '目的港',
    '最早航班时间',
    '生成报价单',
  ],
  // 字段名逐字照 iFreight 表 `tbl99ei11SxswR8v`（与后端 `_WD_OP_RELATED.fields` 同一份口径）
  iFreight: ['操作编号', '同步状态', '主单号', '分单号', '运输方式', '发货人名称'],
  // iFreight 详情面板里的两个字段编组（截图给的）：各占一个子 Tab，字段名逐字照飞书。
  // 摆成表而不是折叠卡片——它们和 `iFreight` 是同一批行（一张订单挂两条是常态），
  // 用同一种版式才看得出「这几行是同一批单据的不同侧面」。
  // 每张表都以「分单号」打头：业务要求分单号在每个子 Tab 里都看得见（截图里它也在最前）。
  复制提单编组: ['分单号', '复制提单', '复制提单号'],
  投保编组: ['分单号', '投保', '投保条款', '币制', '报关金额', '启运地'],
}

/**
 * 报价**展开体**里不显示的字段（见抽屉的 `linkDetailHidden`）。
 *
 * 展开体只该放「表格那 8 列之外、又真需要看」的字段。这几个要么本系统改不了
 * （原始报价附件、供应商编码、被方案采用）、要么业务上不看（报价状态、报价负责人、报价有效期），
 * 摆着只是把 20 个格子撑成 20 多行。
 *
 * 注意：**不摆出来就等于本系统内改不了**，要改这几个字段得回飞书。
 */
const LINK_DETAIL_HIDDEN: Record<string, readonly string[]> = {
  航线报价: ['报价状态', '原始报价附件', '报价负责人', '被方案采用', '报价有效期', '供应商编码'],
}

/**
 * 关联分区右上角「新增」按钮：关联字段名 → 新记录在**关联表**里回指本商机的字段名。
 *
 * 只给能手工补的关联配。「对客方案」不配：它是从报价生成的，手建一条空的方案没有意义。
 */
const LINK_CREATE_BACK_REFS: Record<string, string> = {
  航线报价: '关联商机',
}

/**
 * 关联分区**上方**的动作按钮（关联字段名 → 商机主表上的**按钮字段**名）。
 *
 * 只收按钮字段（name 在 `detail_fields` 里且 type 是 3001），写别的类型不会渲染。
 *
 * **暂时是空的**：这里原来摆的是对客方案的「发起报价审批」，业务方把它在飞书里改成了
 * 复选框（旧按钮还在表里，叫「发起报价审批」但已不再用）。换成复选框之后要走
 * `LINK_ACTION_FIELDS` 那条路——同一个位置，但字段能直接勾。留着这个常量是因为抽屉
 * 那条渲染路径还在，以后再冒出别的按钮直接加回来。
 */
const LINK_ACTION_BUTTONS: Record<string, readonly string[]> = {}

/**
 * 关联分区**上方**、本系统**能直接改**的字段（关联字段名 → 商机主表字段名）。
 *
 * 与 `LINK_ACTION_BUTTONS` 同一个位置、同一个理由：这些字段挂在**商机主表**上（不是
 * 对客方案表），作用的却是这一整块方案列表——「发起审批」审的是这条商机下的全部方案，
 * 飞书自己的记录详情面板也把它摆在方案列表上方。
 *
 * 字段名逐字照商机主表的 `field_name`。**必须是非按钮字段**：按钮仍然只能去飞书点，
 * 摆成可点的控件是骗人；名字写错或类型不对就当没配，那一格不出现（不会让整页打不开）。
 *
 * 注意飞书侧字段类型有 **10 分钟 Redis 缓存**（`lark_fields:*`）——业务刚把按钮改成
 * 复选框时，后端可能还在拿旧的字段定义，界面上就还是「什么都不渲染」。等缓存过期或
 * 清掉那几个键再刷新即可。
 */
const LINK_ACTION_FIELDS: Record<string, readonly string[]> = {
  对客方案: ['发起审批'],
}

/**
 * 哪些**按钮字段**改由本系统自己完成（关联分区 → 按钮名 → 子表怎么接）。
 *
 * 「添加费用明细」在飞书里只是弹一个「批量添加对客费用明细」的表单，没有非它不可的自动化，
 * 所以能整套搬进来：点开一个对话框，把这条对客方案的费用明细一行条地录/改/删。
 *
 * 两个字段名都取自数据模型：`费用明细` 挂在**对客方案表**上（所以对话框要拿对客方案表 id
 * 去问后端，而不是本工作台的表），`关联的对客方案` 是新明细回指当前方案的那一列。
 * 不配的按钮保持原样（灰的，去飞书点）。
 */
const LINK_BUTTON_DIALOGS: Record<string, Record<string, { linkField: string; backRefField: string }>> = {
  对客方案: {
    添加费用明细: { linkField: '费用明细', backRefField: '关联的对客方案' },
  },
}

const config = ref<WorkbenchConfig | null>(null)
const allRecords = ref<BitableRecord[]>([])
const loading = ref(false)
/** 数据量超过拉取上限时为真（此时本地筛选只覆盖已加载的部分，界面要如实说明） */
const truncated = ref(false)

const searchInput = ref('')
const appliedSearch = ref('')

const pageIndex = ref(1)
const pageSize = ref(50)

const drawerVisible = ref(false)
const currentRecord = ref<BitableRecord | null>(null)
const configVisible = ref(false)
const filterVisible = ref(false)
const createVisible = ref(false)

/** 本地自定义列配置；null = 没配过，用后端默认列 */
const viewColumns = ref<ViewColumn[] | null>(null)
/** 多条件筛选（当前生效的） */
const conditions = ref<FilterCondition[]>([])
const conjunction = ref<'and' | 'or'>('and')
/** 保存下来的命名筛选视图 */
const views = ref<FilterView[]>([])
const activeViewId = ref('')

/** 单元格写入状态：`{record_id}:{字段}` → saving/saved/error */
const cellState = ref<Record<string, 'saving' | 'saved' | 'error'>>({})

/**
 * 当前正在编辑的格子，`{record_id}:{字段}`；空串 = 没在编辑。
 *
 * 只允许一个格子开着编辑器：表格里同时挂几百个 Element Plus 表单控件会把主线程
 * 占死（页面卡到打不开），所以默认只渲染文本，点开哪一格才挂哪一格的控件。
 */
const editingKey = ref('')

/**
 * 像 Excel 那样的布局个性化，全部只存本地、**不写飞书**。
 *
 * 键：行用 `record_id`（翻页筛选都不会变），列用 `colId(col)`（不能用下标，
 * 列一重排就整体错位）。详见 `view-config.ts` 里 `ViewConfig` 的注释。
 */
const rowHeights = ref<Record<string, number>>({})
const columnWidths = ref<Record<string, number>>({})
const cellColors = ref<Record<string, string>>({})
const rowColors = ref<Record<string, string>>({})
const columnColors = ref<Record<string, string>>({})

/** 填色模式：开着的时候点格子是刷色，不是编辑。 */
const paintVisible = ref(false)
const paintColor = ref('#ffe58f')
const paintScope = ref<'cell' | 'row' | 'column'>('cell')

/** 常用底色，省得每次去调色板；都是浅色，压不住单元格里的黑字。 */
const PAINT_PRESETS: readonly string[] = ['#ffe58f', '#b7eb8f', '#91d5ff', '#ffccc7', '#d3adf7', '#ffd6e7']

/**
 * 当前生效的列：本地配过就用本地，否则用后端默认列。
 * 计算属性里读 `userId`，换用户登录后会自动重算。
 */
const columns = computed<ViewColumn[]>(() => {
  if (viewColumns.value && viewColumns.value.length) return viewColumns.value
  return defaultColumns(config.value?.columns ?? [])
})

/**
 * 这一页有没有「单票状态」可显示。
 *
 * 按**实际拿到的数据**判断而不是按角色判断：值由后端 `WorkbenchConfig.single_status` 决定，
 * 前端再写一份「哪个角色有」等于维护第二份开关，两边不同步时就会多出一个整列空白的列。
 */
const hasStatus = computed(() => allRecords.value.some((record) => record.single_status))

/** 本表全部字段元数据（老配置没有 `fields` 时用分组拼回来）。 */
const allFields = computed<FieldMeta[]>(() => {
  const cfg = config.value
  if (!cfg) return []
  if (cfg.fields?.length) return cfg.fields
  return [...cfg.base_fields, ...cfg.groups.flatMap((g) => g.fields)]
})

function metaOf(name: string): FieldMeta {
  return allFields.value.find((f) => f.name === name) ?? { ...UNKNOWN_META, name }
}

/**
 * 取值用的**真实飞书字段名**。
 *
 * 空列不能直接给 `col.label`——那只是用户看到的列名，落在飞书里的字段名是
 * `blankFieldName(col)` 拼出来的长名字，取错了整列都会是空的。
 */
function nameOf(col: ViewColumn): string {
  if (col.kind === 'template') return ''
  return col.kind === 'blank' ? blankFieldName(col) : col.name
}

function isInlineEditable(name: string): boolean {
  if (!name) return false
  const meta = metaOf(name)
  return meta.editable && INLINE_TYPES.includes(meta.type)
}

/** 拼接列与只读列走 tooltip 截断；可编辑格子不能被 tooltip 罩住。 */
function isStaticCell(col: ViewColumn): boolean {
  return col.kind === 'template' || !isInlineEditable(nameOf(col))
}

/**
 * 列表 key。用稳定 `colId` 而不是下标——拖拽改列序时下标全变，按下标当 key
 * 会让 Vue 把每一列都当新的重建一遍，列里的编辑态全丢。
 *
 * 重名只在字段配置异常时才可能（列配置弹窗会挡掉重复选同一字段），兜一下
 * 加个序号，免得出现重复 key 警告。
 */
const columnKeys = computed(() => {
  const seen = new Map<string, number>()
  return columns.value.map((col) => {
    const id = colId(col)
    const n = (seen.get(id) ?? 0) + 1
    seen.set(id, n)
    return n === 1 ? id : `${id}#${n}`
  })
})

/** 列头显示的文字：空列/拼接列显示用户自己起的名字，真字段显示字段名。 */
function columnLabel(col: ViewColumn): string {
  return columnTitle(col)
}

/** 没被拖过的列回落到这个默认宽度。 */
function columnWidth(col: ViewColumn): number {
  if (col.kind === 'template') return 200
  if (col.kind === 'blank') return 180
  return columnLabel(col).length > 8 ? 160 : 120
}

function resolvedWidth(col: ViewColumn): number {
  return columnWidths.value[colId(col)] ?? columnWidth(col)
}

/** 拼接列按列名索引，供筛选时把拼接列也当成可筛字段。 */
const templateByLabel = computed(() => {
  const m = new Map<string, string>()
  for (const c of columns.value) {
    if (c.kind === 'template') m.set(c.label, c.expression)
  }
  return m
})

/**
 * 取某行在某列上的值（筛选用）。
 *
 * 拼接列优先：它是算出来的、不在 `row.fields` 里，只能现渲染。
 * 若某个拼接列的列名与真字段重名，以拼接列为准。
 */
function valueOf(row: BitableRecord, field: string): unknown {
  const expression = templateByLabel.value.get(field)
  if (expression !== undefined) return renderTemplate(expression, row.fields)
  return row.fields[field]
}

function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)
}

/** ── 本地配置读写 ─────────────────────────────────────────────── */

/** 空表存成 `undefined`：没涂过色就别往 localStorage 里塞五个空对象。 */
function nonEmpty<T>(m: Record<string, T>): Record<string, T> | undefined {
  return Object.keys(m).length ? m : undefined
}

function persist() {
  saveViewConfig(userId.value, props.role, {
    columns: viewColumns.value ?? [],
    views: views.value,
    activeViewId: activeViewId.value || undefined,
    rowHeights: nonEmpty(rowHeights.value),
    columnWidths: nonEmpty(columnWidths.value),
    cellColors: nonEmpty(cellColors.value),
    rowColors: nonEmpty(rowColors.value),
    columnColors: nonEmpty(columnColors.value),
  })
}

/** 从 localStorage 载入该用户在当前工作台的列配置、筛选视图与布局（行高列宽颜色）。 */
function loadViewConfig() {
  const cfg = getViewConfig(userId.value, props.role)
  viewColumns.value = cfg?.columns?.length ? cfg.columns : null
  views.value = cfg?.views ?? []
  activeViewId.value = cfg?.activeViewId ?? ''
  rowHeights.value = cfg?.rowHeights ?? {}
  columnWidths.value = cfg?.columnWidths ?? {}
  cellColors.value = cfg?.cellColors ?? {}
  rowColors.value = cfg?.rowColors ?? {}
  columnColors.value = cfg?.columnColors ?? {}
  applyActiveView()
}

/** 把当前选中视图的条件灌进筛选状态；没有选中视图就清空。 */
function applyActiveView() {
  const active = views.value.find((v) => v.id === activeViewId.value)
  conditions.value = active ? active.conditions.map((c) => ({ ...c })) : []
  conjunction.value = active ? active.conjunction : 'and'
}

function handleSaveColumns(next: ViewColumn[]) {
  viewColumns.value = next
  persist()
}

function handleResetColumns() {
  viewColumns.value = null
  persist()
}

/** 新建/改名了飞书字段：字段元数据与记录投影都要重新拉，否则新列取不到值。 */
async function handleFieldCreated() {
  await Promise.all([loadConfig(), loadRecords()])
}

/**
 * 建好一条记录：重新拉列表（数据源是飞书，写完再读就是「系统和飞书都有」了）。
 *
 * 回到第 1 页：新记录排在哪儿由服务端排序决定，停在当前页大概率看不见它。
 * 当前筛选条件**不动**——自动清掉用户设的筛选比「新记录没露脸」更让人意外。
 */
async function handleRecordCreated() {
  pageIndex.value = 1
  await loadRecords()
}

function handleApplyFilter(payload: { conditions: FilterCondition[]; conjunction: 'and' | 'or' }) {
  conditions.value = payload.conditions
  conjunction.value = payload.conjunction
  // 手动改过条件就不再挂在某个命名视图上
  activeViewId.value = ''
  pageIndex.value = 1
  persist()
}

function handleSaveView(payload: { name: string; conditions: FilterCondition[]; conjunction: 'and' | 'or' }) {
  // 同名即覆盖：改完条件再存一次同名视图，符合直觉
  const existing = views.value.find((v) => v.name === payload.name)
  const id = existing?.id ?? `v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
  const view: FilterView = { id, name: payload.name, conditions: payload.conditions, conjunction: payload.conjunction }
  views.value = existing ? views.value.map((v) => (v.id === id ? view : v)) : [...views.value, view]
  conditions.value = payload.conditions
  conjunction.value = payload.conjunction
  activeViewId.value = id
  pageIndex.value = 1
  persist()
}

function handleSelectView(id: string) {
  activeViewId.value = id
  applyActiveView()
  pageIndex.value = 1
  persist()
}

function handleRemoveView(id: string) {
  views.value = views.value.filter((v) => v.id !== id)
  if (activeViewId.value === id) activeViewId.value = ''
  applyActiveView()
  persist()
}

/** 视图下拉被清空（或切到别的视图）时同步筛选条件。 */
function onViewChange() {
  applyActiveView()
  pageIndex.value = 1
  persist()
}

/** ── 单元格写入 ───────────────────────────────────────────────── */

function cellKey(recordId: string, field: string): string {
  return `${recordId}:${field}`
}

function isEditing(recordId: string, field: string): boolean {
  return editingKey.value !== '' && editingKey.value === cellKey(recordId, field)
}

/** 点开一格：别的格子若还开着，先收掉（它自己会在 unmount 前把值提交）。 */
function startEdit(recordId: string, field: string) {
  editingKey.value = cellKey(recordId, field)
}

function stopEdit() {
  editingKey.value = ''
}

function cellClass(recordId: string, field: string): string {
  const state = cellState.value[cellKey(recordId, field)]
  return state ? `is-${state}` : ''
}

function setCellState(key: string, state: 'saving' | 'saved' | 'error' | '') {
  const next = { ...cellState.value }
  if (state) next[key] = state
  else delete next[key]
  cellState.value = next
}

/**
 * 改完一格就写飞书（失焦/回车/选完即触发）。
 *
 * 乐观更新：先把新值盖到本地，拼接列与筛选立刻跟着变；写失败再回滚，不留假象。
 * 关掉成功 toast——逐格保存时弹提示会把界面淹掉，改用格子上的状态色反馈。
 */
/**
 * 人员字段当前值的 open_id 列表（`fields` 里存的是姓名，改人得用 id）。
 *
 * 一律返回数组：单选字段也包一层，界面上少一处分支。
 */
function userCellValue(row: BitableRecord, field: string): string[] {
  return (row.user_refs?.[field] ?? []).map((r) => r.id)
}

/** 人员字段当前成员的 id→姓名，给下拉显示通讯录里已没有的人（离职）兜底。 */
function userCellNames(row: BitableRecord, field: string): Record<string, string> {
  return Object.fromEntries((row.user_refs?.[field] ?? []).map((r) => [r.id, r.name]))
}

async function commitCell(row: BitableRecord, field: string, value: unknown, refs?: UserRef[]) {
  const key = cellKey(row.record_id, field)
  const previous = row.fields[field]
  const previousRefs = row.user_refs?.[field]
  if (refs) {
    // 人员：发出去的是 open_id，格子里要显示姓名。两份额外维护的原始标识得一起更新，
    // 否则乐观更新后格子里会显示成一串 `ou_...`（要等下次拉列表才恢复）。
    row.user_refs = { ...row.user_refs, [field]: refs }
    row.fields[field] = refs.length ? refs.map((r) => r.name) : null
  } else {
    row.fields[field] = value
  }
  setCellState(key, 'saving')
  try {
    await FeishuWorkbenchAPI.updateRecord(props.role, row.record_id, { [field]: value }, false, {
      showSuccessMessage: false,
    })
    setCellState(key, 'saved')
    window.setTimeout(() => {
      if (cellState.value[key] === 'saved') setCellState(key, '')
    }, 1500)
  } catch {
    row.fields[field] = previous
    if (refs) row.user_refs = { ...row.user_refs, [field]: previousRefs ?? [] }
    setCellState(key, 'error')
  }
}

/** ── 布局：行高 / 列宽 / 列序 / 填色 ───────────────────────────── */

/**
 * 拖动中的通用收尾：跟着鼠标改，松手才落盘。
 *
 * 拖动期间只改内存不 `persist()`——`mousemove` 一秒能来几十次，每次都写
 * localStorage 会边拖边卡；松手写一次就够。
 */
function drag(onMove: (e: MouseEvent) => void) {
  const move = (e: MouseEvent) => onMove(e)
  const up = () => {
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
    persist()
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}

/** 拖行高：从把手所在的 `<tr>` 量起，这样没拖过的行也能接着调。 */
function onRowResizeStart(e: MouseEvent, row: BitableRecord) {
  const startY = e.clientY
  const tr = (e.currentTarget as HTMLElement | null)?.closest('tr')
  const startH = rowHeights.value[row.record_id] ?? tr?.offsetHeight ?? 40
  drag((ev) => {
    rowHeights.value = {
      ...rowHeights.value,
      [row.record_id]: clampSize(startH + ev.clientY - startY, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX),
    }
  })
}

/** 拖列宽：量的是表头 `<th>` 的实际宽度，和拖行高同思路。 */
function onResizeStart(e: MouseEvent, col: ViewColumn) {
  const startX = e.clientX
  const th = (e.currentTarget as HTMLElement | null)?.closest('th')
  const id = colId(col)
  const startW = columnWidths.value[id] ?? th?.offsetWidth ?? columnWidth(col)
  drag((ev) => {
    columnWidths.value = {
      ...columnWidths.value,
      [id]: clampSize(startW + ev.clientX - startX, COLUMN_WIDTH_MIN, COLUMN_WIDTH_MAX),
    }
  })
}

/** 列序拖拽用原生 HTML5 DnD：表头是 v-for 出来的，接不了 Sortable 的 DOM 绑定。 */
const dragFromIndex = ref(-1)
const dragOverIndex = ref(-1)

function onColDragStart(index: number) {
  dragFromIndex.value = index
}

function onColDragOver(index: number) {
  dragOverIndex.value = index
}

function onColDrop() {
  const from = dragFromIndex.value
  const to = dragOverIndex.value
  onColDragEnd()
  if (from < 0 || to < 0 || from === to) return
  const next = [...columns.value]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  // 落到本地配置上：列序本来就存在 `columns` 里，这里顺带把默认列「固化」下来
  viewColumns.value = next
  persist()
}

function onColDragEnd() {
  dragFromIndex.value = -1
  dragOverIndex.value = -1
}

/** 行高：只有拖过的行才设高，其余交给 ElTable 自己算。 */
/**
 * 有提醒的行，状态格整格闪烁（与妙搭同一套视觉）。
 *
 * 后端 `list` 已经算好 `item.alerts`（未收到账单 / 未确认 / 未开票 / 录入反馈 / 额外费用），
 * 这里只负责把它变成"看得见"：critical 红闪，warning 橙闪，info 不闪。
 */
type WbAlert = { key?: string; signature?: string; severity?: string; title?: string; detail?: string }

function alertsOf(row: BitableRecord): WbAlert[] {
  return (row as unknown as { alerts?: WbAlert[] }).alerts || []
}

const DISMISS_PREFIX = 'weida_alert_dismiss:'
function dismissKeyOf(row: BitableRecord, a: WbAlert): string {
  const id = (row as unknown as { record_id?: string }).record_id || ''
  return `${DISMISS_PREFIX}${id}:${a.key ?? a.title ?? ''}`
}
/** 点掉过的（签名一致才算点掉；数据一变签名变，提醒自己回来）。 */
function isDismissed(row: BitableRecord, a: WbAlert): boolean {
  try {
    return localStorage.getItem(dismissKeyOf(row, a)) === (a.signature ?? '')
  } catch {
    return false
  }
}

const alertDialogVisible = ref(false)
const alertDialogRow = ref<BitableRecord | null>(null)

function onStatusCellClick(row: BitableRecord, column: { label?: string }): void {
  if (column?.label !== '单票状态') return
  if (alertsOf(row).length === 0) return
  alertDialogRow.value = row
  alertDialogVisible.value = true
}

function dismissAlert(row: BitableRecord, a: WbAlert): void {
  try {
    localStorage.setItem(dismissKeyOf(row, a), a.signature ?? '')
  } catch {
    /* 隐私模式写不进：本次会话不闪即可 */
  }
  allRecords.value = [...allRecords.value]
}

function alertCellClass({ row }: { row: BitableRecord }): string {
  const list = alertsOf(row).filter((a) => !isDismissed(row, a))
  if (list.some((a) => a?.severity === 'critical')) return 'cell-alert-critical'
  if (list.some((a) => a?.severity === 'warning')) return 'cell-alert-warning'
  return ''
}

function rowStyle({ row }: { row: BitableRecord }): Record<string, string> {
  const h = rowHeights.value[row.record_id]
  return h ? { height: `${h}px` } : {}
}

/**
 * 单元格底色。优先级：单元格 > 整行 > 整列。
 *
 * 把手列/「查看」列没有 `property`（不是数据列），只吃行色，这样刷的整行看起来
 * 才是连成一条的，不会两边缺一块。
 */
function cellStyle({ row, column }: { row: BitableRecord; column?: { property?: string } }): Record<string, string> {
  const id = column?.property
  const color =
    (id ? cellColors.value[cellColorKey(row.record_id, id)] : undefined) ??
    rowColors.value[row.record_id] ??
    (id ? columnColors.value[id] : undefined)
  return color ? { backgroundColor: color } : {}
}

/**
 * 点一格：填色模式下刷色（同色再点即擦），可编辑格点开编辑器。
 *
 * **点格子不是「打开这一行」**：开详情只有最右边那个「查看」按钮（表上不再有 `@row-click`）。
 * 改掉它的直接原因是一次误伤——拖行高时 `mousemove` 挂在 `document` 上，松手补的那次 click
 * 落点常在把手之外，冒泡到行上就把详情捅开了；根本原因是两件事本来就不该共用一个手势：
 * 这一格点下去是「我要改它」，改详情是另一件事。
 *
 * 只读格（公式/查找/关联/附件）点下去什么都不发生——它改不了，也不该被当成开详情的入口。
 * 事件照旧拦住：拦的是**别的**行级处理器（将来若再加，不会又冒出一个隐藏入口）。
 */
function onCellClick(e: MouseEvent, row: BitableRecord, col: ViewColumn) {
  e.stopPropagation()
  if (paintVisible.value) {
    paintCell(row, col)
    return
  }
  const name = nameOf(col)
  if (isInlineEditable(name)) {
    startEdit(row.record_id, name)
  }
}

function paintCell(row: BitableRecord, col: ViewColumn) {
  const id = colId(col)
  if (paintScope.value === 'cell') toggleColor(cellColors, cellColorKey(row.record_id, id))
  else if (paintScope.value === 'row') toggleColor(rowColors, row.record_id)
  else toggleColor(columnColors, id)
  persist()
}

/** 刷同色 = 擦掉，省得再去点橡皮。 */
function toggleColor(target: typeof cellColors, key: string) {
  const next = { ...target.value }
  if (next[key] === paintColor.value) delete next[key]
  else next[key] = paintColor.value
  target.value = next
}

/** 一键清空本视图所有涂色（行高列宽不动）。 */
function clearPaint() {
  cellColors.value = {}
  rowColors.value = {}
  columnColors.value = {}
  persist()
}

// 进填色模式先把开着的编辑器收掉：两种模式都在抢「点一下格子」这个动作
watch(paintVisible, (on) => {
  if (on) stopEdit()
})

/** ── 列表加载与筛选 ───────────────────────────────────────────── */

const filteredRecords = computed(() => {
  const cfg = config.value
  if (!cfg) return []
  const kw = appliedSearch.value.trim().toLowerCase()

  return allRecords.value.filter((rec) => {
    if (kw) {
      const hit = cfg.search_fields.some((name) => {
        const v = rec.fields[name]
        if (v === null || v === undefined) return false
        return (Array.isArray(v) ? v.join(' ') : String(v)).toLowerCase().includes(kw)
      })
      if (!hit) return false
    }
    return matchesFilter(conditions.value, conjunction.value, (f) => valueOf(rec, f))
  })
})

const pagedRecords = computed(() => {
  const start = (pageIndex.value - 1) * pageSize.value
  return filteredRecords.value.slice(start, start + pageSize.value)
})

function applySearch() {
  appliedSearch.value = searchInput.value
  pageIndex.value = 1
}

function openDetail(row: BitableRecord) {
  currentRecord.value = row
  drawerVisible.value = true
}

async function loadConfig() {
  const res = await FeishuWorkbenchAPI.getConfig(props.role)
  config.value = res.data.data
}

/** ── 索引页「分单号」列：逐条改 iFreight ──────────────────────────── */

/**
 * 订单表上的「分单号」是**查找引用**（type 19，只读）：飞书把挂在这张订单上的每条 iFreight
 * 的分单号 `LISTCOMBINE` 成逗号串，**不带 record_id**——所以那份值拆不回原记录，想改只能去
 * 改 iFreight 自己那一行。这一列因此不走进 `WeidaCellEditor`，点开是个小面板逐条列出来
 * （见模板里的 `ElPopover`）。
 *
 * 面板按需拉取（点开才发请求，一次只问这一张订单），不预取整表：`getLinkedRecords` 是
 * 整表扫描后按回指筛，预取要等一轮，而多数行点都不会被点开。
 */
interface FendanRow {
  /** iFreight 那条记录自己的 id——**写回时用它**，一个订单两条时各改各的 */
  recordId: string
  value: string
}

/** 这一列的名字与它背后那张反查页的 key（与后端 `_WD_OP_RELATED` 同口径）。 */
const FENDAN_FIELD = '分单号'
const FENDAN_BLOCK = 'iFreight'

const fendanRows = ref<FendanRow[]>([])
const fendanLoading = ref(false)
const fendanSaving = ref('')

/** 只有配了 iFreight 反查页的工作台（国内操作）这一列才可改；别的表上它是普通只读列。 */
function isFendanColumn(col: ViewColumn): boolean {
  if (col.kind !== 'field' || col.name !== FENDAN_FIELD) return false
  return (config.value?.related_blocks ?? []).some((b) => b.key === FENDAN_BLOCK)
}

/**
 * 拉回这一张订单挂着的每条 iFreight（含各自的分单号）。
 *
 * 按**本表订单记录 id** 查（反查页的边是反的，见后端 `linked_records`），返回的那几条
 * 天然都属于这张订单，所以不必再从回指列里挑——`link_ids` 里那个 `所属订单` 在这里读到
 * 也没用，是回声。
 */
async function loadFendan(row: BitableRecord): Promise<void> {
  fendanLoading.value = true
  fendanRows.value = []
  try {
    const res = await FeishuWorkbenchAPI.getLinkedRecords(props.role, FENDAN_BLOCK, [row.record_id])
    fendanRows.value = res.data.data.records.map((rec) => ({
      recordId: rec.record_id,
      value: String(rec.fields[FENDAN_FIELD] ?? ''),
    }))
  } catch {
    // 拦截器已提示；面板留空，这一列仍显示订单表那份（可能已过时的）汇总值
    fendanRows.value = []
  } finally {
    fendanLoading.value = false
  }
}

/**
 * 改一条 iFreight 的分单号，**写回 iFreight 表**（不是订单表）。
 *
 * 订单表那一列显示的是公式算出来的汇总，改完本系统不去猜它——本地按当前面板里的值拼一份
 * 乐观串盖上去（飞书那边要等下一次取数才会把公式重算的结果送回来），让用户改完立刻看见，
 * 而不是盯着一个没变的旧值怀疑没保存。
 */
async function commitFendan(row: BitableRecord, fr: FendanRow, value: string): Promise<void> {
  const next = String(value ?? '')
  if (next === fr.value) return
  fendanSaving.value = fr.recordId
  try {
    await FeishuWorkbenchAPI.updateLinkedRecord(props.role, FENDAN_BLOCK, fr.recordId, {
      [FENDAN_FIELD]: next,
    })
    fr.value = next
    row.fields[FENDAN_FIELD] = fendanRows.value
      .map((r) => r.value)
      .filter(Boolean)
      .join(',')
  } catch {
    ElMessage.error('分单号更新失败')
  } finally {
    fendanSaving.value = ''
  }
}

/** 全量拉取：数据量小（当前几十条），一次拉完让搜索/筛选/翻页全部走本地，零额外请求。 */
async function loadRecords() {
  loading.value = true
  truncated.value = false
  try {
    const collected: BitableRecord[] = []
    let token: string | null = null
    for (let i = 0; i < MAX_PAGES; i += 1) {
      const res = await FeishuWorkbenchAPI.getRecords(props.role, {
        page_token: token ?? undefined,
        page_size: PAGE_SIZE,
      })
      const page = res.data.data
      collected.push(...page.items)
      token = page.page_token
      if (!token) break
    }
    truncated.value = !!token
    allRecords.value = collected
    pageIndex.value = 1
  } finally {
    loading.value = false
  }
}

async function reload() {
  loadViewConfig()
  await Promise.all([loadConfig(), loadRecords()])
}

// 角色切换（同一组件复用于多个部门工作台）时重新加载
watch(() => props.role, reload)

onMounted(reload)
</script>

<style scoped>
.weida-workbench {
  padding: 12px;
}

.wb-card :deep(.el-card__body) {
  padding: 12px;
}

.wb-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}

.wb-search {
  width: 240px;
}

.wb-view-select {
  width: 160px;
}

.wb-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.wb-table :deep(.el-table__row) {
  cursor: pointer;
}

/* 提醒闪烁（2026-11-23）：与妙搭同口径 —— 整格描边高频交替，红=critical，橙=warning */
@keyframes wb-alert-flash-red {
  0%, 100% { box-shadow: inset 0 0 0 3px hsl(0 84% 55%); background: hsl(0 84% 96%); }
  50% { box-shadow: inset 0 0 0 3px transparent; background: transparent; }
}
@keyframes wb-alert-flash-amber {
  0%, 100% { box-shadow: inset 0 0 0 3px hsl(38 92% 50%); background: hsl(38 92% 95%); }
  50% { box-shadow: inset 0 0 0 3px transparent; background: transparent; }
}
.wb-table :deep(td.cell-alert-critical) {
  animation: wb-alert-flash-red 1.15s infinite;
}
.wb-table :deep(td.cell-alert-warning) {
  animation: wb-alert-flash-amber 1.15s infinite;
}
@media (prefers-reduced-motion: reduce) {
  .wb-table :deep(td.cell-alert-critical) { animation: none; box-shadow: inset 0 0 0 3px hsl(0 84% 55%); }
  .wb-table :deep(td.cell-alert-warning) { animation: none; box-shadow: inset 0 0 0 3px hsl(38 92% 50%); }
}

.cell-empty {
  color: var(--el-text-color-placeholder);
}

/* 拼接列允许换行（用户可插入换行符），按用户敲的换行原样显示 */
.cell-template {
  white-space: pre-line;
}

.wb-alerts { display: flex; flex-direction: column; gap: 10px; }
.wb-alert-item { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 10px; }
.wb-alert-item.critical { border-color: hsl(0 84% 60%); background: hsl(0 84% 98%); }
.wb-alert-item.warning { border-color: hsl(38 92% 60%); background: hsl(38 92% 98%); }
.wb-alert-head { display: flex; justify-content: space-between; align-items: baseline; }
.wb-alert-title { font-weight: 600; font-size: 13px; }
.wb-alert-badge { font-size: 11px; color: var(--el-text-color-secondary); }
.wb-alert-detail { margin-top: 4px; font-size: 12px; color: var(--el-text-color-regular); }
.wb-alert-done { font-size: 12px; color: var(--el-text-color-placeholder); }

.cell-edit {
  display: flex;
  align-items: center;
  min-height: 24px;
  margin: -4px 0;
  cursor: text;
}

/* 没在编辑时只是一段文本；给个浅浅的底提示「这格能点开改」 */
.cell-edit:not(.is-editing):hover {
  background: var(--el-fill-color-light);
}

.cell-edit.is-editing {
  cursor: default;
}

.cell-display {
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 写入状态反馈：逐格保存关了 toast，靠这三条边框说明「在存 / 存好了 / 没存上」 */
.cell-edit.is-saving {
  opacity: 0.6;
}

.cell-edit.is-saved {
  box-shadow: inset 2px 0 0 var(--el-color-success);
}

.cell-edit.is-error {
  box-shadow: inset 2px 0 0 var(--el-color-danger);
}

.wb-pagination {
  justify-content: flex-end;
  margin-top: 12px;
}

/* ── 填色工具条 ───────────────────────────────────────────────── */

.wb-paint {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.paint-swatch {
  box-sizing: border-box;
  width: 20px;
  height: 20px;
  cursor: pointer;
  border: 1px solid var(--el-border-color);
  border-radius: 3px;
}

.paint-swatch.is-active {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}

.paint-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 填色模式下整格是「可刷」的，光标跟着变，免得以为还能点开编辑 */
.wb-table.is-painting :deep(.cell-wrap) {
  cursor: crosshair;
}

/* ── 表头：拖列序 + 右缘拖列宽 ────────────────────────────────── */

/* 表头单元格的内边距收掉，拖宽把手才能严丝合缝贴在列的分隔线上 */
.wb-table :deep(.el-table__header th.el-table__cell > .cell) {
  height: 100%;
  padding: 0;
  overflow: visible;
}

.col-head {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
  min-height: 23px;
  padding: 0 10px;
  cursor: move;
  user-select: none;
}

.col-head-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-resizer {
  position: absolute;
  top: 0;
  right: -1px;
  width: 8px;
  height: 100%;
  cursor: col-resize;
  border-right: 2px solid transparent;
}

.col-resizer:hover {
  border-right-color: var(--el-color-primary);
}

/* ── 行高把手列 ──────────────────────────────────────────────── */

.wb-table :deep(.col-grip) {
  padding: 0;
}

.wb-table :deep(.col-grip > .cell) {
  height: 100%;
  padding: 0;
}

.grip-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.row-grip {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  min-height: 24px;
  color: var(--el-text-color-placeholder);
  cursor: row-resize;
  user-select: none;
}

.row-grip:hover {
  color: var(--el-color-primary);
}

/* ── 单元格容器 ──────────────────────────────────────────────── */

/* 每个格子包一层，点击区才覆盖整格（含留白），填色时不用精准点到字上 */
.cell-wrap {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 24px;
}

.cell-wrap > * {
  flex: 1 1 auto;
  min-width: 0;
}

/* ── 索引页「分单号」列（逐条改 iFreight）─────────────────────── */

/*
 * 格子平时是只读文本（订单表那份公式汇总），末尾一支铅笔说明它点得开——不加这支笔
 * 就和旁边那些真·只读格长得一模一样，没人会去点。
 */
.cell-fendan {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  width: 100%;
  min-width: 0;
  cursor: pointer;
}

.cell-fendan-icon {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--el-text-color-placeholder);
}

.cell-fendan:hover .cell-fendan-icon {
  color: var(--el-color-primary);
}

/* 面板：一行一条 iFreight，各改各的 */
.fendan-title {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.fendan-empty {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.fendan-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.fendan-row + .fendan-row {
  margin-top: 6px;
}

/*
 * 「第 N 条」不换行：一个订单两条时这行序号是分辨「哪个框是哪条记录」的唯一线索
 * （分单号本身看起来就像同一批），被挤断反而更难认。
 */
.fendan-no {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
</style>
