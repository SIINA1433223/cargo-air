<template>
  <ElDrawer
    :model-value="visible"
    size="82%"
    :title="title"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="emit('update:visible', $event)">
    <!--
      表头右侧挂「单票状态」：标题是商机信息（客户/单号/件数那一长串），状态单看一格也
      说得清，两者并排正好是「哪一单 · 走到哪了」。占的是 header 插槽而不是塞进 title 字符
      串——标题是纯文本，标签要上色，拼进去就只剩颜色不对的方括号了。
    -->
    <template #header>
      <span class="drawer-head">
        <span class="drawer-title">{{ title }}</span>
        <WeidaStatusTag :status="record?.single_status" />
      </span>
    </template>

    <!--
      照飞书的记录详情面板分成几个子详情（Tab）：商机详情 / 货物详情 / 航线报价 / 对客方案。
      字段全铺在一页上时（49 个 + 关联表 28 个）没有一屏看得完的可能，分屏之后每屏都短，
      切过去就是完整信息而不是同一页往下翻。

      Tab 划分由后端下发（`config.detail_groups`），关联表那几个由工作台页面按业务指定
      （`linkBlocks`）——各表要展开的关联不一样，写死在组件里就绑死了一张表。
    -->
    <ElTabs v-model="activeTab" class="detail-tabs" :class="{ 'is-single': tabs.length <= 1 }">
      <!--
        `lazy`：**切到那个 Tab 才挂它的内容**。不挂的话抽屉一开就把所有子详情一起挂上——
        这张表的 69 个字段分成几页，每页几十个控件（下拉、日期、人员）同时初始化，正是
        「详情页打开要等半天」的原因。挂过之后不再卸载（切回去不用重来），值随记录一起来，
        所以切到就能看。
      -->
      <ElTabPane v-for="tab in tabs" :key="tab.key" :label="tab.label" :name="tab.key" lazy>
        <!--
          要点：**不给整页加 loading 遮罩**。字段值随记录一起到，切到就能看；
          只有关联分区要再打一次接口，且**点到那个 Tab 才拉**（见 ensureLinkBlock）。
        -->
        <div v-if="tab.kind === 'fields'" class="field-grid">
          <!--
            「分单号」：**每个子 Tab 都摆一份，都可改**（业务要求）。
            订单表上它是只读的查找引用——飞书把挂在这张订单上的每条 iFreight 的分单号
            `LISTCOMBINE` 成逗号串，那份值**不带 record_id**，拆不回原记录。所以这里不摆
            那格只读文本，而是把这张订单挂着的 iFreight 逐条列出来，各改各的：一张订单挂
            两条时两个框各写各的记录（`commitLinked('iFreight', …)` 认的是每条的 record_id），
            写完直接回 iFreight 表，订单表那列由飞书公式自己汇总。
          -->
          <div v-if="hasFendan" class="field-item is-wide">
            <WeidaFieldLabel name="分单号" description="改的是 iFreight 表里每条记录；订单表这一列由飞书公式自动汇总" />
            <div v-loading="blockOf(FENDAN_BLOCK).loading" class="fendan-fields">
              <span v-if="!fendanRecords.length && !blockOf(FENDAN_BLOCK).loading" class="fendan-hint">
                这张订单还没有 iFreight 数据
              </span>
              <div
                v-for="(linked, i) in fendanRecords"
                :key="linked.record_id"
                class="fendan-field"
                :class="`state-${linkedCellState[linkKey(linked, FENDAN_FIELD)] ?? ''}`">
                <span class="fendan-no">第 {{ i + 1 }} 条</span>
                <RecordFieldControl
                  :field="fendanMeta"
                  :value="linked.fields[FENDAN_FIELD]"
                  :role="role"
                  :disabled="!canUpdate || savingLinked === linkKey(linked, FENDAN_FIELD)"
                  commit-on-change
                  @update:value="(v) => commitLinked(FENDAN_BLOCK, linked, fendanMeta, v)" />
              </div>
            </div>
          </div>
          <div
            v-for="field in tabFields(tab)"
            :key="field.name"
            class="field-item"
            :class="[`state-${cellState[field.name] ?? ''}`, boolTone(field), { 'is-wide': isWide(field) }]">
            <WeidaFieldLabel :name="field.name" :description="field.description" />

            <!-- 飞书按钮（确认订单/批量添加…）：本系统点了没用，摆原位但只能去飞书点 -->
            <div v-if="field.type === FIELD_TYPE.BUTTON" class="button-slot">
              <ElButton size="small" disabled>{{ field.name }}</ElButton>
              <span class="button-hint">需在飞书里点</span>
            </div>

            <RecordFieldControl
              v-else
              :field="field"
              :value="valueOf(field)"
              :textarea="isMultiline(field)"
              :role="role"
              :fallback-names="fallbackNames(field.name)"
              :disabled="!canUpdate || savingField === field.name"
              commit-on-change
              @update:value="(v) => commit(field, v)" />
          </div>
        </div>

        <!--
          关联分区（航线报价 / 对客方案）与**反查页**（iFreight 数据）：都是「另一张表里、
          跟当前这条记录有关的那些行」。版式按三种来：配了列清单的走表格、拆出来的字段页走
          字段网格、其余走折叠卡片——见下面各块的注释。三者共用这一个容器（`link-hint` /
          「共 N 条」/ 收尾字段各自按需出现）。
        -->
        <div v-else v-loading="blockOf(tab.field).loading" class="link-block">
          <!--
            反查页（iFreight 数据）的来源说明：那几行是飞书侧的自动化写进去的（订单上的
            「创建iFreight 数据表」按钮建行、iFreight 上的「同步到iFreight」按钮同步）。
            这里的每一格都**直接写回从表**——飞书那两个按钮只在按下的那一刻跑一次，不会再回头
            覆盖，所以说了「改了要负责」比让人猜要诚实。
          -->
          <div v-if="isRelated(tab)" class="link-hint">这几行由飞书侧的自动化生成；在此处的修改会直接写回这张表</div>

          <!--
            分区**上方**的动作区（对客方案的「发起审批」）。这些字段都挂在**商机主表**上，
            作用的却是这一整块方案列表，飞书里就摆在这儿。两类：

            - **按钮**（`linkActionButtons`，如「发起报价审批」）：飞书的自动化按钮没有开放
              「点一下」的接口，只能回飞书点，所以是灰的。
            - **普通字段**（`linkActionFields`，如「发起审批」）：业务方把原来那个按钮换成了
              复选框，于是这一格能直接勾，走 `commit()` 写回主记录。
          -->
          <div v-if="actionButtons(tab.field).length || actionFieldMetas(tab.field).length" class="link-actions">
            <template v-for="button in actionButtons(tab.field)" :key="button.name">
              <ElButton size="small" disabled>{{ button.name }}</ElButton>
              <span class="button-hint">需在飞书里点</span>
            </template>
            <!-- 改完即写，与字段页同一套描边反馈（`cellState` 按字段名索引，两处不会互相盖） -->
            <div
              v-for="field in actionFieldMetas(tab.field)"
              :key="field.name"
              class="link-action-field"
              :class="`state-${cellState[field.name] ?? ''}`">
              <WeidaFieldLabel :name="field.name" :description="field.description" />
              <RecordFieldControl
                :field="field"
                :value="valueOf(field)"
                :textarea="isMultiline(field)"
                :role="role"
                :fallback-names="fallbackNames(field.name)"
                :disabled="!canUpdate || savingField === field.name"
                commit-on-change
                @update:value="(v) => commit(field, v)" />
            </div>
          </div>

          <!--
            「新增」只建一条**带回指的空行**，字段就地在下面的卡片里填——不弹表单：
            报价的字段多到一屏放不下，弹窗里填跟在这儿填是同一件事，却多一层进出。
          -->
          <div class="link-toolbar">
            <span class="link-count">共 {{ blockOf(tab.field).records.length }} 条</span>
            <ElButton
              v-if="canAddLinked(tab.field)"
              size="small"
              type="primary"
              plain
              :loading="addingLinked === tab.field"
              :disabled="!canUpdate"
              @click="addLinked(tab.field)">
              新增{{ tab.field }}
            </ElButton>
          </div>

          <!--
            表格模式（配了列清单的分区，如航线报价）：一行一条报价、列就是飞书那张表的样子。
            **点格子就地改**（公式/关联那几列改不了，点了不动），**只有最右边那个箭头才展开详情**
            ——展开体里是没列进表格的其余字段（28 − 8 = 20）。
          -->
          <div v-if="isTableBlock(tab.field) && blockOf(tab.field).records.length" class="lt-wrap">
            <div class="lt-grid" :style="tableStyle(tab.field)">
              <div v-for="col in tableColumns(tab.field)" :key="`h:${col.name}`" class="lt-head" :title="col.name">
                {{ col.name }}
              </div>
              <div class="lt-head lt-head-arrow"></div>

              <template v-for="linked in blockOf(tab.field).records" :key="linked.record_id">
                <div
                  v-for="col in tableColumns(tab.field)"
                  :key="`${linked.record_id}:${col.name}`"
                  class="lt-cell"
                  :class="[
                    `state-${linkedCellState[linkKey(linked, col.name)] ?? ''}`,
                    { 'is-editable': isCellEditable(col) },
                  ]"
                  @click="startEdit(linked, col)">
                  <!-- 勾选框直接点就切换，不必先进编辑态（飞书那格里也是这个手感） -->
                  <ElCheckbox
                    v-if="col.type === FIELD_TYPE.CHECKBOX"
                    :model-value="linked.fields[col.name] === true"
                    :disabled="!canUpdate"
                    @click.stop
                    @change="(v) => commitLinked(tab.field, linked, col, v === true)" />
                  <!--
                    飞书自动化按钮列。飞书**没有开放「触发按钮」的接口**，所以默认只能去
                    飞书里点，这里摆成灰按钮（空白格看起来像数据没加载出来）。

                    例外是页面上点名「这个按钮改由系统内对话框完成」的（见
                    `linkButtonDialogs`）：那种渲染成**可点**的按钮，点开自己那一套表单。
                    目前只有对客方案的「添加费用明细」——飞书那边只是弹个批量录入表单，
                    没有非它不可的自动化，所以能原样搬过来。
                  -->
                  <ElButton
                    v-else-if="col.type === FIELD_TYPE.BUTTON && opensDialog(tab.field, col.name)"
                    size="small"
                    type="primary"
                    plain
                    :disabled="!canUpdate"
                    @click.stop="openButtonDialog(tab.field, col.name, linked)">
                    {{ col.name }}
                  </ElButton>
                  <ElButton v-else-if="col.type === FIELD_TYPE.BUTTON" size="small" disabled title="需在飞书里点">
                    {{ col.name }}
                  </ElButton>
                  <WeidaCellEditor
                    v-else-if="isEditing(linked, col)"
                    :meta="col"
                    :value="linked.fields[col.name]"
                    :role="role"
                    :user-value="linkedUserIds(linked, col)"
                    :user-names="linkedFallbackNames(linked, col.name)"
                    @commit="(v, refs) => commitLinked(tab.field, linked, col, v, refs)"
                    @close="stopEdit()" />
                  <span v-else class="lt-text" :title="cellText(linked, col)">{{ cellText(linked, col) }}</span>
                </div>
                <div class="lt-cell lt-cell-arrow" @click="toggleExpand(linked.record_id)">
                  <ElIcon class="lt-arrow" :class="{ 'is-open': isExpanded(linked.record_id) }">
                    <ArrowRight />
                  </ElIcon>
                </div>
                <div v-if="isExpanded(linked.record_id)" class="lt-detail">
                  <div class="field-grid field-grid-linked">
                    <div
                      v-for="field in restFields(tab.field)"
                      :key="field.name"
                      class="field-item"
                      :class="{ 'is-wide': isWide(field) }">
                      <WeidaFieldLabel :name="field.name" :description="field.description" />
                      <RecordFieldControl
                        :field="field"
                        :value="linkedValueOf(linked, field)"
                        :textarea="isMultiline(field)"
                        :role="role"
                        :table-id="blockOf(tab.field).tableId"
                        :fallback-names="linkedFallbackNames(linked, field.name)"
                        :disabled="!canUpdate || savingLinked === linkKey(linked, field.name)"
                        commit-on-change
                        @update:value="(v) => commitLinked(tab.field, linked, field, v)" />
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!--
            字段网格模式（没配列清单的**字段页**：订仓单 / 交接操作这类拆出来的页，以及将来
            没配列清单的反查页）：每条从记录铺一张点名字段的网格，就地和字段页一样改完即写。

            一个商机就一条订单，摆一张只有一行的表多出来的是表头与箭头；iFreight 这种 1:N
            的才配列清单走上面的表格（见 `LINK_COLUMNS`）。
          -->
          <template v-else-if="tab.kind === 'linked' && blockOf(tab.field).records.length">
            <div
              v-for="linked in blockOf(tab.field).records"
              :key="linked.record_id"
              class="field-grid field-grid-linked">
              <div
                v-for="field in splitFields(tab)"
                :key="field.name"
                class="field-item"
                :class="[`state-${linkedCellState[linkKey(linked, field.name)] ?? ''}`, { 'is-wide': isWide(field) }]">
                <WeidaFieldLabel :name="field.name" :description="field.description" />

                <!-- 生成订舱单这类飞书自动化按钮：同上，只能去飞书点 -->
                <div v-if="field.type === FIELD_TYPE.BUTTON" class="button-slot">
                  <ElButton size="small" disabled>{{ field.name }}</ElButton>
                  <span class="button-hint">需在飞书里点</span>
                </div>

                <!--
                  关联字段（订单表的「供应商」）摆成下拉，而不是通用控件的只读文本：这个字段在
                  飞书里本来就能改，只是通用控件把关联一律按只读处理。候选由 `loadLinkPicker`
                  就地为这一页拉一次（订单表不在本工作台，`linkSections` 里没有它的表）。
                -->
                <ElSelect
                  v-else-if="isLinkEditable(field)"
                  :model-value="linkedLinkId(linked, field)"
                  clearable
                  filterable
                  class="full-width"
                  :loading="linkPickerLoading[pickerKey(tab.field, field.name)]"
                  :disabled="!canUpdate || savingLinked === linkKey(linked, field.name)"
                  :placeholder="`请选择${field.name}`"
                  @update:model-value="(v) => commitLinkedLink(tab.field, linked, field, String(v ?? ''))">
                  <ElOption
                    v-for="opt in linkPickerOptions[pickerKey(tab.field, field.name)] ?? []"
                    :key="opt.record_id"
                    :label="opt.text || '(未命名)'"
                    :value="opt.record_id" />
                </ElSelect>

                <RecordFieldControl
                  v-else
                  :field="field"
                  :value="linkedValueOf(linked, field)"
                  :textarea="isMultiline(field)"
                  :role="role"
                  :table-id="blockOf(tab.field).tableId"
                  :fallback-names="linkedFallbackNames(linked, field.name)"
                  :disabled="!canUpdate || savingLinked === linkKey(linked, field.name)"
                  commit-on-change
                  @update:value="(v) => commitLinked(tab.field, linked, field, v)" />
              </div>
            </div>
          </template>

          <!--
            卡片模式（没配列清单的**关联分区**，如对客方案）：每条一张可折叠的卡。
            摘要与展开体的分工见 `linkColumns`。
          -->
          <ElCollapse
            v-else-if="tab.kind === 'link' && blockOf(tab.field).records.length"
            v-model="openedLinks[tab.field]">
            <ElCollapseItem
              v-for="(linked, index) in blockOf(tab.field).records"
              :key="linked.record_id"
              :name="linked.record_id">
              <template #title>
                <span class="linked-title">{{ linkedTitle(tab.field, linked, index) }}</span>
              </template>
              <div class="field-grid field-grid-linked">
                <div
                  v-for="field in restFields(tab.field)"
                  :key="field.name"
                  class="field-item"
                  :class="[
                    `state-${linkedCellState[linkKey(linked, field.name)] ?? ''}`,
                    { 'is-wide': isWide(field) },
                  ]">
                  <WeidaFieldLabel :name="field.name" :description="field.description" />
                  <RecordFieldControl
                    :field="field"
                    :value="linkedValueOf(linked, field)"
                    :textarea="isMultiline(field)"
                    :role="role"
                    :table-id="blockOf(tab.field).tableId"
                    :fallback-names="linkedFallbackNames(linked, field.name)"
                    :disabled="!canUpdate || savingLinked === linkKey(linked, field.name)"
                    commit-on-change
                    @update:value="(v) => commitLinked(tab.field, linked, field, v)" />
                </div>
              </div>
            </ElCollapseItem>
          </ElCollapse>
          <!-- 空态文案：反查页说「还没有」，因为那一页本来就常常是空的（iFreight 还没生成） -->
          <ElEmpty
            v-else-if="!blockOf(tab.field).loading"
            :description="isRelated(tab) ? `还没有${tab.label}` : `没有关联的${tab.field}`"
            :image-size="60" />

          <!--
            关联分区下方的**收尾字段**（对客方案下面那四个：最终对客方案 / 确认订单 /
            是否拿到货 / 未拿到货备注）。来自后端一个同名分组，见 `trailingFields`。
            和上面那张表一样是改完即写；`cellState` 与字段页共用一套描边反馈。
          -->
          <div v-if="trailingFields(tab.field).length" class="link-trailing">
            <div
              v-for="field in trailingFields(tab.field)"
              :key="field.name"
              class="field-item"
              :class="[`state-${cellState[field.name] ?? ''}`, boolTone(field), { 'is-wide': isWide(field) }]">
              <WeidaFieldLabel :name="field.name" :description="field.description" />

              <!-- 飞书自动化按钮（确认订单）：同上，只能去飞书点 -->
              <div v-if="field.type === FIELD_TYPE.BUTTON" class="button-slot">
                <ElButton size="small" disabled>{{ field.name }}</ElButton>
                <span class="button-hint">需在飞书里点</span>
              </div>

              <!--
                关联字段（最终对客方案）写成下拉，而不是通用控件的只读文本：这个字段在飞书里
                本来就能改，只是通用控件把关联一律按只读处理（改关联在索引页有专门的入口）。
                候选就是上面那张表已经拉回来的记录，见 `linkOptions`。
              -->
              <ElSelect
                v-else-if="isLinkPicker(field)"
                :model-value="linkFieldValue(field)"
                clearable
                filterable
                :disabled="!canUpdate || savingField === field.name"
                class="full-width"
                :placeholder="`请选择${field.name}`"
                @update:model-value="(v) => commitLinkField(field, v)">
                <ElOption v-for="opt in linkOptions(field)" :key="opt.id" :label="opt.label" :value="opt.id" />
              </ElSelect>

              <RecordFieldControl
                v-else
                :field="field"
                :value="valueOf(field)"
                :textarea="isMultiline(field)"
                :role="role"
                :fallback-names="fallbackNames(field.name)"
                :disabled="!canUpdate || savingField === field.name"
                commit-on-change
                @update:value="(v) => commit(field, v)" />
            </div>
          </div>
        </div>
      </ElTabPane>
    </ElTabs>

    <template #footer>
      <div class="drawer-footer">
        <!-- 逐格保存没有「保存」按钮，用一句提示说明这一页是怎么写回去的 -->
        <span class="save-hint">{{ canUpdate ? '改动会在离开输入框时自动写回飞书' : '当前账号只能查看' }}</span>
        <ElButton @click="emit('update:visible', false)">关闭</ElButton>
      </div>
    </template>
  </ElDrawer>

  <!--
    按钮字段开出来的系统内表单（对客方案的「添加费用明细」）。挂在抽屉**外面**：
    ElDialog 自带 teleport，摆里面也只是多一层没用的嵌套，还会继承抽屉的滚动容器。
    参数取自打开它的那一刻（见 `openButtonDialog`），关闭时不清理——`visible` 为假就什么都不显示。
  -->
  <WeidaCostDetailDialog
    v-model:visible="buttonDialogVisible"
    :role="role"
    :parent="buttonDialogRow"
    :parent-table-id="buttonDialog.parentTableId"
    :link-field="buttonDialog.linkField"
    :back-ref-field="buttonDialog.backRefField"
    :parent-label="buttonDialogLabel"
    @changed="refreshLinkBlock(buttonDialog.parentLinkField)" />
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ElButton,
  ElCheckbox,
  ElCollapse,
  ElCollapseItem,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElOption,
  ElSelect,
  ElTabPane,
  ElTabs,
  vLoading,
} from 'element-plus'
import FeishuWorkbenchAPI, {
  FIELD_TYPE,
  type BitableRecord,
  type FieldMeta,
  type LinkedRecordsResult,
  type LinkOption,
  type LinkOptionFilter,
  type RelatedBlocksResult,
  type UserOption,
  type UserRef,
  type WorkbenchConfig,
} from '@/api/module_feishu/workbench'
import { cellKind } from '@/utils/feishu-cell'
import { loadUserOptions } from '@/utils/feishu-users'
import { formatFieldValue } from '@/utils/view-config'
import { useAuth } from '@/hooks/core/useAuth'
import RecordFieldControl from '@/views/module_feishu/components/RecordFieldControl.vue'
import WeidaCellEditor from './WeidaCellEditor.vue'
import WeidaCostDetailDialog from './WeidaCostDetailDialog.vue'
import WeidaFieldLabel from './WeidaFieldLabel.vue'
import WeidaStatusTag from './WeidaStatusTag.vue'

defineOptions({ name: 'WeidaRecordDrawer' })

const props = defineProps<{
  visible: boolean
  role: string
  config: WorkbenchConfig | null
  record: BitableRecord | null
  /**
   * 要**单独成一个子详情**的关联字段（如「航线报价」「对客方案」）。
   *
   * 做成参数而不是写死在组件里：富士康那套详情也有自己的关联，各表要展开的东西不一样，
   * 由工作台页面按业务说了算。
   */
  linkBlocks?: readonly string[]
  /**
   * 关联记录渲染成**表格**时要摆的列（关联字段名 → 该表里的字段名，顺序即列序）。
   *
   * 键既可以是本表上的关联字段名（航线报价 / 对客方案），也可以是**反查页的 key**
   * （`RelatedBlock.key`，如 `iFreight`）——两者是同一个「另一张表的若干行」，共用这一套
   * 版式。配了就是表格（折叠时只有摘要文本，展开才挂控件），不配的退回折叠卡片或字段网格
   * ——摆哪几列是**业务决定**，写死在组件里等于把航线报价那张表焊进详情页。
   * 展开体渲染的是其余字段，两边不重复。
   */
  linkColumns?: Record<string, readonly string[]>
  /**
   * 关联记录的**展开体**里不显示的字段（关联字段名 → 该表里的字段名）。
   *
   * 这些字段只是「不摆在这一页」，不是从飞书删掉——但请注意**摆出来的才能改**，
   * 列在这里等于本系统内改不了它（还得回飞书）。
   */
  linkDetailHidden?: Record<string, readonly string[]>
  /**
   * 关联分区右上角「新增」按钮：关联字段名 → 新记录要带上的**回指字段名**。
   *
   * 不配就不显示按钮（对客方案由报价生成，不该在这儿手建）。回指字段名必须来自关系本身，
   * 服务端不猜——猜错就是一条挂在别的商机上的报价。
   */
  linkCreateBackRefs?: Record<string, string>
  /**
   * 关联分区**上方**的动作按钮（关联字段名 → 商机主表上的按钮字段名）。
   *
   * 「发起报价审批」挂在商机主表上，却是对**这一整块方案列表**发起的，飞书里就摆在
   * 列表上方——摆到下面那一排收尾字段里会让人以为是最后一步。只认按钮字段（type 3001），
   * 名字对不上或不是按钮就不渲染。
   */
  linkActionButtons?: Record<string, readonly string[]>
  /**
   * 关联分区**上方**、可就地改的**普通字段**（关联字段名 → 商机主表上的字段名）。
   *
   * 与 `linkActionButtons` 同一个位置、同一个理由：这些字段挂在**商机主表**上，作用的却是
   * 这一整块列表（「发起审批」审的是这条商机下的全部方案），飞书自己也摆在列表上方。
   * 区别是它们**能改**——业务方把原来那个 3001 按钮换成了复选框，于是这一格从「去飞书点」
   * 变成可勾选，走 `commit()` 直接写回主记录。所以这里**只收非按钮字段**：按钮仍然只能去
   * 飞书点，摆成可点的控件是骗人。
   *
   * 点名到这里的字段会**从字段页里摘掉**（见 `tabs`），否则同一个字段在「商机详情」里
   * 还会再出现一次，两个控件绑同一个值——不是数据 bug，但看着像重复渲染。
   */
  linkActionFields?: Record<string, readonly string[]>
  /**
   * 关联分区里**哪些按钮字段改由本系统完成**（关联字段名 → 按钮名 → 子表怎么接）。
   *
   * 飞书的按钮字段（3001）本来只能回飞书点，但其中有些只是「弹个录入表单」，没有非它
   * 不可的自动化——那些就能原样搬进来（对客方案的「添加费用明细」）。搬过来的是**一整个
   * 对话框**：`linkField` 是子记录挂在当前行上的关联字段、`backRefField` 是新子记录回指
   * 当前行的字段。两个名字都来自数据模型本身，服务端不猜。
   *
   * 不配的按钮仍是灰的（去飞书点），所以这里是「升级」而不是「重写」。
   */
  linkButtonDialogs?: Record<string, Record<string, LinkButtonDialog>>
  /**
   * 把**一个关联字段**再拆成几个子详情（关联字段名 → 每个子详情一个名字 + 一份字段清单）。
   *
   * 「订仓单」「交接操作」就是这种情况：两者看的是**同一条订单**（商机上的「订单表」关联
   * 字段），只是一个管头程订舱、一个管交接与国内操作，要摆的字段不一样。不拆的话它们只能
   * 挤成一个叫「订单表」的页。
   *
   * 清单里写的是**关联表**（订单表）里的字段名，顺序即版面顺序。名字对不上就当没写——
   * 字段被改名只会少一格，不会让整页打不开。
   */
  linkFieldSplits?: Record<string, readonly LinkFieldSplit[]>
  /**
   * 二级关联字段下拉里候选的范围约束（**关联表**里的字段名 → 命中哪些值才算候选）。
   *
   * 订单表的「供应商」指向整张客商表，可选值却只有角色含「订舱代理」的那些；不筛的话
   * 客户、报关行、车队全混在下拉里，挑的人得自己认。按字段名索引，与 `linkColumns` 同约定。
   */
  linkOptionFilters?: Record<string, LinkOptionFilter>
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const { hasAuth } = useAuth()
const canUpdate = computed(() => hasAuth('module_feishu:record:update'))

/** 正在写飞书的那个字段名（写的时候禁用它自己，避免连点发出多个请求） */
const savingField = ref('')
/** 字段名 → 保存状态，用来给格子戴上色（与索引页同一套反馈） */
const cellState = ref<Record<string, 'saving' | 'saved' | 'error'>>({})

function setCellState(name: string, state: '' | 'saving' | 'saved' | 'error'): void {
  const next = { ...cellState.value }
  if (state) next[name] = state
  else delete next[name]
  cellState.value = next
}

/**
 * 详情字段清单：优先用后端下发的 `detail_fields`（照飞书表顺序、含按钮）。
 *
 * 兜底用 `fields`（缺按钮）而不是 config 的分组：分组是我们自己重排的，与飞书面板
 * 长得不一样；这里宁可少 4 个按钮占位，也不要版式退回去。
 */
const detailFields = computed(() => props.config?.detail_fields ?? props.config?.fields ?? [])

/**
 * 主字段的字段名：飞书表第一列（「商机信息」）。
 *
 * 没有单独下发 is_primary，而 `detail_fields` 是照表顺序给的，第一项就是主字段。
 */
const primaryName = computed(() => detailFields.value[0]?.name ?? '')

/** 标题取主字段的值（飞书详情面板顶上就是它）——不写死字段名，换表也对 */
const title = computed(() => {
  const value = props.record?.fields[primaryName.value]
  if (value !== null && value !== undefined && value !== '') return String(value)
  return props.config?.name ?? '记录详情'
})

/** ── 子详情（Tab）────────────────────────────────────────── */

/** 字段页：铺一张字段网格（商机详情 / 货物详情） */
interface FieldsTab {
  kind: 'fields'
  key: string
  label: string
  fields: FieldMeta[]
}

/** 关联页：铺若干条关联记录（航线报价 / 对客方案） */
interface LinkTab {
  kind: 'link'
  key: string
  label: string
  field: string
}

type DetailTab = FieldsTab | LinkTab | LinkedFieldsTab

/**
 * 关联页**拆出来的字段页**（订仓单 / 交接操作 / iFreight 数据）：看的还是关联表里那些记录，
 * 但只铺点名的这几个字段，而且不是表格是字段网格——一个商机就一条订单，摆一张只有一行的表
 * 多出来的是表头、列数、展开箭头这些没有信息量的东西。
 *
 * `field` 有两种来源：本表上的关联字段名（「订单表」→ 拿 `link_ids` 里的 id 去取），
 * 或工作台配的**反查页** key（「iFreight」→ 拿记录自己的 id 去取，见 `RelatedBlock`）。
 */
interface LinkedFieldsTab {
  kind: 'linked'
  key: string
  label: string
  /** 取数用的名字：关联字段名，或反查页的 key */
  field: string
  /** 要铺的字段（**关联表**里的字段名，顺序即版面顺序） */
  fields: readonly string[]
  /**
   * 这一页是**反查页**（`RelatedBlock`）：取数拿的是当前记录自己的 id，而写入目标是**从表**
   * （`updateLinkedRecord` 收的 `field` 正是这个 key，后端认得出它是反查页）。
   */
  related?: boolean
}

/**
 * 「这个按钮改由系统内对话框完成」的接法（见 `linkButtonDialogs`）。
 *
 * 两个字段名都是数据模型里现成的：`linkField` 是**子记录挂在当前行上**的那个关联字段
 * （对客方案上的「费用明细」），`backRefField` 是**子记录回指当前行**的那个
 * （费用明细上的「关联的对客方案」）。后者只有建新行时用得上。
 */
interface LinkButtonDialog {
  linkField: string
  backRefField: string
}

/** 关联分区拆出来的一个字段页（见 `linkFieldSplits`）：一个名字 + 一份字段清单 */
interface LinkFieldSplit {
  label: string
  fields: readonly string[]
}

/** 没配拆分时共用的空数组（每次现造一个 `[]` 会让下游的 `v-for` 以为数据变了） */
const NO_SPLITS: readonly LinkFieldSplit[] = []

/** 没有收尾字段时共用的空数组：每次现造一个 `[]` 会让下游的 `v-for` 以为数据变了 */
const NO_TRAILING: readonly FieldMeta[] = []

/** 没打开任何按钮对话框时的占位（模板直接读它的字段，所以不留 null） */
const NO_BUTTON_DIALOG = { linkField: '', backRefField: '', parentTableId: '', parentLinkField: '' }

/**
 * 关联分区**自己的收尾字段**：后端下发的一个分组，其 `label` 与某个关联字段同名时，
 * 那一组不单独成 Tab，而是铺在对应关联分区的**下方**（见 `tabs` 与模板里的 `link-trailing`）。
 *
 * 这是**约定**而不是额外配置：飞书的记录详情面板就是这样排的——「对客方案」的方案列表
 * 下面接着「最终对客方案 / 确认订单 / 是否拿到货 / 未拿到货备注」。让后端把这几条
 * 单列一组、组名取成关联字段名，两边就对上了；没有这一组的分区（航线报价）自然为空。
 */
/**
 * 这个 Tab 是不是**反查页**（iFreight 数据）。
 *
 * 收成一个函数是因为三种版式现在共用一个容器：模板里那个 `tab` 是 `LinkTab | LinkedFieldsTab`
 * 的联合，直接读 `tab.related` 过不了类型检查（`LinkTab` 上没有这个字段）。
 */
function isRelated(tab: DetailTab): boolean {
  return tab.kind === 'linked' && tab.related === true
}

function trailingFields(field: string): readonly FieldMeta[] {
  return (props.config?.detail_groups ?? []).find((group) => group.label === field)?.fields ?? NO_TRAILING
}

/**
 * 关联分区**上方**的动作按钮：页面点名商机主表上的按钮字段（见 `linkActionButtons`）。
 *
 * 只保留真是按钮的（配置写错成普通字段就不渲染——摆上来也不是个按钮）。
 */
function actionButtons(field: string): readonly FieldMeta[] {
  const wanted = props.linkActionButtons?.[field] ?? NO_FIELDS
  if (!wanted.length) return NO_TRAILING
  const byName = new Map(detailFields.value.map((meta) => [meta.name, meta]))
  return wanted
    .map((name) => byName.get(name))
    .filter((meta): meta is FieldMeta => meta !== undefined && meta.type === FIELD_TYPE.BUTTON)
}

/**
 * 关联分区上方那些**可勾选/可填**的字段（见 `linkActionFields`）。
 *
 * 与 `actionButtons` 刚好互补：那边只收按钮、渲染成灰的；这边**滤掉按钮**、渲染成真控件。
 * 一个字段同时出现在两份清单里时以按钮为准（灰的），不会既灰又可点。
 */
function actionFieldMetas(field: string): readonly FieldMeta[] {
  const wanted = props.linkActionFields?.[field] ?? NO_FIELDS
  if (!wanted.length) return NO_TRAILING
  const byName = new Map(detailFields.value.map((meta) => [meta.name, meta]))
  return wanted
    .map((name) => byName.get(name))
    .filter((meta): meta is FieldMeta => meta !== undefined && meta.type !== FIELD_TYPE.BUTTON)
}

/**
 * 按钮对话框的状态。
 *
 * 记的是**打开它的那一刻**的上下文（哪一行、哪张表、哪个链接分区）而不是实时跟着
 * 选中的行走：关闭时不清空，`visible` 为假就什么都不显示，省去一堆判空。
 */
const buttonDialog = ref({ ...NO_BUTTON_DIALOG })
const buttonDialogVisible = ref(false)
const buttonDialogRow = ref<BitableRecord | null>(null)
const buttonDialogLabel = ref('')

/** 这个按钮是不是配了系统内对话框（没配就还是灰的「需在飞书里点」） */
function opensDialog(blockField: string, buttonName: string): boolean {
  return Boolean(props.linkButtonDialogs?.[blockField]?.[buttonName])
}

function openButtonDialog(blockField: string, buttonName: string, row: BitableRecord): void {
  const cfg = props.linkButtonDialogs?.[blockField]?.[buttonName]
  if (!cfg) return
  const block = blockOf(blockField)
  buttonDialog.value = {
    ...cfg,
    // 关联字段长在**这个分区那张表**上（对客方案表），不是工作台表——漏传后端会找不到字段
    parentTableId: block.tableId,
    // 增删明细后要重拉的是**这个分区**（对客方案），不是字段名「费用明细」
    parentLinkField: blockField,
  }
  buttonDialogRow.value = row
  // 标题里带上是哪条方案。取第一列当「名字」，与折叠卡片标题同一套口径（见 linkedTitle）
  const primary = block.fields[0]?.name
  buttonDialogLabel.value = primary ? formatFieldValue(row.fields[primary], row.record_id) : ''
  buttonDialogVisible.value = true
}

/**
 * 重拉某个关联分区（`force` 跳过「已加载」判断）。
 *
 * 对话框里加了明细就牵动了双向关联的另一侧：父记录（对客方案）在服务端多了个关联 id，
 * 手里这份是旧的。重拉一次，之后再点开这一行的按钮才看得到刚加的那几条。
 */
function refreshLinkBlock(blockField: string): void {
  if (!blockField) return
  void ensureLinkBlock(blockField, true)
}

/**
 * 本表上**真有**的那些关联字段（页面点名要做关联分区的，且确实是本表的关联字段）。
 *
 * 这个筛是必要的：`linkBlocks` 是页面按业务写的一份清单（威达那三个工作台共用同一个），
 * 换一张表就会点名到本表没有的字段（订单表上没有「航线报价」这个字段）。不筛的话每个
 * 工作台的详情里都会长出一排**空 Tab**——点进去只有一句「没有关联的航线报价」。
 */
const linkFields = computed<readonly string[]>(() => {
  const wanted = props.linkBlocks ?? []
  if (!wanted.length) return []
  const byName = new Map(detailFields.value.map((meta) => [meta.name, meta]))
  return wanted.filter((name) => Boolean(byName.get(name)?.link_table_id))
})

/**
 * 被提到关联分区**上方**的字段名（`tabs` 要从字段页里把这些摘掉，免得同一个字段出现两次）。
 *
 * 只算**这一页真有的**那些关联分区：`linkActionFields` 是几个威达工作台共用的一份清单，
 * 换一张表时点名的分区可能根本不在（本表没有「对客方案」这个关联字段），那种情况下不该
 * 拿它的字段名去别处做减法，否则会把本表一个普通字段凭空藏起来。
 */
const consumedAbove = computed<ReadonlySet<string>>(() => {
  const names = new Set<string>()
  for (const block of linkFields.value) {
    for (const name of props.linkActionFields?.[block] ?? NO_FIELDS) names.add(name)
  }
  return names
})

/**
 * Tab 清单 = 后端给的字段分组 + 工作台指定的关联分组 + 工作台配的**反查页**。
 *
 * 后端没配 `detail_groups` 时退化成单独一页（`is-single` 会把那排只有一个按钮的
 * Tab 头藏掉——摆着只是个装饰）。
 *
 * 与关联字段同名的那几组要**从 Tab 列表里摘掉**（它们已经铺到关联分区下方了），
 * 否则同名会出现两个 Tab。
 */
const tabs = computed<DetailTab[]>(() => {
  const allGroups = props.config?.detail_groups ?? []
  const groups = allGroups.filter((group) => !linkFields.value.includes(group.label))
  // 判断退化的依据是**后端有没有给分组**，不是过滤后还剩几个：只剩收尾组的极端情况下
  // 也不该把整张表铺成「详情」一页
  // 已经被提到关联分区上方的那几个字段，从字段页里摘掉（同一个字段摆两处、两个控件绑
  // 同一个值，看着像重复渲染）。摘的是**这一页确实提到了**的那些，见 `consumedAbove`。
  const above = consumedAbove.value
  const fieldList = (fields: FieldMeta[]): FieldMeta[] =>
    above.size ? fields.filter((meta) => !above.has(meta.name)) : fields
  const fieldTabs: FieldsTab[] = allGroups.length
    ? groups.map((group) => ({
        kind: 'fields',
        key: `group:${group.label}`,
        label: group.label,
        fields: fieldList(group.fields),
      }))
    : [{ kind: 'fields', key: 'group:all', label: '详情', fields: fieldList(detailFields.value) }]
  // 配了拆分的关联字段不单独成页，而是**拆成几页**（订仓单 / 交接操作都取自「订单表」）；
  // 没配的还是原来那样，一个字段一页。
  // 返回类型写出来：`flatMap` 自己会把两个分支的返回并成一个联合，推出 `U` 是个数组而不是元素
  const linkTabs: DetailTab[] = linkFields.value.flatMap((field): DetailTab[] => {
    const splits = props.linkFieldSplits?.[field] ?? NO_SPLITS
    if (splits.length) {
      return splits.map((split) => ({
        kind: 'linked',
        // 键里带上 field：拆出来的几页取自同一个关联字段，只按名字做键在重名时会撞
        key: `linked:${field}:${split.label}`,
        label: split.label,
        field,
        fields: split.fields,
      }))
    }
    return [{ kind: 'link', key: `link:${field}`, label: field, field }]
  })
  // 反查页（后端 `related_blocks`，如国内操作的「iFreight 数据」）：版式与拆出来的关联字段页
  // 完全一样（每条一张字段网格），只是取数走 `record_id` 反查。字段可编辑性与从表一致
  // （飞书侧只读的类型仍是只读），改了就写回从表。
  const relatedTabs: DetailTab[] = (props.config?.related_blocks ?? []).map(
    (block): DetailTab => ({
      kind: 'linked',
      key: `related:${block.key}`,
      label: block.label,
      field: block.key,
      fields: block.fields.map((meta) => meta.name),
      related: true,
    })
  )
  return [...fieldTabs, ...linkTabs, ...relatedTabs]
})

const activeTab = ref('')

/** 长字段占满整行：挤在 1/N 格里只会被截断，反而看不全 */
const WIDE_FIELDS = new Set([
  '派送地址',
  '货源地',
  '客户核心关注点',
  '路线要求',
  '尺寸（长*宽*高 * 数量）',
  '备注',
  '记录备注',
  '未拿到货备注',
  '风险提醒',
  '航线方案建议',
  // 订单表上的几个长文本（订仓单 / 交接操作两页）：一格 240px 装不下整段，
  // 挤在格子里只能来回滚动，独占一行才读得完
  '操作要求',
  '特别注意',
  '代理操作要求',
  '提货信息',
  // 「货物数据」一行一条：小计/件数/重量一行，底下每行一个尺寸。
  // 一格里横着排会折成一团，得独占一行才看得出有几条
  '货物数据',
])

/**
 * 值里**自带换行**的文本字段：用多行输入框，让换行真的换行。
 *
 * 单行 `<input>` 不认换行——`"1/419/1.39\n171*118*69*1"` 会显示成
 * `1/419/1.39171*118*69*1`，不光标错了行，值连起来读着就是错的，改的时候更没法下手。
 *
 * 按字段名点名而不是「只要是文本就给多行」：表里绝大多数文本字段就是一行短字，
 * 全换成两行起步的框会把网格撑散。飞书那边改名了这里会退回单行，值不会丢。
 */
const MULTILINE_FIELDS = new Set(['货物数据', '操作要求', '特别注意', '代理操作要求', '提货信息'])

function isMultiline(field: FieldMeta): boolean {
  return field.type === FIELD_TYPE.TEXT && MULTILINE_FIELDS.has(field.name)
}

/**
 * 反向例外：按类型该占整行、实际值却很短的那几个。
 *
 * 「中转点」是多选，默认会独占一行；可它装的是 HKG / PVG 这类三字码，挤在一格里正好——
 * 独占一行反而把它从「目的港」旁边挤走，看着像两个不相干的字段。
 */
const NARROW_FIELDS = new Set(['中转点'])

function isWide(field: FieldMeta): boolean {
  if (NARROW_FIELDS.has(field.name)) return false
  return WIDE_FIELDS.has(field.name) || field.type === FIELD_TYPE.MULTI_SELECT
}

/**
 * 「是/否」型字段：是=绿、否=红。
 *
 * 这两个字本身不显眼，扫一列很难看出哪几条拿到了货；染色之后整栏的颜色就是答案。
 * 按**字段名**点名而不是按类型判断——表里还有一堆一样是单选、取值却不是是/否的字段
 * （业务类型、方案状态…），按类型来会把它们一起染上。
 */
const YES_NO_FIELDS = new Set(['是否拿到货'])

function boolTone(field: FieldMeta): string {
  if (!YES_NO_FIELDS.has(field.name)) return ''
  const value = props.record?.fields[field.name]
  if (value === '是') return 'tone-yes'
  if (value === '否') return 'tone-no'
  return ''
}

/**
 * 格子该显示的值：人员走 open_id（`fields` 里只有姓名，改不了人），其余照 `fields`。
 *
 * 关联字段不用在这里做转换：对方的名字由后端补好了（飞书只回 record_id 不回名字，
 * 得拿 id 去目标表查，那件事在 `_fill_link_texts` 里统一做了）。
 */
function valueOf(field: FieldMeta): unknown {
  const rec = props.record
  if (!rec) return null
  if (field.type === FIELD_TYPE.USER) {
    const ids = (rec.user_refs?.[field.name] ?? []).map((r) => r.id)
    return field.multiple ? ids : (ids[0] ?? '')
  }
  return rec.fields[field.name] ?? null
}

/** 人员字段已有成员的 id→姓名，让离职等不在通讯录里的人也能显示成姓名而不是 `ou_...` */
function fallbackNames(name: string): Record<string, string> {
  return Object.fromEntries((props.record?.user_refs?.[name] ?? []).map((r) => [r.id, r.name]))
}

/**
 * 通讯录，用来把刚选中的 open_id 换成姓名（`fields` 里要存姓名才能显示）。
 *
 * 走 `loadUserOptions` 的进程内缓存：抽屉里每个人选下拉、索引页的格子编辑器、新建表单
 * 用的都是同一份，这里不会多发一次请求。
 */
const directory = ref<UserOption[]>([])

/**
 * 选中的人 → `{id, name}`；通讯录里查不到（离职）就退回记录自带的姓名，再退回 id。
 *
 * `fallback` 由调用方给：商机上的字段查商机的 `user_refs`，报价卡片上的字段得查**那条报价**
 * 自己的——同名人员字段在两张表里各有各的成员。
 */
function toUserRefs(value: unknown, fallback: Record<string, string>): UserRef[] {
  const ids = (Array.isArray(value) ? value : value ? [value] : []).map(String)
  return ids.map((id) => ({
    id,
    name: directory.value.find((u) => u.open_id === id)?.name ?? fallback[id] ?? id,
  }))
}

/** ── 改完即存 ─────────────────────────────────────────────── */

/**
 * 改一个字段就写一次飞书，没有「保存」按钮。
 *
 * 乐观更新 + 失败回滚：`record` 与列表里那一行是**同一个对象**，改完索引页立刻同步，
 * 不用重拉列表；写失败必须把值放回去，否则界面上显示着一个飞书里并不存在的值。
 * 成功不弹 toast（逐格保存会淹掉界面），用格子上的描边色反馈。
 */
async function commit(field: FieldMeta, value: unknown): Promise<void> {
  const rec = props.record
  if (!rec || !canUpdate.value) return

  const previous = rec.fields[field.name]
  const previousRefs = rec.user_refs?.[field.name]
  if (field.type === FIELD_TYPE.USER) {
    // 人员提交的是 open_id，而 `fields` 里要显示姓名——两份标识得一起更新，
    // 漏一个就会在乐观更新后看到一串 `ou_...`（要等下次拉列表才恢复）
    const refs = toUserRefs(value, fallbackNames(field.name))
    rec.user_refs = { ...rec.user_refs, [field.name]: refs }
    rec.fields[field.name] = refs.length ? refs.map((r) => r.name) : null
  } else {
    rec.fields[field.name] = value
  }

  savingField.value = field.name
  setCellState(field.name, 'saving')
  try {
    await FeishuWorkbenchAPI.updateRecord(props.role, rec.record_id, { [field.name]: value }, false, {
      showSuccessMessage: false,
    })
    setCellState(field.name, 'saved')
    window.setTimeout(() => {
      if (cellState.value[field.name] === 'saved') setCellState(field.name, '')
    }, 1500)
  } catch {
    rec.fields[field.name] = previous
    if (field.type === FIELD_TYPE.USER) rec.user_refs = { ...rec.user_refs, [field.name]: previousRefs ?? [] }
    setCellState(field.name, 'error')
  } finally {
    savingField.value = ''
  }
}

/** ── 关联子详情（航线报价 / 对客方案）──────────────────────── */

interface LinkBlock {
  field: string
  /**
   * 这一块是**反查页**（见 `LinkedFieldsTab.related` / `RelatedBlock`）。
   *
   * 取数时要拿当前记录自己的 id 去问从表（本表上没有指向从表的关联字段），与其它关联
   * 分区按 `link_ids` 取数相反。
   */
  related?: boolean
  /** 请求在途 */
  loading: boolean
  /** 拉过一次没有（**拉过就认**，哪怕结果是空：翻回来不该再打一次接口） */
  loaded: boolean
  /**
   * 关联表的 table_id。
   *
   * 只有「下载这张表里的附件」用得着：飞书按 tableId 判附件权限，而这张表不属于本工作台，
   * 后端拿主表去取会 403。前端没有别的途径知道它，只能由 `getLinkedRecords` 带回来。
   */
  tableId: string
  fields: FieldMeta[]
  records: BitableRecord[]
}

const linkSections = ref<LinkBlock[]>([])

/**
 * 各家关联记录里**已展开**的卡片（字段名 → record_id 数组）。
 *
 * 默认全展开：折起来的话切过去只看到一排标题，「一屏看全」就无从谈起。折叠只留给
 * 一票挂了五六条报价、需要把别的挪出视野的场合。
 */
const openedLinks = ref<Record<string, string[]>>({})

/**
 * 反查页（`RelatedBlock`）**一次全取**：一个请求把该工作台所有反查页填好。
 *
 * 为什么不逐页取：这几页常常指向**同一张从表**（国内操作那三页都是 iFreight、回指字段
 * 也同一个，只差铺哪几列），而后端每页要整表扫一遍——实测三个 Tab 合计 4.4 秒，其中两次
 * 纯属重复。合并后同一张表只扫一次（一秒半），三个 Tab 一起填好，之后切页是纯本地切换。
 *
 * 打开抽屉时就发（而不是等到点那个 Tab）：分单号那一块长在**订单的几个子 Tab 顶部**，
 * 用的正是 iFreight 的记录，不提前拿的话操作页上那是空的。反正三个 Tab 迟早都要看，
 * 与其点一次等一次，不如一次等完。
 *
 * 失败时把这几页标成已加载但空着——一个从表拉不到不该让整个详情页打不开（与
 * `ensureLinkBlock` 同一个取舍）。
 */
async function ensureRelatedBlocks(force = false): Promise<void> {
  const rec = props.record
  if (!rec) return
  const keys = (props.config?.related_blocks ?? []).map((b) => b.key)
  const targets = keys
    .map((key) => linkSections.value.find((s) => s.field === key))
    .filter((s): s is LinkBlock => s !== undefined && !s.loading && (force || !s.loaded))
  if (!targets.length) return

  targets.forEach((s) => patchBlock(s.field, { loading: true }))
  let result: RelatedBlocksResult = {}
  try {
    const res = await FeishuWorkbenchAPI.getRelatedBlocks(props.role, [rec.record_id])
    result = res.data.data ?? {}
  } catch {
    // 具体报错由拦截器弹出；这里让这几页空着，别的子详情照常能看能改
    result = {}
  }
  for (const s of targets) {
    const page = result[s.field]
    patchBlock(s.field, {
      loading: false,
      loaded: true,
      tableId: page?.table_id ?? '',
      fields: page?.fields ?? [],
      records: page?.records ?? [],
    })
    // 拉到就全摊开（见 openedLinks）
    openedLinks.value = { ...openedLinks.value, [s.field]: (page?.records ?? []).map((r) => r.record_id) }
  }
}

/**
 * 关联记录**点到那个 Tab 才拉**（反查页除外，见 `ensureRelatedBlocks`）。
 *
 * 一次打开抽屉就把几个关联表全拉回来，等于让用户为没看的页面白等（每次一两个来回）。
 * 翻回来时不再重拉：这类内容是「看一眼」的，值变了重开抽屉即可。
 *
 * 拉不到就留空并显示「没有关联的X」——一个关联表出问题不该让整个详情页打不开。
 *
 * `force` 用于「刚建了一条」：那次重拉要**绕过** `loaded`，否则新行永远不出现。
 */
async function ensureLinkBlock(field: string, force = false): Promise<void> {
  const block = linkSections.value.find((b) => b.field === field)
  if (!block) return
  // 反查页**不单取**：同一个工作台的反查页常常指向同一张从表（国内操作那三页都是
  // iFreight），逐页取数会把这张表各扫一遍。走 `ensureRelatedBlocks` 一次全取回来，
  // 谁先被点到都顺带把其余几页填好——切 Tab 因此不再等一秒多。
  if (block.related) return await ensureRelatedBlocks(force)
  if (block.loading || (block.loaded && !force)) return

  const rec = props.record
  // 反查页的「钥匙」是**记录自己**的 id，这里剩下的都是本表上的关联字段：
  // 拿本表这一列的关联 id 去取。
  const ids = rec?.link_ids?.[field] ?? []
  if (!rec || !ids.length) {
    // 强制重拉时没有 id 说明刚建的那条还没回写进内存，此时**保留**已有的记录，
    // 别把眼前的卡片清空
    if (!force) patchBlock(field, { loaded: true, tableId: '', fields: [], records: [] })
    return
  }

  patchBlock(field, { loading: true })
  const EMPTY_RESULT: LinkedRecordsResult = { table_id: '', fields: [], records: [] }
  let result: LinkedRecordsResult = EMPTY_RESULT
  try {
    const res = await FeishuWorkbenchAPI.getLinkedRecords(props.role, field, ids)
    result = res.data.data
  } catch {
    // 具体报错由拦截器弹出；这里让分区空着，别的子详情照常能看能改
    result = EMPTY_RESULT
  }
  patchBlock(field, {
    loading: false,
    loaded: true,
    tableId: result.table_id,
    fields: result.fields,
    records: result.records,
  })
  // 拉到就全摊开（见 openedLinks）
  openedLinks.value = { ...openedLinks.value, [field]: result.records.map((r) => r.record_id) }
}

function patchBlock(field: string, patch: Partial<LinkBlock>): void {
  linkSections.value = linkSections.value.map((b) => (b.field === field ? { ...b, ...patch } : b))
}

/** 模板里要按字段名取分区；取不到时给一份空的，省得每处都写 `?.` */
const EMPTY_BLOCK: LinkBlock = { field: '', loading: false, loaded: false, tableId: '', fields: [], records: [] }

function blockOf(field: string): LinkBlock {
  return linkSections.value.find((b) => b.field === field) ?? EMPTY_BLOCK
}

/** ── 「分单号」：每个订单子 Tab 都摆一份，写回 iFreight 每条记录 ───────────── */

/**
 * 分单号可写的形态在 **iFreight 表**里（每条记录一个文本值）；订单表上那一列是只读的
 * 查找引用（飞书把这几条拼成逗号串），所以这一格不能走进普通字段渲染——它要按 iFreight
 * 的记录数摆出**几个**输入框，而不是一个。
 */
const FENDAN_FIELD = '分单号'
const FENDAN_BLOCK = 'iFreight'

/** 这个工作台配了 iFreight 反查页才摆分单号（别的详情页没这回事）。 */
const hasFendan = computed(() => (props.config?.related_blocks ?? []).some((b) => b.key === FENDAN_BLOCK))

/** 这张订单挂着的 iFreight：每一条各是一个可改的分单号。 */
const fendanRecords = computed(() => blockOf(FENDAN_BLOCK).records)

/**
 * 分单号在 **iFreight 表**上的字段定义（控件按它渲染）。
 *
 * 取不到时给一份文本字段兜底：从表那边改了名也不该让这一格消失——`commitLinked` 发的
 * 还是 `分单号` 这个名字，飞书认不出会报错，但比整块凭空不见容易发现。
 */
const fendanMeta = computed<FieldMeta>(
  () =>
    blockOf(FENDAN_BLOCK).fields.find((m) => m.name === FENDAN_FIELD) ?? {
      name: FENDAN_FIELD,
      type: FIELD_TYPE.TEXT,
      ui_type: '',
      read_only: false,
      editable: true,
      options: [],
      multiple: false,
      description: '',
    }
)

/** 订单子 Tab 的字段清单：**摘掉「分单号」**——上面那个组件已经按 iFreight 逐条摆了它。 */
function tabFields(tab: FieldsTab): FieldMeta[] {
  if (!hasFendan.value) return tab.fields
  return tab.fields.filter((f) => f.name !== FENDAN_FIELD)
}

/** 关联卡片的标题：优先主字段（关联表第一列），没有就退回「第 N 条」 */
function linkedTitle(field: string, record: BitableRecord, index: number): string {
  const primary = blockOf(field).fields[0]?.name
  const value = primary ? record.fields[primary] : null
  if (value !== null && value !== undefined && value !== '') return String(value)
  return `${field} ${index + 1}`
}

/** ── 关联记录：表格 / 展开其余 / 就地改 ──────────────────── */

const NO_FIELDS: readonly string[] = []

/**
 * 表格要摆的列（按配置写的顺序，与表里的列序无关）。
 *
 * 只保留目标表里真有的字段——配置里写了表里没有的名字就当没写，不该让整个分区打不开。
 */
function tableColumns(field: string): FieldMeta[] {
  const wanted = props.linkColumns?.[field] ?? NO_FIELDS
  if (!wanted.length) return []
  const byName = new Map(blockOf(field).fields.map((m) => [m.name, m]))
  return wanted.map((name) => byName.get(name)).filter((m): m is FieldMeta => m !== undefined)
}

/** 这个分区走表格还是折叠卡片（后者只有没配列清单的分区） */
function isTableBlock(field: string): boolean {
  return tableColumns(field).length > 0
}

/**
 * 表格的列模板：内容列等宽，末尾一个定宽箭头列。
 *
 * 下限写 **0** 而不是 140px：列多的时候（对客方案 10 列）140px 的下限会把整张表顶出
 * 容器，最右边那个展开箭头要横向滚动才看得见——而「一屏看全」正是这张表存在的理由。
 * 下限归零后由 `1fr` 自己分配，格子按可用宽度收缩，内容长了走省略号（`title` 看全文）。
 */
function tableStyle(field: string): Record<string, string> {
  return { gridTemplateColumns: `repeat(${tableColumns(field).length}, minmax(0, 1fr)) 40px` }
}

/**
 * 展开体里要渲染的字段 = 关联表的全部字段**减去已经成列的**（那些在表格里看过了），
 * 再减去页面点名不要的（`linkDetailHidden`）。
 *
 * 顺序一律照飞书表里的顺序——不重排：业务在飞书里挪了字段，这里自动跟上。
 * 按钮字段也去掉：它没有值，展示体里只能摆个点不动的占位。
 */
function restFields(field: string): FieldMeta[] {
  const shown = new Set(tableColumns(field).map((m) => m.name))
  const hidden = new Set(props.linkDetailHidden?.[field] ?? NO_FIELDS)
  return blockOf(field).fields.filter((m) => !shown.has(m.name) && !hidden.has(m.name) && m.type !== FIELD_TYPE.BUTTON)
}

/**
 * 拆出来的字段页要铺的字段 = 清单里那几个名字对应的元数据。
 *
 * 名字在关联表里找不到就跳过（字段被改名、配置写错时只会少一格，不会让整页打不开）。
 * 顺序**照清单来**，不是飞书表里的顺序——这两页的字段是业务按「订舱要看什么 / 交接要看什么」
 * 排的，照表顺序排就退回成一坨了。
 */
function splitFields(tab: LinkedFieldsTab): FieldMeta[] {
  const byName = new Map(blockOf(tab.field).fields.map((m) => [m.name, m]))
  return tab.fields.map((name) => byName.get(name)).filter((m): m is FieldMeta => m !== undefined)
}

/** `2026-09-12 12:33:00` —— 服务端对日期统一给到秒的 ISO 串（含查找回来的日期） */
const DATE_TIME_RE = /^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}$/

/**
 * 格子里显示的文本；空值给空串（表格里一排 `—` 比留白更碍眼），`title` 悬停看全文。
 *
 * 日期只显示到**日**：表格列宽本来就紧张（10 列），而 `2026-09-12 12:33:00` 比
 * `2026-09-12` 多占一倍宽度换来的时分秒，在这些字段上（报价有效期至、最早航班时间）
 * 基本没人看。完整值仍在展开体里，悬停 `title` 也是完整的。
 */
function cellText(record: BitableRecord, meta: FieldMeta): string {
  const text = formatFieldValue(record.fields[meta.name], '')
  return DATE_TIME_RE.exec(text)?.[1] ?? text
}

/**
 * 这一格点下去能不能变成编辑器。
 *
 * 只读列与附件/关联列本来就没有控件（`plain`）；**勾选框列也排除**——那格常驻一个勾，
 * 再让点空白处冒出一个开关来就有两套改法了。
 */
function isCellEditable(meta: FieldMeta): boolean {
  const kind = cellKind(meta)
  return canUpdate.value && kind !== 'plain' && kind !== 'checkbox'
}

/** ── 就地编辑（同一时刻只挂一个编辑器）──────────────────────── */

/** 正在编辑的那一格；`null` 表示没有 */
const editingCell = ref<{ recordId: string; field: string } | null>(null)

function isEditing(record: BitableRecord, meta: FieldMeta): boolean {
  return editingCell.value?.recordId === record.record_id && editingCell.value.field === meta.name
}

function startEdit(record: BitableRecord, meta: FieldMeta): void {
  if (!isCellEditable(meta)) return
  editingCell.value = { recordId: record.record_id, field: meta.name }
}

function stopEdit(): void {
  editingCell.value = null
}

/** ── 展开详情（只有点最右边那个箭头才展开）────────────────── */

const expandedIds = ref<string[]>([])

function isExpanded(recordId: string): boolean {
  return expandedIds.value.includes(recordId)
}

function toggleExpand(recordId: string): void {
  expandedIds.value = isExpanded(recordId)
    ? expandedIds.value.filter((id) => id !== recordId)
    : [...expandedIds.value, recordId]
}

/** 关联记录上某格要显示的值：人员走 open_id（`fields` 里只有姓名，改不了人），其余照 `fields` */
function linkedValueOf(record: BitableRecord, field: FieldMeta): unknown {
  if (field.type === FIELD_TYPE.USER) {
    const ids = (record.user_refs?.[field.name] ?? []).map((r) => r.id)
    return field.multiple ? ids : (ids[0] ?? '')
  }
  return record.fields[field.name] ?? null
}

/** 那条关联记录里人员字段已有成员的 id→姓名（离职等不在通讯录里的人也要显示成姓名） */
function linkedFallbackNames(record: BitableRecord, name: string): Record<string, string> {
  return Object.fromEntries((record.user_refs?.[name] ?? []).map((r) => [r.id, r.name]))
}

/** 那条关联记录里人员字段当前值的 open_id 数组（表格里的编辑器只认 id，`fields` 里是姓名） */
function linkedUserIds(record: BitableRecord, meta: FieldMeta): string[] {
  return (record.user_refs?.[meta.name] ?? []).map((r) => r.id)
}

/**
 * 关联格子保存态的键：**记录 + 字段**。
 *
 * 同一字段名（成本金额）在多条报价上各有一格，只按字段名记的话，改第一条会把三条一起点亮。
 */
function linkKey(record: BitableRecord, name: string): string {
  return `${record.record_id}|${name}`
}

/** 关联卡片逐格保存的状态（与字段页的 `cellState` 同一套描边色反馈） */
const linkedCellState = ref<Record<string, 'saving' | 'saved' | 'error'>>({})
const savingLinked = ref('')

function setLinkedCellState(key: string, state: '' | 'saving' | 'saved' | 'error'): void {
  const next = { ...linkedCellState.value }
  if (state) next[key] = state
  else delete next[key]
  linkedCellState.value = next
}

/**
 * 改关联记录里的一格，改完即写。
 *
 * 与 `commit` 同一套路（乐观更新 + 失败回滚 + 格子描边、不弹 toast），差别只在写的是
 * **关联表**：`updateRecord` 写本工作台的表，拿商机表的字段类型去写报价只会一个字段都不认。
 */
async function commitLinked(
  field: string,
  record: BitableRecord,
  meta: FieldMeta,
  value: unknown,
  refs?: UserRef[]
): Promise<void> {
  if (!canUpdate.value) return

  const key = linkKey(record, meta.name)
  const previous = record.fields[meta.name]
  const previousRefs = record.user_refs?.[meta.name]
  if (meta.type === FIELD_TYPE.USER) {
    // 人员提交的是 open_id，而 `fields` 里要显示姓名——两份标识得一起更新，漏一个就会在
    // 乐观更新后看到一串 `ou_...`。表格里的编辑器自己算好了 refs，卡片那边从值反推。
    const nextRefs = refs ?? toUserRefs(value, linkedFallbackNames(record, meta.name))
    record.user_refs = { ...record.user_refs, [meta.name]: nextRefs }
    record.fields[meta.name] = nextRefs.length ? nextRefs.map((r) => r.name) : null
  } else {
    record.fields[meta.name] = value
  }

  savingLinked.value = key
  setLinkedCellState(key, 'saving')
  try {
    await FeishuWorkbenchAPI.updateLinkedRecord(props.role, field, record.record_id, { [meta.name]: value })
    setLinkedCellState(key, 'saved')
    window.setTimeout(() => {
      if (linkedCellState.value[key] === 'saved') setLinkedCellState(key, '')
    }, 1500)
  } catch {
    record.fields[meta.name] = previous
    if (meta.type === FIELD_TYPE.USER) record.user_refs = { ...record.user_refs, [meta.name]: previousRefs ?? [] }
    setLinkedCellState(key, 'error')
  } finally {
    savingLinked.value = ''
  }
}

/** ── 关联字段的下拉选择（最终对客方案）────────────────────── */

/**
 * 改**关联表**里那条记录上的关联字段（订单表的「供应商」），改完即写。
 *
 * 与 `commitLinked` 的差别同 `commitLinkField` 与 `commit` 的差别：关联字段有**两份**标识
 * ——`fields` 存显示文本、`link_ids` 存 record_id。只改前者会让界面在乐观更新后显示成一串
 * `rec...`（要等下次拉列表才恢复）；提交回后端的也必须是 id 数组，不是名字。
 * 失败两份一起回滚。
 */
async function commitLinkedLink(
  blockField: string,
  record: BitableRecord,
  meta: FieldMeta,
  recordId: string
): Promise<void> {
  if (!canUpdate.value) return

  const key = linkKey(record, meta.name)
  const previousIds = record.link_ids?.[meta.name]
  const previousText = record.fields[meta.name]
  const payload = recordId ? [recordId] : []
  const options = linkPickerOptions.value[pickerKey(blockField, meta.name)] ?? []

  record.link_ids = { ...record.link_ids, [meta.name]: payload }
  // `fields` 里的关联照服务端那份的形状来（数组），否则这一格换成只读文本时形状对不上
  record.fields[meta.name] = recordId ? [options.find((o) => o.record_id === recordId)?.text || recordId] : null

  savingLinked.value = key
  setLinkedCellState(key, 'saving')
  try {
    await FeishuWorkbenchAPI.updateLinkedRecord(props.role, blockField, record.record_id, { [meta.name]: payload })
    setLinkedCellState(key, 'saved')
    window.setTimeout(() => {
      if (linkedCellState.value[key] === 'saved') setLinkedCellState(key, '')
    }, 1500)
  } catch {
    record.link_ids = { ...record.link_ids, [meta.name]: previousIds ?? [] }
    record.fields[meta.name] = previousText
    setLinkedCellState(key, 'error')
  } finally {
    savingLinked.value = ''
  }
}

/**
 * 商机上的某个关联字段指向的那张表，是否正是**某个已加载的关联分区**。
 *
 * 不按字段名认（写死「最终对客方案 ← 对客方案」换张表就失效），而是按目标表 id 对：
 * 两者指向同一张表就意味着「候选就是那张表里已经拉回来的记录」，不必再打一次接口。
 * 分区还没拉过（`tableId` 为空）或指向别的表时返回 undefined，控件退回只读显示。
 */
function linkTargetBlock(field: FieldMeta): LinkBlock | undefined {
  const tableId = field.link_table_id
  if (!tableId) return undefined
  return linkSections.value.find((b) => b.tableId === tableId)
}

/** 这个字段该不该写成下拉（单向关联 + 它的目标表已经拉回来了） */
function isLinkPicker(field: FieldMeta): boolean {
  return field.type === FIELD_TYPE.SINGLE_LINK && linkTargetBlock(field) !== undefined
}

/** 关联字段当前值的 record_id（空串 = 没选） */
function linkFieldValue(field: FieldMeta): string {
  return props.record?.link_ids?.[field.name]?.[0] ?? ''
}

/**
 * 下拉候选：目标表那几条记录，显示成它们的主字段（对客方案就是「方案编号」）。
 *
 * 当前值不在候选里时要**补一条**——目标表还没拉到、或这条方案已经不从本商机挂过去了，
 * 都会出现这种情况；补不上就会在下拉里显示成一串 `rec...`，看起来像坏了。
 */
function linkOptions(field: FieldMeta): { id: string; label: string }[] {
  const block = linkTargetBlock(field)
  if (!block) return []
  const primary = block.fields[0]?.name
  const options = block.records.map((record) => ({
    id: record.record_id,
    label: primary ? formatFieldValue(record.fields[primary], record.record_id) : record.record_id,
  }))
  const current = linkFieldValue(field)
  if (current && !options.some((option) => option.id === current)) {
    options.unshift({ id: current, label: formatFieldValue(props.record?.fields[field.name], current) })
  }
  return options
}

/**
 * 改商机上的关联字段，改完即写。
 *
 * 与 `commit` 的关键差别：关联字段有**两份**标识——`fields` 存显示文本、`link_ids` 存
 * record_id。`commit` 只更新前者，拿它来写关联会让界面在乐观更新后显示成一串 record_id；
 * 这里两份一起改，失败两份一起回滚。提交给后端的仍是 record_id 数组
 * （`denormalize_field_value` 认这个格式），传空数组 = 解除关联。
 */
async function commitLinkField(field: FieldMeta, value: unknown): Promise<void> {
  const rec = props.record
  if (!rec || !canUpdate.value) return

  const recordId = value ? String(value) : ''
  const previousIds = rec.link_ids?.[field.name]
  const previousText = rec.fields[field.name]
  const payload = recordId ? [recordId] : []

  rec.link_ids = { ...rec.link_ids, [field.name]: payload }
  rec.fields[field.name] = recordId
    ? (linkOptions(field).find((option) => option.id === recordId)?.label ?? recordId)
    : null

  savingField.value = field.name
  setCellState(field.name, 'saving')
  try {
    await FeishuWorkbenchAPI.updateRecord(props.role, rec.record_id, { [field.name]: payload }, false, {
      showSuccessMessage: false,
    })
    setCellState(field.name, 'saved')
    window.setTimeout(() => {
      if (cellState.value[field.name] === 'saved') setCellState(field.name, '')
    }, 1500)
  } catch {
    rec.link_ids = { ...rec.link_ids, [field.name]: previousIds ?? [] }
    rec.fields[field.name] = previousText
    setCellState(field.name, 'error')
  } finally {
    savingField.value = ''
  }
}

/** 正在新增的那个关联字段（发请求时禁住按钮，避免连点建出两条空行） */
const addingLinked = ref('')

/** 这个分区能不能新增（看页面有没有给它配回指字段；不配就不显示按钮） */
function canAddLinked(field: string): boolean {
  return Boolean(props.linkCreateBackRefs?.[field])
}

/**
 * 新增一条关联记录：只建一条**带回指的空行**，字段让用户就地在卡片里填。
 *
 * 建完把新记录拉回来并展开：空行看不见的话，用户会以为没建成而反复点，建出好几条孤儿。
 */
async function addLinked(field: string): Promise<void> {
  const rec = props.record
  const backRef = props.linkCreateBackRefs?.[field]
  if (!rec || !backRef || !canUpdate.value || addingLinked.value) return

  addingLinked.value = field
  try {
    const res = await FeishuWorkbenchAPI.createLinkedRecord(props.role, field, { [backRef]: [rec.record_id] })
    const created = res.data.data.record_id
    // 回指 id 也补进内存里的 link_ids——紧接着的重拉要按它去筛，缺了就拉不到刚建的那一条
    rec.link_ids = { ...rec.link_ids, [field]: [...(rec.link_ids?.[field] ?? []), created] }
    await ensureLinkBlock(field, true)
    if (!blockOf(field).records.some((r) => r.record_id === created)) {
      // 重拉失败（网络/权限）时也得让这条空行出现在眼前：它已经在飞书里了，界面上看不见
      // 就等于「点了没反应」，用户会再点一次，建出第二条。
      patchBlock(field, {
        records: [
          ...blockOf(field).records,
          {
            record_id: created,
            fields: { [backRef]: rec.fields[primaryName.value] ?? '' },
            link_ids: { [backRef]: [rec.record_id] },
            user_refs: {},
          },
        ],
      })
    }
    openedLinks.value = { ...openedLinks.value, [field]: blockOf(field).records.map((r) => r.record_id) }
  } catch {
    // 具体报错由拦截器弹出。这里什么都不改：没有乐观更新要回滚，空行也没建出来，
    // 用户看完提示可以再点一次。
  } finally {
    addingLinked.value = ''
  }
}

/**
 * 关联字段（订单表的「供应商」）摆成下拉时的候选。
 *
 * 键是 `关联字段|字段名`：两页拆自同一个关联字段，而同一页里字段名可能重名。
 *
 * 候选**只能就地为这一页拉**：`linkSections` 里那几张表是「本工作台表上的关联」，
 * 订单表不在其中，它的「供应商」指向哪张表只有再问一次后端才知道。
 */
const linkPickerOptions = ref<Record<string, LinkOption[]>>({})
const linkPickerLoading = ref<Record<string, boolean>>({})

function pickerKey(blockField: string, name: string): string {
  return `${blockField}|${name}`
}

/**
 * 这个字段该摆成下拉吗：**单值关联**（18）且飞书侧本来就能改。
 *
 * 双向关联（21）不收：飞书里它也是只读的，另一侧才有编辑入口。
 */
function isLinkEditable(field: FieldMeta): boolean {
  return canUpdate.value && field.editable && field.type === FIELD_TYPE.SINGLE_LINK
}

/** 关联记录上这个字段当前的 record_id（空串 = 没选） */
function linkedLinkId(record: BitableRecord, field: FieldMeta): string {
  return record.link_ids?.[field.name]?.[0] ?? ''
}

/** 拉这一页里能改的关联字段的候选（已经拉过的跳过；拉失败就让那格空着，别的照常能改） */
async function loadLinkPicker(blockField: string, tableId: string, fields: FieldMeta[]): Promise<void> {
  const wanted = fields.filter((field) => field.editable && field.type === FIELD_TYPE.SINGLE_LINK)
  if (!wanted.length || !tableId) return

  await Promise.all(
    wanted.map(async (field) => {
      const key = pickerKey(blockField, field.name)
      if (linkPickerOptions.value[key]) return
      linkPickerLoading.value = { ...linkPickerLoading.value, [key]: true }
      try {
        const res = await FeishuWorkbenchAPI.getLinkOptions(
          props.role,
          field.name,
          undefined,
          tableId,
          props.linkOptionFilters?.[field.name]
        )
        linkPickerOptions.value = { ...linkPickerOptions.value, [key]: res.data.data ?? [] }
      } catch {
        // 具体报错由拦截器弹出；候选空着，这一格仍显示当前值
      } finally {
        linkPickerLoading.value = { ...linkPickerLoading.value, [key]: false }
      }
    })
  )
}

/**
 * 切到关联子详情（或它拆出来的字段页）：先把那张表拉回来；拆出来的字段页还要再拉一次
 * 页内那些**能改的关联字段**（单值关联）的候选——`loadLinkPicker` 自己会跳过没有这类字段
 * 的页，所以这里不必先判断。
 */
async function openLinkTab(tab: LinkTab | LinkedFieldsTab): Promise<void> {
  await ensureLinkBlock(tab.field)
  if (tab.kind === 'linked') {
    await loadLinkPicker(tab.field, blockOf(tab.field).tableId, splitFields(tab))
  }
}

watch(activeTab, (key) => {
  const tab = tabs.value.find((t) => t.key === key)
  if (tab && tab.kind !== 'fields') void openLinkTab(tab)
})

watch(
  () => [props.visible, props.record?.record_id] as const,
  async ([visible]) => {
    if (!visible) return
    cellState.value = {}
    savingField.value = ''
    linkedCellState.value = {}
    savingLinked.value = ''
    addingLinked.value = ''
    editingCell.value = null
    expandedIds.value = []
    // 换了一条记录就得把关联分区恢复成「没拉过」，否则会显示上一条商机的报价
    openedLinks.value = {}
    linkSections.value = [
      ...linkFields.value.map((field) => ({
        field,
        loading: false,
        loaded: false,
        tableId: '',
        fields: [],
        records: [],
      })),
      // 反查页（iFreight 数据）也走同一套「点到那个 Tab 才拉」，只是取数方式不同（见 linkSections）
      ...(props.config?.related_blocks ?? []).map((block) => ({
        field: block.key,
        related: true,
        loading: false,
        loaded: false,
        tableId: '',
        fields: [],
        records: [],
      })),
    ]
    const first = tabs.value[0]
    activeTab.value = first?.key ?? ''
    // 落到关联页就把它拉出来（上面那个 watcher 也会触发一次，被 loaded/loading 挡住）
    const [, users] = await Promise.all([
      first && first.kind !== 'fields' ? openLinkTab(first) : Promise.resolve(),
      props.role ? loadUserOptions(props.role) : Promise.resolve<UserOption[]>([]),
    ])
    directory.value = users
    // 反查页提前拉：订单那几个子 Tab 顶部都摆着分单号（逐个 iFreight 记录），不先拉的话
    // 它们要等用户点开「iFreight 数据」那一页才有值——而订单 Tab 才是默认落点。
    // **一次把全部反查页拿回来**（后端同一张表只扫一遍），比原来「只提前 iFreight、别的
    // 点到才拉」多拿了「复制提单编组」「投保编组」两页，但那两页本来就要扫同一张表——
    // 合并之后总往返反而从 3 次降到 1 次。
    void ensureRelatedBlocks()
  },
  { immediate: true }
)
</script>

<style scoped>
/*
  表头：标题（商机信息那一长串）+ 单票状态标签并排。
  `baseline` 对齐让标签落在标题的基线上而不是顶部；`flex-wrap` 是给窄屏兜底——
  标题长起来会把标签挤出抽屉，换行总比挤没了强。标签自己 `flex: none`：不然会被
  标题的长文本压扁成一个点。
*/
.drawer-head {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  align-items: baseline;
}

.drawer-title {
  min-width: 0;
}

.drawer-head :deep(.el-tag) {
  flex: none;
}

/*
  紧凑多列网格：字段多（商机 24 个 / 货物 10 个）且多数很短，一列排下来要滚很久。
  三列 + 标签在上值在下，一屏能看全；长字段单独占满一行（见 is-wide）。
*/
.field-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 20px;
}

/*
  关联记录（航线报价 28 个字段）沿用同样的网格，但列数**按宽度自动排**：
  固定两列时格子被拉得很宽、值却只有几个字，一屏只看得到四五条；
  自动排到五六列之后，28 个字段三四行就摆完了——这正是「一屏看到全部信息」的前提。
*/
.field-grid-linked {
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px 16px;
}

/*
  窄屏降到两列。**必须排除关联网格**（`:not(...)` 顺带把权重抬到这一条之上）：
  它按 `auto-fill` 自己算列数，被这条同权重、后出现的规则盖掉就会退回两列——
  格子重新被拉宽，「一屏看全」当场失效。
*/
@media (width <= 1400px) {
  .field-grid:not(.field-grid-linked) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.detail-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

/* 只有一个子详情（没配 detail_groups 也没配 linkBlocks）时，不摆那排单按钮的 Tab 头 */
.detail-tabs.is-single :deep(.el-tabs__header) {
  display: none;
}

.field-item {
  /* min-width:0 让格子能收缩；overflow:hidden 是**兜底**：个别字段的内容（附件那一行）
     自己不肯收，会横着压到右边一格上，看起来就是两块内容叠在一起。 */
  min-width: 0;
  padding: 4px 6px;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 4px;
}

.field-item.is-wide {
  grid-column: 1 / -1;
}

/*
  分单号（每个订单子 Tab 顶部那一块）：一行一条 iFreight。
  一张订单挂两条是常态，两条的分单号看起来就像同一批单据——「第 N 条」这个序号是分辨
  「哪个框是哪条记录」的唯一线索，所以不让它换行、也不让输入框把它挤没。
*/
.fendan-fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fendan-field {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 2px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
}

.fendan-field > :last-child {
  flex: 1 1 auto;
  min-width: 0;
}

.fendan-no {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.fendan-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

/* 保存反馈与 `.field-item` 同一套（描边色），不弹 toast */
.fendan-field.state-saving {
  border-color: var(--el-color-primary-light-5);
}

.fendan-field.state-saved {
  border-color: var(--el-color-success-light-5);
}

.fendan-field.state-error {
  border-color: var(--el-color-danger-light-5);
}

/* 保存状态：与索引页同一套反馈（描边色），不弹 toast */
.field-item.state-saving {
  border-color: var(--el-color-primary-light-5);
}

.field-item.state-saved {
  border-color: var(--el-color-success-light-5);
}

.field-item.state-error {
  border-color: var(--el-color-danger-light-5);
}

/*
  「是/否」型字段的取值染色（见 `boolTone`）。判据是**取值**而不是字段类型：
  同一个格子里换成别的选项（或清空）时 `boolTone` 不再返回类名，颜色自然消失。

  值是下拉框，所以颜色只能透过 `:deep()` 打到 Element Plus 的壳上。写字色而不是只描边：
  扫一眼列表要的就是「绿的一列 / 红的一列」，边框太细看不出来。底色只给最浅一档，
  免得抢过上面那行字段名。
*/
.field-item.tone-yes :deep(.el-select__selected-item) {
  color: var(--el-color-success);
}

.field-item.tone-yes :deep(.el-select__wrapper) {
  background: var(--el-color-success-light-9);
  box-shadow: 0 0 0 1px var(--el-color-success-light-5) inset;
}

.field-item.tone-no :deep(.el-select__selected-item) {
  color: var(--el-color-danger);
}

.field-item.tone-no :deep(.el-select__wrapper) {
  background: var(--el-color-danger-light-9);
  box-shadow: 0 0 0 1px var(--el-color-danger-light-5) inset;
}

/* 字段名与它的说明都在 WeidaFieldLabel 里（说明改成悬停提示），间距跟着组件走 */

.button-slot {
  display: flex;
  gap: 8px;
  align-items: center;
}

.button-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.link-block {
  min-height: 120px;
}

/*
  只读页（反查回来的 iFreight 数据）顶上的一句话。

  铺出来的是**一页灰控件**——不说明白，看着像坏了或者像没权限；说一句「飞书的自动化写的」，
  灰就是正常的了。
*/
.link-hint {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/*
  分区**上方**的动作按钮条（对客方案的「发起报价审批」）。

  跟下面的表头之间留一条细线：这个按钮作用于**整块方案列表**，不划线容易被当成某一行
  的操作。左对齐——飞书里它也在左上角。
*/
.link-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  align-items: center;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

/* 动作区里的一个可改字段：字段名在左、控件在右，与字段页里「名在上、控件在下」不同——
   这里只有一两个字段、又是横排，竖着排会把这一行撑得老高 */
.link-action-field {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 6px;
  /* 与字段页的 `.field-item` 一样留出透明描边，保存反馈才不至于把版面顶动 */
  border: 1px solid transparent;
  border-radius: 4px;
}

.link-action-field.state-saving {
  border-color: var(--el-color-primary-light-5);
}

.link-action-field.state-saved {
  border-color: var(--el-color-success-light-5);
}

.link-action-field.state-error {
  border-color: var(--el-color-danger-light-5);
}

.link-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.link-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/*
  报价表格。

  用 CSS grid 而不是 ElTable：列是**动态**的（跟着配置走），而格子要能点开就地改、
  末列还得是个展开箭头——ElTable 那套插槽 + 固定行高的写法在这里反而绕。
  列少时用 `1fr` 铺满，列多到装不下就把外层横向滚起来（窄屏不挤压内容）。
*/
.lt-wrap {
  overflow-x: auto;
}

.lt-grid {
  display: grid;
  min-width: 0;
  font-size: 13px;
}

.lt-head {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  color: var(--el-text-color-regular);
  white-space: nowrap;
  border-bottom: 1px solid var(--el-border-color);
}

.lt-head,
.lt-cell {
  padding: 6px 8px;
}

.lt-head-arrow {
  border-bottom: 1px solid var(--el-border-color);
}

.lt-cell {
  display: flex;
  align-items: center;

  /* overflow:hidden 与 min-width:0 一起兜住「内容比格子宽」的情况：不裁的话格子里的
     东西会横着压到右边一格上，看着就是两格内容叠在一起（附件那一行最容易这样）。 */
  min-width: 0;
  overflow: hidden;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

/* 能改的格子给个手型和悬停底色：不然看不出哪几格点得动 */
.lt-cell.is-editable {
  cursor: cell;
}

.lt-cell.is-editable:hover {
  background: var(--el-fill-color-light);
}

.lt-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/*
  保存反馈：整格换底色。
  与字段页的描边同一套语义（primary=在存 / success=存好了 / danger=失败）——
  这里是一格挨一格的表格，描边会跟表格线糊在一起。
*/
.lt-cell.state-saving {
  background: var(--el-color-primary-light-9);
}

.lt-cell.state-saved {
  background: var(--el-color-success-light-9);
}

.lt-cell.state-error {
  background: var(--el-color-danger-light-9);
}

.lt-cell-arrow {
  justify-content: center;
  color: var(--el-text-color-placeholder);
  cursor: pointer;
}

.lt-arrow {
  transition: transform 0.2s;
}

.lt-arrow.is-open {
  transform: rotate(90deg);
}

.lt-detail {
  grid-column: 1 / -1;
  padding: 10px 8px 14px;
  background: var(--el-fill-color-blank);
  border-bottom: 1px solid var(--el-border-color);
}

/*
  关联分区下方的收尾字段。跟表格之间拉一条线分开——不隔开的话，「最终对客方案」看着像
  上面那张表格的最后一行。用三列网格（与字段页一致）而不是铺满：这几个字段都很短。
*/
.link-trailing {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 20px;
  padding-top: 12px;
  margin-top: 16px;
  border-top: 1px solid var(--el-border-color);
}

@media (width <= 1400px) {
  .link-trailing {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.full-width {
  width: 100%;
}

.linked-title {
  font-weight: 600;
}

.drawer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.save-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
