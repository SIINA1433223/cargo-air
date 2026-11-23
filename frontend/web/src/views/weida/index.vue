<template>
  <div class="wb">
    <MasterDataPanel v-if="role === 'master-data'" />
    <template v-if="role !== 'master-data'">
      <header v-if="!isDashboard" class="wb-head">
        <div class="wb-title">
          <div class="wb-title-text">
            <h2>{{ config?.name || '工作台' }}</h2>
            <span class="wb-subtitle">{{ config?.description || '交接操作 · iFreight' }}</span>
          </div>
        </div>
        <div class="wb-actions">
          <a-input-search
            v-model:value="keyword"
            class="wb-search wb-t-search"
            size="small"
            placeholder="搜索编号 / 客户 / 品名"
            allow-clear
            @search="applySearch" />
          <a-badge class="wb-t-notify" :count="unreadCount" :offset="[-2, 2]">
            <a-button size="small" @click="openNotifications">通知</a-button>
          </a-badge>

          <a-button class="wb-t-textcolor" size="small" @click="textColorOpen = true">字色</a-button>
          <a-select
            v-if="savedViews.length"
            v-model:value="activeViewId"
            size="small"
            placeholder="视图"
            allow-clear
            class="wb-view"
            style="width: 120px"
            @change="applyView">
            <a-select-option v-for="v in savedViews" :key="v.id" :value="v.id">{{ v.name }}</a-select-option>
          </a-select>
          <a-select class="wb-t-filtermode" :value="conditions.length ? 'filtered' : 'all'" size="small" style="width: 104px" @change="openFilters">
            <a-select-option value="all">不筛选</a-select-option>
            <a-select-option value="filtered">已筛选</a-select-option>
          </a-select>
          <a-button class="wb-t-filter" size="small" @click="openFilters">
            筛选<span v-if="conditions.length">（{{ conditions.length }}）</span>
          </a-button>
          <a-button class="wb-t-query" size="small" type="primary" @click="applySearch">查询</a-button>
          <a-button
            v-if="role === 'customer_service'"
            class="wb-t-new"
            size="small"
            type="primary"
            @click="openCreate">
            新建商机
          </a-button>
          <a-button class="wb-t-columns" size="small" @click="columnOpen = true">自定义列</a-button>
          <a-button class="wb-t-paint" size="small" @click="paintOpen = true">填色</a-button>
          <!-- 比例调整：只缩放索引页表格（弹窗、抽屉不受影响），按账号记本机 -->
          <el-select
            class="wb-t-zoom"
            v-model="tableZoom"
            size="small"
            style="width: 100px"
            @change="saveTableZoom">
            <el-option v-for="z in ZOOM_OPTIONS" :key="z" :label="`比例 ${z}%`" :value="z" />
          </el-select>
          <a-dropdown class="wb-t-more" trigger="click">
            <a-button size="small">更多</a-button>
            <template #overlay>
              <a-menu @click="({ key }) => onTopCommand(String(key))">
                <a-menu-item key="home">返回总览</a-menu-item>
                <a-menu-divider />
                <a-menu-item key="create">新建</a-menu-item>
                <a-menu-item key="columns">自定义列</a-menu-item>
                <a-menu-item key="paint">填色（{{ paintRules.length }}）</a-menu-item>
                <a-menu-item key="password">修改密码</a-menu-item>
                <a-menu-item key="logout">退出登录</a-menu-item>
                <a-menu-item key="refresh" :disabled="loading">刷新</a-menu-item>
                <a-menu-divider v-if="role === 'pricing' || role === 'route_quote' || role === 'plan'" />
                <a-menu-item v-if="role === 'pricing'" key="route-quote">航线报价</a-menu-item>
                <a-menu-item v-if="role === 'route_quote' || role === 'plan'" key="pricing">
                  待报价队列
                </a-menu-item>
                <a-menu-item v-if="role === 'route_quote' || role === 'pricing'" key="plan">
                  对客方案
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </div>
      </header>

      <div v-if="isDashboard" class="wb-dash">
        <div class="wb-dash-head">
          <div class="wb-dash-title-group">
            <div class="wb-dash-back" @click="router.push('/weida')">← 总览</div>
            <div>
              <h3>{{ dashboard.title || '仪表盘' }}</h3>
              <p>{{ dashboard.subtitle || '' }}</p>
            </div>
          </div>
          <div class="wb-dash-tools">
            <el-radio-group v-model="dashPeriod" size="small" @change="loadStats">
              <el-radio-button label="week">按周</el-radio-button>
              <el-radio-button label="month">按月</el-radio-button>
              <el-radio-button label="range">自定义区间</el-radio-button>
            </el-radio-group>
            <el-date-picker
              v-if="dashPeriod === 'range'"
              v-model="dashRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              size="small"
              @change="loadStats" />
            <el-select
              v-if="dashRole === 'customer_service'"
              v-model="dashOwner"
              clearable
              size="small"
              placeholder="全部业务员"
              style="width: 150px"
              @change="loadStats">
              <el-option
                v-for="o in dashOwnerOptions"
                :key="o"
                :label="o"
                :value="o" />
            </el-select>
            <el-date-picker
              v-if="dashRole === 'customer_service'"
              v-model="dashTrendRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              size="small"
              placeholder="趋势区间（默认 W41 起）"
              style="width: 220px"
              @change="loadStats" />
            <el-radio-group v-if="dashRole === 'pricing'" v-model="dashGroup" size="small" @change="loadStats">
              <el-radio-button label="customer">按客户</el-radio-button>
              <el-radio-button label="supplier">按供应商</el-radio-button>
            </el-radio-group>
            <el-button size="small" @click="loadStats">刷新</el-button>
          </div>
        </div>
        <div v-if="!dashboard.summary" class="wb-dash-cards">
          <div v-for="c in dashboard.cards" :key="c.label" class="wb-dash-card">
            <span class="v">{{ c.value }}</span>
            <span class="l">{{ c.label }}</span>
          </div>
        </div>
        <div v-if="dashboard.summary" class="wb-dash-summary">
          <div class="big">{{ dashboard.summary.rate }}</div>
          <div class="items">
            <span>拿到 {{ dashboard.summary.won }} / 新建 {{ dashboard.summary.created }}</span>
            <span>计费重 {{ dashboard.summary.wonWeight }} / {{ dashboard.summary.createdWeight }} · {{ dashboard.summary.weightRate }}</span>
            <span v-if="dashboard.summary.undetermined !== undefined">还没判定 {{ dashboard.summary.undetermined }}</span>
          </div>
        </div>
        <div v-if="dashboard.reasons?.length" class="wb-dash-reasons">
          <span class="lbl">没拿到货的原因</span>
          <el-tag v-for="r in dashboard.reasons" :key="r.name" size="small" effect="plain">
            {{ r.name }} {{ r.value }}
          </el-tag>
        </div>
        <div v-if="dashboard.summary2" class="wb-dash-summary">
          <div class="big">{{ dashboard.summary2.rate }}</div>
          <div class="items">
            <span>按时完成率</span>
            <span>截止前完成 {{ dashboard.summary2.onTime }}</span>
            <span>超过截止 {{ dashboard.summary2.late }}</span>
            <span>判不了 {{ dashboard.summary2.undetermined }}</span>
          </div>
        </div>
        <div class="wb-dash-sections">
          <div v-for="s in dashboard.sections" :key="s.key" class="wb-dash-section">
            <h4>{{ s.title }}</h4>
            <p>{{ s.caption }}</p>
            <ECharts v-if="s.type === 'line'" :options="sectionOption(s)" height="340px" @click="(p: any) => onSectionClick(s, p)" />
            <div v-else-if="s.data?.length" class="wb-col-chart">
              <div class="wb-col-axis">
                <span v-for="t in chartTicks(s)" :key="t">{{ t }}</span>
              </div>
              <div class="wb-col-plot">
                <div class="wb-col-grid">
                  <i v-for="(t, i) in chartTicks(s)" :key="i" :style="{ top: `${(i / 4) * 100}%` }"></i>
                </div>
                <div class="wb-col-bars">
                  <div
                    v-for="d in s.data"
                    :key="d.name"
                    class="wb-col-item"
                    :title="`${d.name}：${d.value} 票`"
                    @click="onSectionClick(s, { name: d.name })">
                    <div class="wb-col-space">
                      <div class="wb-col-bar" :style="{ height: `${barHeight(d, s)}%` }">
                        <div class="wb-col-dark" :style="{ height: `${barPart(d)}%` }"></div>
                        <div class="wb-col-light"></div>
                      </div>
                    </div>
                    <div class="wb-col-name">{{ d.name }}</div>
                    <div class="wb-col-caption">{{ d.caption || `${d.value} 票` }}</div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="wb-col-empty">暂无数据</div>
          </div>
        </div>
        <div v-if="dashboard.todayWarehouse?.length" class="wb-dash-section">
          <h4>今日入仓操作时间</h4>
          <el-table :data="dashboard.todayWarehouse" size="small" border>
            <el-table-column prop="title" label="编号" width="170" />
            <el-table-column prop="customer" label="客户" />
            <el-table-column prop="operator" label="操作" width="130" />
          </el-table>
        </div>
        <div v-if="dashboard.todayFlights?.length" class="wb-dash-section">
          <h4>今日航班</h4>
          <el-table :data="dashboard.todayFlights" size="small" border>
            <el-table-column prop="title" label="编号" width="170" />
            <el-table-column prop="flight" label="航班时间" width="170" />
            <el-table-column prop="operator" label="操作" width="130" />
          </el-table>
        </div>
        <div v-if="dashboard.operatorTable?.length" class="wb-dash-section">
          <h4>按操作人员</h4>
          <el-table :data="dashboard.operatorTable" size="small" border>
            <el-table-column prop="name" label="操作人员" />
            <el-table-column prop="week" label="本周" width="90" />
            <el-table-column prop="month" label="本月" width="90" />
            <el-table-column prop="pending" label="未完成" width="90" />
            <el-table-column prop="done" label="已完成" width="90" />
          </el-table>
        </div>
      </div>
      <template v-else>
        <div
          ref="tableRef"
          class="wb-table"
          :style="{ '--wb-row-height': rowHeight + 'px', zoom: tableZoom / 100 }">
          <a-table
            :data-source="rows"
            :columns="antColumns"
            :loading="loading"
            :pagination="false"
            :row-key="(record: Row) => record.id"
            :custom-row="antCustomRow"
            :scroll="{ x: 'max-content', y: tableScrollY }"
            size="small"
            bordered>
            <template #bodyCell="{ column, record, index }">
              <div class="wb-cell-clip">
              <template v-if="column.key === '__index'">{{ index + 1 }}</template>
              <template v-else-if="column.key === '__status'">
                <div
                  class="wb-status-cell"
                  :class="{ 'wb-alert-flashing': activeAlerts(record).some((a: any) => a.severity === 'critical') }"
                  @click="openAlerts(record)">
                  <div class="wb-status-top">
                    <span
                      v-if="record.fields.single_status?.value"
                      class="wb-status-pill"
                      :class="{ 'wb-status-clickable': canClickStatus(record) }"
                      :title="canClickStatus(record) ? '点击标记为已发送询价' : ''"
                      :style="statusPillStyle(record.fields.single_status.value)"
                      @click.stop="onStatusClick(record)">
                      {{ record.fields.single_status.value }}
                    </span>
                    <span v-else class="cell-empty">—</span>
                    <!-- 所有叹号统一靠右：账单「客人未确认」黄叹号、周末/节假日、提醒 -->
                    <span class="wb-status-flags">
                      <span
                        v-if="role === 'customer_service' && record.fields.bill_status?.value === '客人未确认'"
                        class="wb-bill-alert-badge"
                        title="客人未确认：账单发出后 3 天还没收到回复">!</span>
                      <span
                        v-if="record.weekend_check?.pending"
                        class="wb-weekend-dot"
                        title="周末/节假日进仓待确认"
                        @click.stop="openWeekend(record)">!</span>
                      <!-- 正在闪红框时不再画红色叹号（重复） -->
                      <span
                        v-if="activeAlerts(record).length && !activeAlerts(record).some((a: any) => a.severity === 'critical')"
                        class="wb-alert-dot"
                        :class="activeAlerts(record)[0].severity === 'critical' ? 'is-critical' : 'is-warning'">
                        !
                      </span>
                    </span>
                  </div>
                  <span v-if="record.flight_changed" class="wb-flight-changed">航班更新</span>
                  <span
                    v-if="role !== 'pricing'"
                    class="wb-flight-status"
                    :class="record.fields.flight_status?.value ? flightStatusClass(record.fields.flight_status.value) : 'is-empty'">
                    {{ record.fields.flight_status?.value || '航班状态' }}
                  </span>
                </div>
              </template>
              <template v-else-if="column.key === '__view_left'">
                <a-button type="link" size="small" @click.stop="openDetail(record)">查看</a-button>
              </template>
              <template v-else-if="column.key === '__todo'">
                <a-tag :color="todoDone(record) === todoFields.length ? 'success' : 'default'">
                  {{ todoDone(record) }}/{{ todoFields.length }}
                </a-tag>
              </template>
              <template v-else-if="column.key === '__view'">
                <a-button type="link" size="small" @click.stop="openDetail(record)">查看</a-button>
              </template>
              <template v-else>
                <div
                  v-if="inlineEdit && inlineEdit.rowId === record.id && inlineEdit.key === column.key"
                  class="wb-inline-size">
                  <!-- 按字段类型给控件：日期弹日期面板、单选取值弹下拉、人/客商弹选择器，
                       别的才是文本框（妙搭 EditableCell 同一套口径） -->
                  <!-- 日期用 Element 面板（接受字符串值，选完即存） -->
                  <el-date-picker
                    v-if="inlineEdit.type === 'date'"
                    v-model="inlineEditValue"
                    type="date"
                    value-format="YYYY-MM-DD"
                    size="small"
                    style="width: 100%"
                    @click.stop
                    @change="saveInlineEdit" />
                  <el-date-picker
                    v-else-if="inlineEdit.type === 'datetime'"
                    v-model="inlineEditValue"
                    type="datetime"
                    value-format="YYYY-MM-DD HH:mm:ss"
                    size="small"
                    style="width: 100%"
                    @click.stop
                    @change="saveInlineEdit" />
                  <a-select
                    v-else-if="inlinePersonColumns.has(inlineEdit.column || '')"
                    v-model:value="inlineEditValue"
                    size="small"
                    show-search
                    allow-clear
                    style="width: 100%"
                    :options="staffOptions.map((p: any) => ({ label: p.name, value: p.name }))"
                    @click.stop
                    @change="saveInlineEdit" />
                  <a-select
                    v-else-if="inlineVendorColumns.has(inlineEdit.column || '')"
                    v-model:value="inlineEditValue"
                    size="small"
                    show-search
                    allow-clear
                    style="width: 100%"
                    :options="vendorOptions.map((v) => ({ label: v.name, value: v.name }))"
                    @click.stop
                    @change="saveInlineEdit" />
                  <a-select
                    v-else-if="(inlineEdit.options?.length || 0) > 0"
                    v-model:value="inlineEditValue"
                    size="small"
                    show-search
                    allow-clear
                    style="width: 100%"
                    :options="inlineEdit.options!.map((o) => ({ label: o, value: o }))"
                    @click.stop
                    @change="saveInlineEdit" />
                  <a-input
                    v-else
                    v-model:value="inlineEditValue"
                    class="wb-inline-edit"
                    size="small"
                    :type="colOf(column.key)?.kind === 'template' ? 'textarea' : 'text'"
                    :rows="colOf(column.key)?.kind === 'template' ? 2 : undefined"
                    autofocus
                    @click.stop
                    @blur="saveInlineEdit"
                    @press-enter="colOf(column.key)?.kind !== 'template' && saveInlineEdit()" />
                  <a-button
                    v-if="colField(column.key) === 'size_length_width_height_quantity'"
                    size="small"
                    @mousedown.prevent
                    @click.stop="calcInlineVolume">计</a-button>
                </div>
                <div v-else-if="column.key === 'bill_status'" @click.stop>
                  <a-select
                    :value="record.fields.bill_status?.step ?? '__auto'"
                    size="small"
                    allow-clear
                    placeholder="自动"
                    style="width: 170px"
                    @change="(v: any) => changeBillStatus(record, v)">
                    <!-- 自动推导出来的那一档也要显示文字（step 为空时以前只显示空白） -->
                    <a-select-option value="__auto">
                      {{ record.fields.bill_status?.value || '自动' }}
                    </a-select-option>
                    <a-select-option v-for="(label, i) in billStatusOptions" :key="i" :value="i + 1">
                      {{ label }}
                    </a-select-option>
                  </a-select>
                </div>
                <template v-else-if="colOf(column.key)?.kind === 'field'">
                  <span :class="{ 'cell-empty': isEmpty(cellValue(record, colField(column.key))) }">
                    {{ display(cellValue(record, colField(column.key)), colType(column.key)) }}
                  </span>
                </template>
                <span v-else-if="colOf(column.key)?.kind === 'template'" class="cell-template">
                  {{ renderTemplate(colExpression(column.key), record) }}
                </span>
                <a-input
                  v-else
                  size="small"
                  :value="blankValues[`${record.id}:${column.key}`] || ''"
                  placeholder="本机备注"
                  @click.stop
                  @update:value="(v: string) => (blankValues[`${record.id}:${column.key}`] = v)" />
              </template>
              </div>
            </template>
            <template #emptyText>
              <a-empty v-if="!loading" description="暂无记录" />
              <span v-else style="color: #6b7280">加载中…</span>
            </template>
          </a-table>
        </div>

        <div class="wb-foot">
          <span class="wb-total">共 {{ total }} 条</span>
          <a-pagination
            v-if="!config?.no_paging"
            v-model:current="page"
            v-model:page-size="pageSize"
            :total="total"
            :show-size-changer="true"
            :page-size-options="['20', '50', '100', '200']"
            show-quick-jumper
            @change="() => load()" />
        </div>
      </template>

      <a-drawer
        v-model:open="drawerOpen"
        :title="detailTitle"
        :width="drawerWidth"
        :closable="false"
        :mask-closable="false"
        :destroy-on-close="true"
        class="wb-detail-drawer">
        <div class="wb-drawer-resizer" title="拖动调整宽度" @mousedown.prevent="startDrawerResize"></div>
        <div v-if="detail" class="wb-detail">
          <div class="wb-detail-meta">
            <div class="wb-detail-title">
              <strong>{{ role === 'domestic_ops' ? detail.fields.single_status?.value || detailTitle : `记录 #${detail.id}` }}</strong>
              <span>{{ role === 'domestic_ops' ? '国内操作工作台' : detailTitle }}</span>
            </div>
            <div class="wb-detail-top-actions">
              <template v-if="role === 'domestic_ops'">
                <el-button v-if="!detail.handover" size="small" @click="handoverOpen = true">周末交接</el-button>
                <el-button v-else size="small" type="warning" plain @click="cancelHandover">撤回周末交接</el-button>
              </template>
              <el-button link size="small" @click="refreshDetail">刷新</el-button>
              <el-button v-if="drawerMode === 'edit'" link size="small" type="danger" @click="deleteDetail">删除</el-button>
              <el-button link size="small" @click="drawerOpen = false">✕</el-button>
            </div>
          </div>
          <div v-if="drawerMode === 'create'" class="wb-create">
            <div class="wb-create-grid">
              <div
                v-for="item in createFieldList"
                :key="item.column"
                class="wb-create-field"
                :class="{ 'wb-create-wide': item.wide }">
                <label>
                  {{ item.label }}
                  <span v-if="item.required" class="wb-required">*</span>
                </label>
                <el-select
                  v-if="item.column === 'customer_name'"
                  v-model="editModel.customer_name"
                  filterable
                  default-first-option
                  clearable
                  placeholder="请选择客户">
                  <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
                </el-select>
                <el-select
                  v-else-if="item.column === 'destination_port'"
                  v-model="editModel[item.column]"
                  multiple
                  collapse-tags
                  filterable
                  remote
                  :remote-method="searchPortOptions"
                  :loading="portOptionsLoading"
                  clearable
                  placeholder="输入三字代码（如 CDG）">
                  <el-option
                    v-for="p in portOptions"
                    :key="`${p.code}-${p.name}`"
                    :label="`${p.code} ${p.name}`"
                    :value="p.code" />
                </el-select>
                <el-select
                  v-else-if="isCreateSelect(item.column)"
                  v-model="editModel[item.column]"
                  :multiple="item.column === 'destination_port'"
                  collapse-tags
                  filterable
                  default-first-option
                  clearable
                  :placeholder="`请选择${item.label}`">
                  <el-option
                    v-for="v in createSelectOptions(item.column)"
                    :key="typeof v === 'object' ? v.code : v"
                    :label="typeof v === 'object' ? `${v.code} ${v.name}` : v"
                    :value="typeof v === 'object' ? v.code : v" />
                </el-select>
                <div v-else-if="item.column === 'size_length_width_height_quantity'" class="wb-size-create">
                  <WeidaFieldEditor
                    v-model="editModel[item.column]"
                    :field="item.field"
                    :person-options="staffOptions"
                    :readonly-value="detail.fields[item.column]?.value" />
                  <el-button size="small" @click="calcCreateVolume">计算体积</el-button>
                </div>
                <WeidaFieldEditor
                  v-else
                  v-model="editModel[item.column]"
                  :field="item.field"
                  :person-options="staffOptions"
                  :readonly-value="detail.fields[item.column]?.value" />
              </div>
            </div>
            <div v-if="createMissing.length" class="wb-create-hint">
              还差 {{ createMissing.length }} 项必填：{{ createMissing.join('、') }}
            </div>
          </div>
          <a-tabs
            v-else-if="detailSections.length"
            v-model:activeKey="activeTab"
            class="wb-tabs"
            @change="onDetailTabChange">
            <a-tab-pane
              v-for="section in detailSections"
              :key="section.label"
              :tab="section.label">
              <div
                v-if="
                  drawerMode === 'edit' && (actionsForTab(section.label).length || commandsForTab(section.label).length)
                "
                class="wb-detail-actions">
                <el-button
                  v-for="cmd in commandsForTab(section.label)"
                  :key="cmd.key"
                  type="primary"
                  :loading="actionBusy === cmd.key"
                  @click="runCommand(cmd.key)">
                  {{ cmd.label }}
                </el-button>
                <el-button
                  v-for="a in actionsForTab(section.label)"
                  :key="a.field"
                  :type="todoChecked(a.field) ? 'success' : 'primary'"
                  :loading="actionBusy === a.field"
                  plain
                  @click="runAction(a.field)">
                  {{ todoChecked(a.field) ? a.done : a.label }}
                </el-button>
              </div>
              <div v-if="section.label === '开票'" class="wb-invoice-block">
                <el-form label-position="left" label-width="120px" class="wb-form">
                  <el-form-item label="账单状态">
                    <a-select
                      :value="detail.fields.bill_status?.step ?? undefined"
                      allow-clear
                      placeholder="自动"
                      style="width: 240px"
                      @change="(v: any) => changeBillStatus(detail as any, v)">
                      <a-select-option v-for="(label, i) in billStatusOptions" :key="i" :value="i + 1">
                        {{ label }}
                      </a-select-option>
                    </a-select>
                  </el-form-item>
                </el-form>
                <h4>发送开票邮件</h4>
                <div class="wb-attach-row">
                  <span class="wb-attach-name">确认账单文件</span>
                  <div v-for="a in detailAttachments['确认账单文件'] || []" :key="a.id" class="wb-attach-row">
                    <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                    <el-button link type="danger" size="small" @click="removeDetailAttachment(a, '确认账单文件')">删除</el-button>
                  </div>
                  <el-upload multiple :show-file-list="false" @click.stop="activeAttachField = '确认账单文件'" :http-request="(opt: any) => uploadDetailAttachment('确认账单文件', opt)">
                    <el-button size="small">上传</el-button>
                  </el-upload>
                </div>
                <el-form label-position="left" label-width="120px" class="wb-form">
                  <el-form-item label="开票备注">
                    <el-input v-model="editModel.invoice_note" type="textarea" :rows="2" />
                  </el-form-item>
                  <el-form-item label="司机信息">
                    <el-input v-model="editModel.driver_info" />
                  </el-form-item>
                  <el-form-item label="报关品名">
                    <el-input v-model="editModel.customs_declaration_goods_name" />
                  </el-form-item>
                </el-form>
                <div class="wb-attach-row">
                  <span class="wb-attach-name">发票</span>
                  <div v-for="a in detailAttachments['发票'] || []" :key="a.id" class="wb-attach-row">
                    <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                    <el-button link type="danger" size="small" @click="removeDetailAttachment(a, '发票')">删除</el-button>
                  </div>
                  <el-upload multiple :show-file-list="false" @click.stop="activeAttachField = '发票'" :http-request="(opt: any) => uploadDetailAttachment('发票', opt)">
                    <el-button size="small">上传</el-button>
                  </el-upload>
                </div>
              </div>
              <el-form
                v-if="section.label !== '国外预报' && section.label !== '开票'"
                label-position="left"
                label-width="120px"
                class="wb-form"
                :class="{
                  'wb-form-compact':
                    section.label === '商机详情' ||
                    (section.label === '货物详情' && role === 'domestic_ops'),
                }">
                <template v-for="grp in detailGroups(section)" :key="grp.label">
                  <h4 v-if="grp.label" class="wb-subhead">{{ grp.label }}</h4>
                  <el-form-item v-for="f in grp.fields" :key="f.column" :label="f.label">
                  <el-select
                    v-if="f.column === 'supplier' || f.column === 'foreign_agent_name'"
                    v-model="editModel[f.column]"
                    filterable
                    clearable
                    placeholder="请选择">
                    <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
                  </el-select>
                  <el-select
                    v-else-if="f.column === 'customer_name'"
                    v-model="editModel.customer_name"
                    filterable
                    default-first-option
                    clearable
                    placeholder="请选择客户">
                    <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
                  </el-select>
                  <el-select
                    v-else-if="f.column === 'departure_location' || f.column === 'destination'"
                    v-model="editModel[f.column]"
                    filterable
                    remote
                    :remote-method="searchPortOptions"
                    :loading="portOptionsLoading"
                    clearable
                    placeholder="输入三字代码（如 CDG）">
                    <el-option
                      v-for="p in portOptions"
                      :key="`${p.code}-${p.name}`"
                      :label="`${p.code} ${p.name}`"
                      :value="p.code" />
                  </el-select>
                  <el-select
                    v-else-if="f.column === 'destination_port'"
                    v-model="editModel.destination_port"
                    multiple
                    collapse-tags
                    filterable
                    remote
                    :remote-method="searchPortOptions"
                    :loading="portOptionsLoading"
                    placeholder="选择三字代码">
                    <el-option v-for="p in portOptions" :key="`${p.code}-${p.name}`" :label="`${p.code} ${p.name}`" :value="p.code" />
                  </el-select>
                  <el-date-picker
                    v-else-if="
                      role === 'customer_service' &&
                      (f.column === 'goods_ready_time' || f.column === 'request_arrival_time')
                    "
                    v-model="editModel[f.column]"
                    type="date"
                    value-format="YYYY-MM-DD"
                    placeholder="选择日期"
                    style="width: 100%" />
                  <div v-else-if="f.column === 'goods_data'" class="wb-readonly-text">
                    {{ opportunityGoodsText(detail.fields) }}
                  </div>
                  <div v-else-if="f.column === 'bill_status'" class="wb-readonly-text">
                    {{ detail.fields.bill_status?.value || '—' }}
                  </div>
                  <div v-else-if="f.column === 'size_length_width_height_quantity'" class="wb-size-edit">
                    <WeidaFieldEditor
                      v-model="editModel[f.column]"
                      :field="f"
                      :person-options="staffOptions"
                      :vendor-options="vendorOptions"
                      :readonly-value="detail.fields[f.column]?.value" />
                    <el-button size="small" @click="calcEditVolume">计算体积</el-button>
                  </div>
                  <WeidaFieldEditor
                    v-else
                    v-model="editModel[f.column]"
                    :field="f"
                    :person-options="staffOptions"
                    :vendor-options="vendorOptions"
                    :readonly-value="detail.fields[f.column]?.value" />
                  </el-form-item>
                  <div
                    v-if="section.label === '提货计划' && role === 'domestic_ops' && (grp.label === '头程' || grp.label === '二程')"
                    class="wb-attach-block wb-attach-in-group">
                    <h4>{{ grp.label === '头程' ? '头程入货通知' : '二程入货通知' }}</h4>
                    <div
                      v-for="a in detailAttachments[grp.label === '头程' ? '头程入货通知' : '二程入货通知'] || []"
                      :key="a.id"
                      class="wb-attach-row">
                      <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                      <el-button link type="danger" size="small" @click="removeDetailAttachment(a, grp.label === '头程' ? '头程入货通知' : '二程入货通知')">删除</el-button>
                    </div>
                    <el-upload
                      multiple
                      :show-file-list="false"
                      @click.stop="activeAttachField = grp.label === '头程' ? '头程入货通知' : '二程入货通知'"
                      :http-request="(opt: any) => uploadDetailAttachment(grp.label === '头程' ? '头程入货通知' : '二程入货通知', opt)">
                      <el-button size="small">上传附件</el-button>
                    </el-upload>
                  </div>
                </template>
              </el-form>
              <div v-if="section.label === '报关'" class="wb-dash-section" style="margin-top: 14px">
                <h4>报关明细</h4>
                <el-table :data="relatedData.ifreight || []" size="small" border>
                  <el-table-column label="主单号" min-width="140">
                    <template #default>{{ display(detail.fields.master_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="分单号" min-width="130">
                    <template #default="{ row }">{{ display(row.fields.house_bl_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="件数" width="80">
                    <template #default="{ row }">{{ display(row.fields.quantity?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="毛重" width="100">
                    <template #default="{ row }">{{ display(row.fields.gross_weight?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="口岸" min-width="180">
                    <template #default>{{ display(detail.fields.export_port?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="预计报关时间" min-width="160">
                    <template #default>{{ display(detail.fields.warehouse_in_operation_time?.value, 'datetime') }}</template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-if="section.label === '代理到货计划'" class="wb-dash-section" style="margin-top: 14px">
                <h4>分单明细</h4>
                <el-table :data="relatedData.ifreight || []" size="small" border>
                  <el-table-column label="主单号" min-width="140">
                    <template #default>{{ display(detail.fields.master_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="分单号" min-width="130">
                    <template #default="{ row }">{{ display(row.fields.house_bl_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="件数" width="80">
                    <template #default="{ row }">{{ display(row.fields.quantity?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="毛重" width="100">
                    <template #default="{ row }">{{ display(row.fields.gross_weight?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="体积" width="90">
                    <template #default="{ row }">{{ display(row.fields.volume?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="中文品名" min-width="150">
                    <template #default="{ row }">{{ display(row.fields.customs_declaration_goods_name?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="英文品名" min-width="220">
                    <template #default="{ row }">{{ display(row.fields.goods_description?.value, 'text') }}</template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-if="section.label === '国外预报'" class="wb-dash-section" style="margin-top: 14px">
                <h4>预报明细</h4>
                <el-table :data="relatedData.ifreight || []" size="small" border>
                  <el-table-column label="INV NO" min-width="120">
                    <template #default>{{ display(detail.fields.client_ref_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="MAWB" min-width="130">
                    <template #default>{{ display(detail.fields.master_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="HAWB" min-width="130">
                    <template #default="{ row }">{{ display(row.fields.house_bl_no?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="PCS" width="70">
                    <template #default="{ row }">{{ display(row.fields.quantity?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="GROSS WEIGHT" width="120">
                    <template #default="{ row }">{{ display(row.fields.gross_weight?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="DIMENSION" min-width="140">
                    <template #default="{ row }">{{ display(row.fields.dimension?.value, 'text') }}</template>
                  </el-table-column>
                  <el-table-column label="CBM" width="80">
                    <template #default="{ row }">{{ display(row.fields.volume?.value, 'number') }}</template>
                  </el-table-column>
                  <el-table-column label="DELIVERY ADDRESS" min-width="260">
                    <template #default="{ row }">{{ display(row.fields.consignee_address?.value, 'text') }}</template>
                  </el-table-column>
                </el-table>
              </div>
              <div v-if="section.label === '货物详情'" class="wb-info-panels">
                <div class="wb-info-row">
                  <span>启运港操作要求</span>
                  <p>{{ detail.fields.origin_operation_requirement?.value || '—' }}</p>
                </div>
                <div class="wb-info-row">
                  <span>目的港操作要求</span>
                  <p>{{ detail.fields.destination_operation_requirement?.value || '—' }}</p>
                </div>
                <div class="wb-info-row">
                  <span>客户操作要求</span>
                  <p>{{ detail.fields.customer_operation_requirement?.value || '—' }}</p>
                </div>
              </div>
              <div
                v-if="section.label === '货物详情' && role === 'domestic_ops'"
                class="wb-attach-block">
                <div v-for="mailField in ['相关资料', '国内邮件', '国外邮件']" :key="mailField" class="wb-attach-inner">
                  <h4>{{ mailField }}</h4>
                  <div v-for="a in detailAttachments[mailField] || []" :key="a.id" class="wb-attach-row">
                    <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                    <el-button link type="danger" size="small" @click="removeDetailAttachment(a, mailField)">删除</el-button>
                  </div>
                  <el-upload
                      multiple
                    :show-file-list="false"
                    @click.stop="activeAttachField = mailField"
                    :http-request="(opt: any) => uploadDetailAttachment(mailField, opt)">
                    <el-button size="small">上传附件</el-button>
                  </el-upload>
                </div>
              </div>
              <div v-if="section.label === '做账明细'" class="wb-dash-section" style="margin-top: 14px">
                <div class="wb-attach-block">
                  <div v-for="docField in ['报价单', '订舱单']" :key="docField" class="wb-attach-inner">
                    <h4>{{ docField }}</h4>
                    <div v-for="a in detailAttachments[docField] || []" :key="a.id" class="wb-attach-row">
                      <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                      <el-button link type="danger" size="small" @click="removeDetailAttachment(a, docField)">删除</el-button>
                    </div>
                    <el-upload
                      multiple
                      :show-file-list="false"
                      @click.stop="activeAttachField = docField"
                      :http-request="(opt: any) => uploadDetailAttachment(docField, opt)">
                      <el-button size="small">上传附件</el-button>
                    </el-upload>
                  </div>
                </div>
                <h4>保费计算器</h4>
                <div class="wb-premium-row">
                  <label>报关金额<el-input v-model="premiumAmount" size="small" /></label>
                  <label>币制
                    <el-select v-model="premiumCurrency" size="small" style="width: 90px">
                      <el-option label="CNY" value="CNY" />
                      <el-option label="USD" value="USD" />
                      <el-option label="EUR" value="EUR" />
                    </el-select>
                  </label>
                  <label>汇率<el-input v-model="premiumRate" size="small" style="width: 90px" /></label>
                  <label>费率(%)<el-input v-model="premiumPct" size="small" style="width: 90px" /></label>
                  <span>保费：{{ premiumResult.toFixed(2) }} {{ premiumCurrency }}</span>
                  <el-button size="small" type="primary" @click="applyPremium">带入</el-button>
                </div>
                <div class="wb-fee-head">
                  <h4 style="margin-top: 12px">额外费用明细</h4>
                  <el-button size="small" @click="feeAddOpen = true">添加额外费用明细</el-button>
                </div>
                <p>按 iFreight 分单号分组，可直接改费用字段</p>
                <el-table :data="extraFees" size="small" border>
                  <el-table-column label="分单号" min-width="120">
                    <template #default="{ row }">{{ row.house_bl_no || '—' }}</template>
                  </el-table-column>
                  <el-table-column label="国内/国外" width="90">
                    <template #default="{ row }">
                      <el-select :model-value="row.domestic_abroad || ''" size="small"
                        @change="(v: string) => saveExtraFee(row, 'domestic_abroad', v)">
                        <el-option label="国内" value="国内" />
                        <el-option label="国外" value="国外" />
                      </el-select>
                    </template>
                  </el-table-column>
                  <el-table-column label="费用类别" min-width="110">
                    <template #default="{ row }">
                      <el-input :model-value="row.fee_category || ''" size="small"
                        @change="(v: string) => saveExtraFee(row, 'fee_category', v)" />
                    </template>
                  </el-table-column>
                  <el-table-column label="币种" width="80">
                    <template #default="{ row }">
                      <el-input :model-value="row.currency || ''" size="small"
                        @change="(v: string) => saveExtraFee(row, 'currency', v)" />
                    </template>
                  </el-table-column>
                  <el-table-column label="数量" width="80">
                    <template #default="{ row }">
                      <el-input :model-value="row.app_quantity ?? ''" size="small"
                        @change="(v: string) => saveExtraFee(row, 'app_quantity', v)" />
                    </template>
                  </el-table-column>
                  <el-table-column label="单价" width="90">
                    <template #default="{ row }">
                      <el-input :model-value="row.unit_price ?? ''" size="small"
                        @change="(v: string) => saveExtraFee(row, 'unit_price', v)" />
                    </template>
                  </el-table-column>
                  <el-table-column label="费用录入情况" min-width="160">
                    <template #default="{ row }">{{ row.fee_entry_status || '—' }}</template>
                  </el-table-column>
                  <template #empty><el-empty description="暂无额外费用" /></template>
                </el-table>
              </div>
            </a-tab-pane>
            <a-tab-pane
              v-if="role === 'customer_service' || role === 'domestic_ops'"
              key="flight"
              tab="航班查询">
              <div v-if="flightSource" class="wb-flight">
                <div class="wb-flight-row">
                  <label>航班状态</label>
                  <el-tag v-if="flightField('flight_status')" type="info">{{ flightField('flight_status') }}</el-tag>
                  <span v-else class="cell-empty">—</span>
                </div>
                <div class="wb-flight-row">
                  <label>全程航班信息</label>
                  <div class="wb-flight-text">{{ flightField('full_flight_info') || '—' }}</div>
                </div>
                <div class="wb-flight-row">
                  <label>代理 deadline</label>
                  <div class="wb-flight-text">{{ flightField('agent_deadline') || '—' }}</div>
                </div>
                <div class="wb-flight-row">
                  <label>最晚到港时间</label>
                  <div class="wb-flight-text">{{ flightField('latest_port_arrival_time') || '—' }}</div>
                </div>
              </div>
              <el-empty v-else description="这条记录还没有关联订单航班信息" />
            </a-tab-pane>
            <a-tab-pane v-if="role === 'domestic_ops'" key="ai_fees" tab="AI费用">
              <div class="wb-related-actions">
                <a-button size="small" type="primary" :loading="aiFeesBusy" @click="confirmAiFees">
                  确认全部费用行
                </a-button>
              </div>
              <el-table v-loading="aiFeesLoading" :data="aiFees" size="small" border>
                <el-table-column label="分单号" min-width="130">
                  <template #default="{ row }">{{ row.house_bl_no || '—' }}</template>
                </el-table-column>
                <el-table-column label="费用类别" min-width="120">
                  <template #default="{ row }">
                    <el-input :model-value="row.fee_category || ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'fee_category', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="非标类别" min-width="140">
                  <template #default="{ row }">
                    <el-input :model-value="row.non_standard_fee_category || ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'non_standard_fee_category', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="数量" width="90">
                  <template #default="{ row }">
                    <el-input :model-value="row.quantity ?? ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'quantity', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="单价" width="100">
                  <template #default="{ row }">
                    <el-input :model-value="row.unit_price ?? ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'unit_price', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="币种" width="80">
                  <template #default="{ row }">
                    <el-input :model-value="row.currency || ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'currency', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="计费单位" width="110">
                  <template #default="{ row }">
                    <el-input :model-value="row.billing_unit || ''" size="small"
                      @change="(v: string) => saveAiFee(row, 'billing_unit', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="90">
                  <template #default="{ row }">{{ row.fee_entry_status || '待复核' }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="暂无 AI 费用行" />
                </template>
              </el-table>
              <h4 style="margin: 16px 0 8px">
                做账费用
                <span style="margin-left: 12px; font-weight: normal; font-size: 12px">
                  <span v-for="(v, k) in extraTotals" :key="k" style="margin-right: 14px">
                    {{ k || '未填币种' }}：{{ Number(v).toFixed(2) }}
                  </span>
                </span>
              </h4>
              <el-table :data="extraFees" size="small" border>
                <el-table-column label="分单号" min-width="120">
                  <template #default="{ row }">{{ row.house_bl_no || '—' }}</template>
                </el-table-column>
                <el-table-column label="国内/国外" width="90">
                  <template #default="{ row }">
                    <el-select :model-value="row.domestic_abroad || ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'domestic_abroad', v)">
                      <el-option label="国内" value="国内" />
                      <el-option label="国外" value="国外" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="费用类别" min-width="120">
                  <template #default="{ row }">
                    <el-input :model-value="row.fee_category || ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'fee_category', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="币种" width="80">
                  <template #default="{ row }">
                    <el-input :model-value="row.currency || ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'currency', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="数量" width="90">
                  <template #default="{ row }">
                    <el-input :model-value="row.app_quantity ?? ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'app_quantity', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="单价" width="100">
                  <template #default="{ row }">
                    <el-input :model-value="row.unit_price ?? ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'unit_price', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="计费单位" width="100">
                  <template #default="{ row }">
                    <el-input :model-value="row.billing_unit || ''" size="small"
                      @change="(v: string) => saveExtraFee(row, 'billing_unit', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="90">
                  <template #default="{ row }">{{ row.fee_entry_status || '—' }}</template>
                </el-table-column>
                <el-table-column label="操作" width="70" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="danger" size="small" @click="removeExtraFee(row)">删除</el-button>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty description="暂无做账费用" />
                </template>
              </el-table>
            </a-tab-pane>
            <a-tab-pane v-for="block in linkBlocks" :key="block.key" :tab="blockLabel(block)">
              <div v-if="block.key === 'plan'" class="wb-approval-bar">
                <span class="wb-approval-label">审批状态</span>
                <el-tag v-if="detail.fields.approval_status?.value" type="success">
                  {{ detail.fields.approval_status.value }}
                </el-tag>
                <span v-else class="cell-empty">—</span>
                <el-button type="primary" size="small" :loading="approvalBusy" @click="requestApproval">
                  {{ detail.fields.initiate_approval?.value ? '已发起审批' : '发起审批' }}
                </el-button>
                <el-button
                  v-if="detail.fields.approval_status?.value === '审批中'"
                  size="small"
                  :loading="approvalBusy"
                  @click="syncApprovalStatus">
                  刷新审批状态
                </el-button>
              </div>
              <div
                v-if="(block.key === 'order' || block.key === 'handover') && relatedOrders.length === 1"
                class="wb-order-card">
                <div class="wb-order-grid">
                  <div
                    v-for="c in blockColumns(block)"
                    :key="c.column"
                    class="wb-order-field"
                    :class="{ 'wb-order-wide': isOrderWide(c) }">
                    <label>{{ c.label }}</label>
                    <div v-if="c.column === 'goods_data'" class="wb-goods-edit">
                      <div class="wb-goods-value">{{ goodsText(relatedOrders[0].id) }}</div>
                      <div class="wb-goods-inputs">
                        <el-input
                          v-model="orderGoodsDraft[relatedOrders[0].id].piece_count"
                          placeholder="件数"
                          @blur="formatGoodsPiece(relatedOrders[0].id)" />
                        <el-input
                          v-model="orderGoodsDraft[relatedOrders[0].id].gross_weight_kg"
                          placeholder="毛重"
                          @blur="formatGoodsGross(relatedOrders[0].id)" />
                        <el-input
                          v-model="orderGoodsDraft[relatedOrders[0].id].volume_m3"
                          placeholder="体积"
                          @blur="formatGoodsVolume(relatedOrders[0].id)" />
                        <div class="wb-goods-size">
                          <el-input
                            v-model="orderGoodsDraft[relatedOrders[0].id].size"
                            type="textarea"
                            :rows="2"
                            placeholder="尺寸，可换行，如 115*98*118*2" />
                          <el-button size="small" @click="recalcGoodsVolume(relatedOrders[0].id)">计算</el-button>
                        </div>
                      </div>
                    </div>
                    <span v-else-if="c.column === 'opportunity_no' || c.column === 'plan_no'" class="wb-order-readonly">
                      {{ display(blockValue(relatedOrders[0], c.column), 'text') }}
                    </span>
                    <el-select
                      v-else-if="c.column === 'departure_location' || c.column === 'destination'"
                      v-model="orderDraft[relatedOrders[0].id][c.column]"
                      filterable
                      remote
                      :remote-method="searchPortOptions"
                      :loading="portOptionsLoading"
                      placeholder="选择三字代码">
                      <el-option
                        v-for="p in portOptions"
                        :key="`${p.code}-${p.name}`"
                        :label="`${p.code} ${p.name}`"
                        :value="p.code" />
                    </el-select>
                    <el-select
                      v-else-if="c.column === 'supplier'"
                      v-model="orderDraft[relatedOrders[0].id][c.column]"
                      filterable
                      clearable
                      placeholder="选择供应商">
                      <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
                    </el-select>
                    <el-select
                      v-else-if="c.column === 'cost_currency'"
                      v-model="orderDraft[relatedOrders[0].id][c.column]"
                      filterable
                      clearable
                      placeholder="选择币种">
                      <el-option label="CNY" value="CNY" />
                      <el-option label="USD" value="USD" />
                      <el-option label="EUR" value="EUR" />
                    </el-select>
                    <el-date-picker
                      v-else-if="c.column === 'warehouse_in_operation_time' || c.column === 'flight'"
                      v-model="orderDraft[relatedOrders[0].id][c.column]"
                      type="date"
                      value-format="YYYY-MM-DD"
                      placeholder="选择日期"
                      style="width: 100%" />
                    <WeidaFieldEditor
                      v-else
                      v-model="orderDraft[relatedOrders[0].id][c.column]"
                      :field="c"
                      :person-options="staffOptions"
                      :readonly-value="blockValue(relatedOrders[0], c.column)" />
                  </div>
                </div>
                <div class="wb-order-actions">
                  <el-button
                    v-if="block.key === 'order'"
                    type="primary"
                    size="small"
                    :loading="bookingBusy === relatedOrders[0].id"
                    :disabled="isOrderDirty(relatedOrders[0])"
                    @click="generateOrderBooking(relatedOrders[0])">
                    {{ blockValue(relatedOrders[0], 'create_booking') ? '重新生成订舱单' : '生成订舱单' }}
                  </el-button>
                  <el-button
                    v-else
                    type="primary"
                    size="small"
                    :loading="handoverBusy === relatedOrders[0].id"
                    :disabled="blockValue(relatedOrders[0], 'handover_domestic_operation') === 1"
                    @click="handoverOrder(relatedOrders[0])">
                    {{ blockValue(relatedOrders[0], 'handover_domestic_operation') === 1 ? '已交接' : '交接' }}
                  </el-button>
                </div>
                <div class="wb-order-attach">
                  <div v-for="a in orderAttachments[relatedOrders[0].id] || []" :key="a.id" class="wb-attach-row">
                    <el-link type="primary" @click="previewAttachment(a)">{{ a.file_name }}</el-link>
                    <el-button link size="small" @click="downloadAttachment(a)">下载</el-button>
                    <el-button link type="danger" size="small" @click="removeOrderAttachment(a, relatedOrders[0].id)">
                      删除
                    </el-button>
                  </div>
                  <el-upload
                      multiple
                    :show-file-list="false"
                    :http-request="(opt: any) => uploadOrderAttachment(relatedOrders[0], opt)"
                    accept=".xlsx,.xls,.pdf,.png,.jpg,.jpeg">
                    <el-button size="small">上传订舱单</el-button>
                  </el-upload>
                </div>
                <!-- 交接操作页的三个订单附件列（妙搭订单表上的附件字段） -->
                <div v-if="block.key === 'handover'" class="wb-attach-block">
                  <div v-for="field in ORDER_ATTACH_FIELDS" :key="field" class="wb-attach-inner">
                    <h4>{{ field }}</h4>
                    <div
                      v-for="a in orderExtraAttachments[relatedOrders[0].id]?.[field] || []"
                      :key="a.id"
                      class="wb-attach-row">
                      <a class="wb-attach-link" @click="downloadAttachment(a)">{{ a.file_name }}</a>
                      <el-button
                        link
                        type="danger"
                        size="small"
                        @click="removeOrderExtraAttachment(a, relatedOrders[0].id, field)">删除</el-button>
                    </div>
                    <el-upload
                      multiple
                      :show-file-list="false"
                      :http-request="(opt: any) => uploadOrderExtraAttachment(relatedOrders[0].id, field, opt)">
                      <el-button size="small">上传附件</el-button>
                    </el-upload>
                  </div>
                </div>
              </div>
              <div
                v-if="block.key === 'pricing' || block.key === 'pricing_review'"
                class="wb-quote-toolbar">
                <span>共 {{ blockCount(block) }} 条</span>
                <el-button v-if="block.key === 'pricing'" type="primary" size="small" @click="openQuoteDialog()">
                  新增航线报价
                </el-button>
              </div>
              <el-table :data="tableRowsFor(block)" size="small" border>
                <template v-for="c in block.columns" :key="c.column">
                  <el-table-column
                    v-if="block.key === 'plan' && c.column === 'generate_quote'"
                    label="添加费用明细"
                    width="130">
                    <template #default="{ row }">
                      <el-button size="small" @click="openFeeDialog(row)">添加费用明细</el-button>
                    </template>
                  </el-table-column>
                  <el-table-column :label="c.label" min-width="120">
                    <template #default="{ row }">
                      <template v-if="block.key === 'pricing' && c.column === 'customer_plan_draft'">
                        <el-button
                          v-if="row.plan_id && row.plan_id > 0"
                          type="primary"
                          size="small"
                          @click="rerunPlan(row)">
                          已生成
                        </el-button>
                        <el-button v-else-if="row.plan_id === -1" size="small" loading>生成中</el-button>
                        <el-button v-else type="primary" size="small" @click="generatePlanFromPricing(row)">
                          生成对客方案
                        </el-button>
                      </template>
                      <template v-else-if="block.key === 'plan' && c.column === 'generate_quote'">
                        <el-button
                          v-if="row.fields.generate_quote?.value"
                          type="primary"
                          size="small"
                          @click="generateQuote(row)">
                          已生成
                        </el-button>
                        <el-button
                          v-else
                          type="primary"
                          size="small"
                          :loading="quoteBusy === row.id"
                          @click="generateQuote(row)">
                          生成
                        </el-button>
                      </template>
                      <template v-else-if="block.key === 'plan' && c.column === 'customer_quote'">
                        <el-input-number
                          :model-value="Number(blockValue(row, 'customer_quote')) || undefined"
                          :controls="false"
                          size="small"
                          style="width: 100%"
                          @change="(v: any) => savePlanField(row, 'customer_quote', v)" />
                      </template>
                      <el-select
                        v-else-if="block.key === 'plan' && c.column === 'quote_currency'"
                        :model-value="blockValue(row, 'quote_currency') || ''"
                        size="small"
                        style="width: 100%"
                        @change="(v: string) => savePlanField(row, 'quote_currency', v)">
                        <el-option label="CNY" value="CNY" />
                        <el-option label="USD" value="USD" />
                        <el-option label="EUR" value="EUR" />
                      </el-select>
                      <el-date-picker
                        v-else-if="block.key === 'plan' && c.column === 'quote_valid_until'"
                        :model-value="blockValue(row, 'quote_valid_until') || ''"
                        type="date"
                        value-format="YYYY-MM-DD"
                        size="small"
                        style="width: 100%"
                        @update:model-value="(v: any) => savePlanField(row, 'quote_valid_until', v)" />
                      <el-input
                        v-else-if="block.key === 'plan' && c.column === 'app_scheme_name'"
                        :model-value="blockValue(row, 'app_scheme_name') || ''"
                        size="small"
                        @change="(v: string) => savePlanField(row, 'app_scheme_name', v)" />
                      <el-select
                        v-else-if="block.key === 'plan' && (c.column === 'departure_port' || c.column === 'destination_port')"
                        :model-value="blockValue(row, c.column) || ''"
                        filterable
                        clearable
                        size="small"
                        style="width: 100%"
                        @change="(v: string) => savePlanField(row, c.column, v)">
                        <el-option
                          v-for="p in quotePortOptions"
                          :key="p.code"
                          :label="`${p.code} ${p.name}`"
                          :value="p.code" />
                      </el-select>
                      <span v-else :class="{ 'cell-empty': isEmpty(blockValue(row, c.column)) }">
                        {{ display(blockValue(row, c.column), c.type) }}
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column
                    v-if="block.key === 'plan' && c.column === 'generate_quote'"
                    label="报价单附件"
                    width="270">
                    <template #default="{ row }">
                      <div v-for="a in planAttachments[row.id] || []" :key="a.id" class="wb-attach-row">
                        <el-link type="primary" @click="previewAttachment(a)">
                          {{ a.file_name }}
                        </el-link>
                        <el-button link size="small" @click="downloadAttachment(a)">下载</el-button>
                        <el-button link type="danger" size="small" @click="removeAttachment(a)">删除</el-button>
                      </div>
                      <el-upload
                      multiple
                        :show-file-list="false"
                        :http-request="(opt: any) => uploadAttachment(row, opt)"
                        accept=".xlsx,.xls,.pdf,.png,.jpg,.jpeg">
                        <el-button size="small">上传</el-button>
                      </el-upload>
                    </template>
                  </el-table-column>
                </template>
                <el-table-column
                  v-if="block.key === 'pricing' || block.key === 'pricing_review'"
                  label="操作"
                  width="72"
                  fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" @click="openQuoteDialog(row)">修改</el-button>
                  </template>
                </el-table-column>
                <el-table-column v-if="block.key === 'pricing'" type="expand" width="48" fixed="right" align="center">
                  <template #default="{ row }">
                    <div class="wb-price-expand">
                      <div v-for="f in block.expand_fields" :key="f.column" class="wb-price-expand-row">
                        <span class="wb-price-expand-label">{{ f.label }}</span>
                        <span :class="{ 'cell-empty': isEmpty(blockValue(row, f.column)) }">
                          {{ display(blockValue(row, f.column), f.type) }}
                        </span>
                      </div>
                    </div>
                  </template>
                </el-table-column>
                <template #empty>
                  <el-empty description="暂无关联记录" />
                </template>
              </el-table>
              <div v-if="block.key === 'pricing_review' && relatedOrders.length" class="wb-review-card">
                <div class="wb-review-head">
                  <strong>最终价格</strong>
                  <el-button type="primary" size="small" :loading="reviewBusy" @click="approvePricing">
                    主管核价
                  </el-button>
                </div>
                <div class="wb-review-status">
                  <span>核价状态</span>
                  <el-tag :type="relatedOrders[0].fields.route_manager_priced?.value === '已核价' ? 'success' : 'warning'">
                    {{ relatedOrders[0].fields.route_manager_priced?.value === '已核价' ? '已核价' : '待主管核价' }}
                  </el-tag>
                </div>
                <div class="wb-review-grid">
                  <!-- 妙搭：航线核价读的是**订单表**那一行（多张订单各一行），不是报价表 -->
                  <div><label>客户</label><span>{{ display(relatedOrders[0].fields.customer_name?.value, 'text') }}</span></div>
                  <div><label>航司</label><span>{{ display(relatedOrders[0].fields.airline?.value, 'text') }}</span></div>
                  <div><label>风险提醒</label><span>{{ display(relatedOrders[0].fields.risk_reminder?.value, 'text') }}</span></div>
                  <div><label>成本金额</label><span>{{ display(relatedOrders[0].fields.cost_amount?.value, 'number') }}</span></div>
                  <div><label>成本币种</label><span>{{ display(relatedOrders[0].fields.cost_currency?.value, 'text') }}</span></div>
                  <div><label>供应商</label><span>{{ display(relatedOrders[0].fields.supplier?.value, 'text') }}</span></div>
                  <div><label>起运地</label><span>{{ display(relatedOrders[0].fields.departure_location?.value, 'text') }}</span></div>
                  <div><label>目的地</label><span>{{ display(relatedOrders[0].fields.destination?.value, 'text') }}</span></div>
                  <div><label>等级、比例要求</label><span>{{ display(relatedOrders[0].fields.level_ratio_req?.value, 'text') }}</span></div>
                  <div><label>航线主管已核价</label><span>{{ relatedOrders[0].fields.route_manager_priced?.value || '—' }}</span></div>
                </div>
              </div>
              <el-form v-if="block.key === 'plan'" label-position="top" class="wb-form wb-plan-footer">
                <el-form-item label="最终对客方案">
                  <el-select v-model="editModel.final_plan_id" clearable placeholder="请选择" style="max-width: 320px">
                    <el-option v-for="p in finalPlanOptions" :key="p.id" :label="p.label" :value="p.id" />
                  </el-select>
                </el-form-item>
                <el-form-item label="是否拿到货">
                  <el-select
                    v-model="editModel.is_goods_received"
                    clearable
                    placeholder="请选择"
                    style="max-width: 160px">
                    <el-option label="是" value="是" />
                    <el-option label="否" value="否" />
                  </el-select>
                </el-form-item>
                <el-form-item label="未拿到货备注">
                  <el-input v-model="editModel.goods_not_received_remark" type="textarea" :rows="2" />
                </el-form-item>
                <div class="wb-plan-footer-actions">
                  <span v-if="editModel.is_goods_received === '是'" class="wb-plan-note">
                    保存之后会自动生成订单数据，订仓单里马上就能看到、可以接着填写处理。
                  </span>
                </div>
              </el-form>
              <el-form v-else-if="block.footer_fields.length" label-position="top" class="wb-form">
                <el-form-item v-for="f in block.footer_fields" :key="f.column" :label="f.label">
                  <WeidaFieldEditor
                    v-model="editModel[f.column]"
                    :field="f"
                    :person-options="staffOptions"
                    :readonly-value="detail.fields[f.column]?.value" />
                </el-form-item>
              </el-form>
            </a-tab-pane>
            <a-tab-pane
              v-for="rb in visibleRelatedBlocks"
              :key="rb.key"
              :tab="rb.label">
              <div v-if="rb.key === 'arrival_report'" class="wb-related-actions">
                <a-button type="primary" size="small" :loading="arrivalBusy" @click="downloadArrivalReport">
                  生成到货预报表
                </a-button>
                <a
                  v-for="a in arrivalAttachments"
                  :key="a.id"
                  class="wb-attach-link"
                  @click="downloadAttachment(a)">
                  {{ a.file_name }}
                </a>
              </div>
              <!-- 车队：按「对账分单号」匹配车队数据（件数/毛重/尺码/体积/比例） -->
              <el-table
                v-if="rb.key === 'fleet'"
                v-loading="relatedLoading[rb.key]"
                :data="[detail.fleet || {}]"
                size="small"
                border>
                <el-table-column label="分单号" min-width="140">
                  <template #default>{{ (relatedData.ifreight || []).map((r: any) => r.fields.house_bl_no?.value).filter(Boolean).join(' / ') || '—' }}</template>
                </el-table-column>
                <el-table-column label="车队件数" width="110">
                  <template #default>{{ (detail.fleet?.count || '').replace(/\n/g, ' / ') || '—' }}</template>
                </el-table-column>
                <el-table-column label="车队毛重" width="110">
                  <template #default>{{ (detail.fleet?.weight || '').replace(/\n/g, ' / ') || '—' }}</template>
                </el-table-column>
                <el-table-column label="车队尺寸" min-width="160">
                  <template #default>{{ (detail.fleet?.size || '').replace(/\n/g, ' / ') || '—' }}</template>
                </el-table-column>
                <el-table-column label="车队体积" width="110">
                  <template #default>{{ detail.fleet?.volume ?? '—' }}</template>
                </el-table-column>
                <el-table-column label="车队比例" width="110">
                  <template #default>{{ detail.fleet?.ratio || '—' }}</template>
                </el-table-column>
                <template #empty><el-empty description="车队数据还没回传（对账分单号匹配）" /></template>
              </el-table>
              <!-- 到货预报表：照妙搭那一页的表头逐列摆（父单字段 + 逐条 iFreight），
                   与生成 Excel 的口径一致（见后端 arrival.py） -->
              <el-table
                v-if="rb.key === 'arrival_report'"
                v-loading="relatedLoading[rb.key]"
                :data="relatedData[rb.key] || []"
                :row-key="(row: any) => row.id"
                size="small"
                border>
                <el-table-column type="index" label="序号" width="48" />
                <el-table-column label="操作日期" width="110">
                  <template #default>{{ display(detail.fields.warehouse_in_operation_time?.value, 'date') }}</template>
                </el-table-column>
                <el-table-column label="航班日期" width="110">
                  <template #default>{{ display(detail.fields.flight?.value, 'date') }}</template>
                </el-table-column>
                <el-table-column label="主单号" width="140">
                  <template #default>{{ display(detail.fields.master_no?.value, 'text') }}</template>
                </el-table-column>
                <el-table-column label="分单号" width="140">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.house_bl_no?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'house_bl_no', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="主单目的港" width="100">
                  <template #default>{{ display(detail.fields.destination?.value, 'text') }}</template>
                </el-table-column>
                <el-table-column label="分单目的港" width="100">
                  <template #default>{{ display(detail.fields.destination?.value, 'text') }}</template>
                </el-table-column>
                <el-table-column label="件数" width="70">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.quantity?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'quantity', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="毛重" width="80">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.gross_weight?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'gross_weight', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="体积" width="80">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.volume?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'volume', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="尺寸" width="200">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.dimension?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'dimension', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="品名" width="160">
                  <template #default="{ row }">
                    <el-input :model-value="row.fields.customs_declaration_goods_name?.value ?? ''" size="small"
                      @change="(v: string) => saveRelatedField(rb, row, 'customs_declaration_goods_name', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="司机信息" width="160">
                  <template #default>
                    <el-input :model-value="detail.fields.driver_info?.value ?? ''" size="small"
                      @change="(v: string) => saveOrderField('driver_info', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="报关方式" width="100">
                  <template #default>
                    <el-input :model-value="detail.fields.customs_declaration_method?.value ?? ''" size="small"
                      @change="(v: string) => saveOrderField('customs_declaration_method', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="代理操作要求" width="160">
                  <template #default>
                    <el-input :model-value="detail.fields.agent_operation_requirement?.value ?? ''" size="small"
                      @change="(v: string) => saveOrderField('agent_operation_requirement', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="主单英文品名" width="150">
                  <template #default>
                    <el-input :model-value="detail.fields.main_product_english_name?.value ?? ''" size="small"
                      placeholder="CONSOL SHIPMENT"
                      @change="(v: string) => saveOrderField('main_product_english_name', v)" />
                  </template>
                </el-table-column>
                <el-table-column label="主单发货人" width="170">
                  <template #default>{{ 'WEIDA FREIGHT SYSTEM CO., LTD.' }}</template>
                </el-table-column>
                <el-table-column label="主单收货人" width="170">
                  <template #default>{{ display(detail.fields.supplier?.value, 'text') }}</template>
                </el-table-column>
                <el-table-column label="主单通知人" width="170">
                  <template #default>{{ display(detail.fields.main_notice_person?.value, 'text') || 'SAME AS CNEE' }}</template>
                </el-table-column>
                <el-table-column label="分单发货人" width="240" />
                <el-table-column label="分单收货人" width="240" />
                <el-table-column label="分单通知人" width="240" />
                <el-table-column label="分单品名" width="150" />
                <template #empty>
                  <el-empty description="暂无关联记录" />
                </template>
              </el-table>
              <!-- 投保：妙搭是「分单号 / 发票号」两列 + 行展开看 12 项明细，不是平铺大表 -->
              <el-table
                v-else-if="rb.key === 'insurance'"
                v-loading="relatedLoading[rb.key]"
                :data="relatedData[rb.key] || []"
                :row-key="(row: any) => row.id"
                size="small"
                border>
                <el-table-column type="expand" width="44">
                  <template #default="{ row }">
                    <div class="wb-ins-grid">
                      <div v-for="f in INSURANCE_DETAIL" :key="f.column" class="wb-ins-field">
                        <label>{{ f.label }}</label>
                        <div>{{ display(row.fields[f.column]?.value, f.type) }}</div>
                      </div>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="分单号" min-width="140" show-overflow-tooltip>
                  <template #default="{ row }">{{ display(row.fields.house_bl_no?.value, 'text') }}</template>
                </el-table-column>
                <el-table-column label="发票号" min-width="140" show-overflow-tooltip>
                  <template #default="{ row }">{{ display(row.fields.customer_ref_no?.value, 'text') }}</template>
                </el-table-column>
                <template #empty>
                  <el-empty description="暂无关联记录" />
                </template>
              </el-table>
              <el-table
                v-else-if="rb.key !== 'fleet'"
                v-loading="relatedLoading[rb.key]"
                :data="relatedData[rb.key] || []"
                :row-key="(row: any) => row.id"
                size="small"
                border>
                <el-table-column v-if="rb.key === 'ifreight'" type="expand" width="44">
                  <template #default="{ row }">
                    <div class="wb-ifreight-expand">
              <div class="wb-ops-card">
                  <div class="wb-ops-card-top">
                    <div class="wb-ops-copy">
                      <span class="wb-ops-label">复制提单库</span>
                      <el-select
                        size="small"
                        filterable
                        clearable
                        placeholder="选择提单库"
                        style="width: 260px"
                        :model-value="row.fields.copy_bl_no?.value || null"
                        @change="(v: any) => copyBlFromCard(row, v)">
                        <el-option
                          v-for="b in billlibOptions"
                          :key="b.id"
                          :label="`${b.copy_bill_no || ''} · ${b.shipper_name || ''} / ${b.consignee_name || ''}`"
                          :value="b.id" />
                      </el-select>
                    </div>
                    <div class="wb-ops-actions">
                      <el-dropdown trigger="click" @command="(cmd: string) => onDocCommand(row, cmd)">
                        <el-button size="small">生成 ▾</el-button>
                        <template #dropdown>
                          <el-dropdown-menu>
                            <el-dropdown-item command="mawb">主单</el-dropdown-item>
                            <el-dropdown-item command="mawbVerify">客户核对主单</el-dropdown-item>
                            <el-dropdown-item command="hawb">分单</el-dropdown-item>
                            <el-dropdown-item command="hawbVerify">客户核对分单</el-dropdown-item>
                            <el-dropdown-item command="hawbRandom">随机分单</el-dropdown-item>
                            <el-dropdown-item command="manifest">舱单</el-dropdown-item>
                            <el-dropdown-item command="manifestRandom">随机舱单</el-dropdown-item>
                            <el-dropdown-item command="booking">空运booking</el-dropdown-item>
                            <el-dropdown-item command="arrival">到货预报表</el-dropdown-item>
                          </el-dropdown-menu>
                        </template>
                      </el-dropdown>
                      <el-button size="small" @click="saveIfreightCard(row)">保存</el-button>
                      <el-button size="small" type="primary" plain @click="syncRelated(rb, row)">同步 ifreight</el-button>
                    </div>
                  </div>
                  <div class="wb-ops-grid">
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">委托书</span>
                      <el-upload
                      multiple
                        :show-file-list="false"
                        :http-request="(opt: any) => uploadIfreightAttachment(row, '委托书', opt)">
                        <el-button size="small">{{ row.fields['委托书']?.value ? '重新上传' : '上传委托书' }}</el-button>
                      </el-upload>
                    </div>
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">提示词类型</span>
                      <!-- 与飞书查找引用一致：选类型 → 自动带出提示词正文 -->
                      <el-select
                        v-model="ifreightCard[row.id].prompt_type"
                        size="small"
                        filterable
                        clearable
                        allow-create
                        placeholder="选择提示词类型"
                        @change="(v: string) => applyPromptTemplate(row, v)">
                        <el-option
                          v-for="tpl in promptTemplates"
                          :key="tpl.type_name + (tpl.customer || '')"
                          :label="tpl.customer ? `${tpl.type_name}（客户专用）` : tpl.type_name"
                          :value="tpl.type_name" />
                      </el-select>
                    </div>
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">提示词</span>
                      <el-input v-model="ifreightCard[row.id].prompt" size="small" />
                    </div>
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">托书识别</span>
                      <el-button size="small" @click="triggerInstruction(rb, row)">识别托书</el-button>
                    </div>
                    <div class="wb-ops-field wb-ops-wide">
                      <span class="wb-ops-label">AI 识别结果</span>
                      <el-input
                        type="textarea"
                        :rows="2"
                        readonly
                        :model-value="row.fields.ai_recognize_result?.value || ''" />
                    </div>
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">同步状态</span>
                      <span>{{ row.fields.sync_status?.value || '未同步' }}</span>
                    </div>
                    <div class="wb-ops-field">
                      <span class="wb-ops-label">同步时间</span>
                      <span>{{ display(row.fields.sync_time?.value, 'datetime') || '—' }}</span>
                    </div>
                    <div class="wb-ops-field wb-ops-wide">
                      <span class="wb-ops-label">失败原因</span>
                      <span>{{ row.fields.fail_reason?.value || '—' }}</span>
                    </div>
                  </div>
              </div>
                      <div class="wb-ifreight-detail">
                        <div
                          v-for="(line, li) in ifreightDetailRows"
                          :key="li"
                          class="wb-ifreight-detail-row"
                          :style="{ '--cols': line.length }">
                          <div v-for="f in line" :key="f.column" class="wb-ifreight-detail-field">
                            <span>{{ f.label }}</span>
                            <div v-if="f.column === 'dimension'" class="wb-size-edit">
                              <el-input
                                type="textarea"
                                :rows="2"
                                size="small"
                                :model-value="row.fields.dimension?.value ?? ''"
                                @change="(v: string) => saveRelatedField(rb, row, 'dimension', v)" />
                              <el-button size="small" @click="calcIfreightVolume(rb, row)">计算</el-button>
                            </div>
                            <el-date-picker
                              v-else-if="f.column === 'estimated_arrival_time'"
                              :model-value="String(row.fields[f.column]?.value ?? '').slice(0, 10)"
                              type="date"
                              value-format="YYYY-MM-DD"
                              size="small"
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, 'estimated_arrival_time', v)" />
                            <el-date-picker
                              v-else-if="f.column === 'estimated_departure_time'"
                              :model-value="String(row.fields[f.column]?.value ?? '').replace('T', ' ').slice(0, 19)"
                              type="datetime"
                              value-format="YYYY-MM-DD HH:mm:ss"
                              size="small"
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, 'estimated_departure_time', v)" />
                            <el-input
                              v-else-if="f.column === 'master_no'"
                              size="small"
                              :model-value="row.fields.master_no?.value ?? ''"
                              @change="(v: string) => saveMasterNo(rb, row, v)" />
                            <div
                              v-else-if="['sync_status','sync_time','fail_reason','ai_recognize_result'].includes(f.column)">
                              {{ display(row.fields[f.column]?.value, 'text') || '—' }}
                            </div>
                            <el-select
                              v-else-if="IFREIGHT_PERSON_COLUMNS.has(f.column)"
                              :model-value="row.fields[f.column]?.value ?? ''"
                              size="small"
                              filterable
                              clearable
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, f.column, v)">
                              <el-option v-for="p in staffOptions" :key="p.id" :label="p.name" :value="p.name" />
                            </el-select>
                            <el-select
                              v-else-if="IFREIGHT_VENDOR_COLUMNS.has(f.column)"
                              :model-value="row.fields[f.column]?.value ?? ''"
                              size="small"
                              filterable
                              clearable
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, f.column, v)">
                              <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
                            </el-select>
                            <el-select
                              v-else-if="IFREIGHT_CITY_COLUMNS.has(f.column)"
                              :model-value="row.fields[f.column]?.value ?? ''"
                              size="small"
                              filterable
                              remote
                              :remote-method="searchPortOptions"
                              :loading="portOptionsLoading"
                              clearable
                              placeholder="三字代码"
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, f.column, v)">
                              <el-option
                                v-for="p in portOptions"
                                :key="`${p.code}-${p.name}`"
                                :label="`${p.code} ${p.name}`"
                                :value="p.code" />
                            </el-select>
                            <el-select
                              v-else-if="IFREIGHT_OPTION_COLUMNS.includes(f.column as any)"
                              :model-value="row.fields[f.column]?.value ?? ''"
                              size="small"
                              filterable
                              allow-create
                              clearable
                              style="width: 100%"
                              @change="(v: string) => saveRelatedField(rb, row, f.column, v)">
                              <el-option
                                v-for="o in ifreightOptionsFor(f.column)"
                                :key="o"
                                :label="o"
                                :value="o" />
                            </el-select>
                            <textarea
                              v-else-if="IFREIGHT_MULTILINE.has(f.column)"
                              class="wb-native-input wb-native-area"
                              rows="2"
                              :value="row.fields[f.column]?.value ?? ''"
                              @change="(e: any) => saveRelatedField(rb, row, f.column, e.target.value)" />
                            <input
                              v-else
                              class="wb-native-input"
                              :value="row.fields[f.column]?.value ?? ''"
                              @change="(e: any) => saveRelatedField(rb, row, f.column, e.target.value)" />
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column
                  v-for="c in rb.columns"
                  :key="c.column"
                  :label="c.label"
                  min-width="120">
                  <template #default="{ row }">
                    <el-input
                      v-if="c.column === 'master_no'"
                      :model-value="row.fields.master_no?.value ?? ''"
                      size="small"
                      @change="(v: string) => saveMasterNo(rb, row, v)" />
                    <input
                      v-else-if="relatedColumnEditable(rb, c.column)"
                      class="wb-native-input"
                      :value="row.fields[c.column]?.value ?? ''"
                      @change="(e: any) => saveRelatedField(rb, row, c.column, e.target.value)" />
                    <span v-else class="wb-order-readonly">
                      {{ display(row.fields[c.column]?.value, c.type) }}
                    </span>
                  </template>
                </el-table-column>


                <template #empty>
                  <el-empty description="暂无关联记录" />
                </template>
              </el-table>
            </a-tab-pane>
          </a-tabs>
          <el-form v-else label-position="top" class="wb-form">
            <el-form-item v-for="f in detailFields" :key="f.column" :label="f.label">
              <WeidaFieldEditor
                v-model="editModel[f.column]"
                :field="f"
                :person-options="staffOptions"
                :readonly-value="detail.fields[f.column]?.value" />
            </el-form-item>
          </el-form>
        </div>
        <template #footer>
          <el-button v-if="drawerMode === 'edit'" type="danger" plain @click="deleteDetail">删除</el-button>
          <el-button @click="beforeCloseDetail(() => (drawerOpen = false))">关闭</el-button>
          <el-button
            v-if="drawerMode === 'create'"
            type="primary"
            :loading="saving"
            :disabled="!canCreate"
            @click="saveDetail">
            新建
          </el-button>
          <el-button v-else type="primary" :loading="saving" @click="saveDetail">保存</el-button>
        </template>
      </a-drawer>
    </template>

    <el-dialog v-model="alertDialogOpen" :title="`这票的提醒（${activeAlerts(alertDialogRecord).length}）`" width="480px">
      <div class="wb-alert-panel">
        <div class="wb-alert-hint">点「取消提醒」只关掉这一条，数据再变会重新提醒；能当场补的可以直接执行。</div>
        <div
          v-for="a in activeAlerts(alertDialogRecord)"
          :key="a.title + a.detail"
          class="wb-alert-item"
          :class="a.severity">
          <div class="wb-alert-head">
            <span class="wb-alert-title">{{ a.title }}</span>
            <span class="wb-alert-time">{{ alertTimeText(a) }}</span>
          </div>
          <div class="wb-alert-detail">{{ a.detail }}</div>
          <div class="wb-alert-actions">
            <span class="wb-alert-badge" :class="a.severity">{{ alertBadgeText(a) }}</span>
            <el-button
              v-if="a.action"
              size="small"
              type="primary"
              @click.stop="runAlertAction(alertDialogRecord, a)">
              {{ a.action.label }}
            </el-button>
            <el-button
              v-if="activeAlerts(alertDialogRecord).some((x: any) => x.title === '客人未确认账单') && a.title === '客人未确认账单'"
              size="small"
              @click.stop="markBillConfirmed(alertDialogRecord)">
              标记已确认
            </el-button>
            <el-button size="small" text @click.stop="dismissAlertFromDialog(a)">取消提醒</el-button>
          </div>
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="weekendOpen" title="周末/节假日进仓确认" width="440px">
      <el-checkbox-group v-model="weekendChecked" class="wb-weekend-checks">
        <el-checkbox label="insured">已投保</el-checkbox>
        <el-checkbox label="declDocs">报关资料齐全</el-checkbox>
        <el-checkbox label="declChecked">报关已核对</el-checkbox>
        <el-checkbox label="clearDocs">清关资料齐全</el-checkbox>
        <el-checkbox label="customerContact">已联系客户</el-checkbox>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="weekendOpen = false">取消</el-button>
        <el-button type="primary" @click="saveWeekendCheck">保存确认</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pwdOpen" title="修改密码" width="420px">
      <el-form label-position="top">
        <el-form-item label="原密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwdForm.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdOpen = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="changeMyPassword">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="handoverOpen" title="周末交接" width="420px">
      <el-select v-model="handoverTo" filterable placeholder="交接给谁" style="width: 100%">
        <el-option v-for="s in staffOptions" :key="s.id" :label="s.name" :value="s.name" />
      </el-select>
      <template #footer>
        <el-button @click="handoverOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!handoverTo" @click="saveHandover">交接</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="feeAddOpen" title="添加额外费用明细" width="520px">
      <el-form label-position="top">
        <el-form-item v-if="(relatedData.ifreight || []).length > 1" label="分单号">
          <el-select v-model="feeAdd.ifreight_id" style="width: 100%" placeholder="选择 iFreight 分单">
            <el-option
              v-for="r in relatedData.ifreight"
              :key="r.id"
              :label="r.fields.house_bl_no?.value || `#${r.id}`"
              :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="国内/国外">
          <el-select v-model="feeAdd.domestic_abroad" style="width: 100%">
            <el-option label="国内" value="国内" />
            <el-option label="国外" value="国外" />
          </el-select>
        </el-form-item>
        <el-form-item label="费用类别">
          <el-input v-model="feeAdd.fee_category" placeholder="如 保费、包装费" />
        </el-form-item>
        <el-form-item label="币种">
          <el-select v-model="feeAdd.currency" style="width: 100%">
            <el-option label="CNY" value="CNY" />
            <el-option label="USD" value="USD" />
            <el-option label="EUR" value="EUR" />
          </el-select>
        </el-form-item>
        <el-form-item label="计费单位">
          <el-input v-model="feeAdd.billing_unit" placeholder="如 PER SHIPMENT" />
        </el-form-item>
        <el-form-item label="数量">
          <el-input v-model="feeAdd.app_quantity" />
        </el-form-item>
        <el-form-item label="单价">
          <el-input v-model="feeAdd.unit_price" />
        </el-form-item>
        <el-form-item label="税点">
          <el-input v-model="feeAdd.tax_point" />
        </el-form-item>
        <el-form-item label="计税类型">
          <el-input v-model="feeAdd.tax_calc_type" placeholder="如 内含 / 外加" />
        </el-form-item>
        <el-form-item label="费用录入情况">
          <el-input v-model="feeAdd.fee_entry_status" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="feeAddOpen = false">取消</el-button>
        <el-button type="primary" @click="addExtraFeeManual">添加</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="billlibOpen" title="复制提单库" width="560px">
      <el-select v-model="billlibValue" filterable placeholder="选择一张提单" style="width: 100%">
        <el-option
          v-for="b in billlibOptions"
          :key="b.id"
          :label="`${b.copy_bill_no || ''} · ${b.shipper_name || ''} / ${b.consignee_name || ''}`"
          :value="b.id" />
      </el-select>
      <template #footer>
        <el-button @click="billlibOpen = false">取消</el-button>
        <el-button type="primary" :disabled="!billlibValue" @click="confirmCopyBl">带入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dashDetailOpen" :title="dashDetailTitle" width="720px">
      <el-table :data="dashDetailRows" size="small" border max-height="520">
        <el-table-column prop="title" label="编号" width="160" />
        <el-table-column prop="subtitle" label="客户 / 货物" min-width="240" />
        <el-table-column prop="status" label="状态" width="120" />
      </el-table>
      <el-empty v-if="!dashDetailRows.length" description="这一档没有明细" />
    </el-dialog>

    <el-dialog v-model="notifOpen" title="通知" width="620px">
      <div v-if="notifications.length" class="wb-notify-list">
        <div v-for="n in notifications" :key="n.id" class="wb-notify-item" :class="{ unread: !n.read_at }">
          <div class="wb-notify-title">{{ n.title }}</div>
          <div class="wb-notify-body">{{ n.body }}</div>
          <div class="wb-notify-time">{{ n.create_time }}</div>
        </div>
      </div>
      <el-empty v-else description="暂无通知" />
    </el-dialog>

    <el-dialog v-model="goodsOpen" title="编辑货物数据" width="760px">
      <div v-if="goodsRows.length" class="wb-goods-edit-list">
        <div v-for="g in goodsRows" :key="g.id" class="wb-goods-edit-item">
          <div class="wb-goods-edit-title">
            第 {{ goodsRows.indexOf(g) + 1 }} 条
            <span v-if="g.hawb">{{ g.hawb }}</span>
          </div>
          <div class="wb-goods-edit-grid">
            <label>件数<el-input v-model="g.quantity" size="small" /></label>
            <label>毛重<el-input v-model="g.gross_weight" size="small" /></label>
            <label>体积<el-input v-model="g.volume" size="small" /></label>
            <label class="wide">尺寸<el-input v-model="g.dimension" type="textarea" :rows="2" size="small" /></label>
          </div>
        </div>
      </div>
      <el-empty v-else description="这条订单还没有 iFreight 分单数据" />
      <template #footer>
        <el-button @click="goodsOpen = false">取消</el-button>
        <el-button type="primary" :loading="goodsBusy" :disabled="!goodsRows.length" @click="saveGoodsEditor">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="approvalOpen" title="对客方案审批" width="720px">
      <div class="wb-approval-list">
        <div v-for="a in approvals" :key="a.id" class="wb-approval-item">
          <div class="wb-approval-head">
            <el-tag :type="a.status === '已通过' ? 'success' : a.status === '已驳回' ? 'danger' : 'warning'">
              {{ a.status }}
            </el-tag>
            <span>#{{ a.id }} · {{ a.applicant_name || '—' }} 发起</span>
            <span v-if="a.decide_time">{{ a.decide_time }}</span>
          </div>
          <div v-if="a.comment" class="wb-approval-comment">意见：{{ a.comment }}</div>
          <div v-if="a.status === '审批中'" class="wb-approval-actions">
            <el-input v-model="approvalComment" size="small" placeholder="审批意见（可选）" style="width: 260px" />
            <el-button type="success" size="small" :loading="approvalBusy" @click="decideApproval(a, true)">通过</el-button>
            <el-button type="danger" size="small" :loading="approvalBusy" @click="decideApproval(a, false)">驳回</el-button>
          </div>
        </div>
        <el-empty v-if="!approvals.length" description="还没有审批记录" />
      </div>
      <template #footer>
        <el-button @click="approvalOpen = false">关闭</el-button>
        <el-button type="primary" :loading="approvalBusy" @click="requestApproval">发起审批</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="feeOpen" title="添加费用明细" width="820px" :before-close="beforeCloseFee">
      <div class="wb-fee-plan">方案：{{ feePlanLabel }}</div>
      <el-table :data="fees" size="small" border max-height="320">
        <el-table-column label="费用类别" width="140">
          <template #default="{ row }">
            <el-select
              v-if="isFeeEditing(row, 'fee_category')"
              v-model="feeEditValue"
              size="small"
              filterable
              default-first-option
              clearable
              @change="saveFeeEdit(row)">
              <el-option v-for="v in feeOptions.fee_categories" :key="v" :label="v" :value="v" />
            </el-select>
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'fee_category')">
              {{ display(blockValue(row, 'fee_category'), 'text') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="120">
          <template #default="{ row }">
            <el-select
              v-if="isFeeEditing(row, 'group')"
              v-model="feeEditValue"
              size="small"
              filterable
              default-first-option
              clearable
              @change="saveFeeEdit(row)">
              <el-option v-for="v in feeOptions.groups" :key="v" :label="v" :value="v" />
            </el-select>
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'group')">
              {{ display(blockValue(row, 'group'), 'text') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="计费单位" width="110">
          <template #default="{ row }">
            <el-select
              v-if="isFeeEditing(row, 'billing_unit')"
              v-model="feeEditValue"
              size="small"
              filterable
              default-first-option
              clearable
              @change="saveFeeEdit(row)">
              <el-option v-for="v in feeOptions.billing_units" :key="v" :label="v" :value="v" />
            </el-select>
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'billing_unit')">
              {{ display(blockValue(row, 'billing_unit'), 'text') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="币种" width="90">
          <template #default="{ row }">
            <el-select
              v-if="isFeeEditing(row, 'currency')"
              v-model="feeEditValue"
              size="small"
              filterable
              default-first-option
              clearable
              @change="saveFeeEdit(row)">
              <el-option v-for="v in feeOptions.currencies" :key="v" :label="v" :value="v" />
            </el-select>
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'currency')">
              {{ display(blockValue(row, 'currency'), 'text') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="100">
          <template #default="{ row }">
            <el-input
              v-if="isFeeEditing(row, 'amount')"
              v-model="feeEditValue"
              size="small"
              @keyup.enter="saveFeeEdit(row)"
              @blur="saveFeeEdit(row)" />
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'amount')">
              {{ display(blockValue(row, 'amount'), 'number') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="150">
          <template #default="{ row }">
            <el-input
              v-if="isFeeEditing(row, 'remark')"
              v-model="feeEditValue"
              size="small"
              @keyup.enter="saveFeeEdit(row)"
              @blur="saveFeeEdit(row)" />
            <span v-else class="wb-fee-cell" @dblclick="startFeeEdit(row, 'remark')">
              {{ display(blockValue(row, 'remark'), 'text') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeFee(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有费用明细" />
        </template>
      </el-table>
      <div class="wb-fee-form">
        <el-select
          v-model="feeForm.fee_category"
          placeholder="费用类别"
          filterable
          default-first-option
          clearable>
          <el-option v-for="v in feeOptions.fee_categories" :key="v" :label="v" :value="v" />
        </el-select>
        <el-select v-model="feeForm.group" placeholder="分组" filterable allow-create default-first-option clearable>
          <el-option v-for="v in feeOptions.groups" :key="v" :label="v" :value="v" />
        </el-select>
        <el-select
          v-model="feeForm.billing_unit"
          placeholder="计费单位"
          filterable
          default-first-option
          clearable>
          <el-option v-for="v in feeOptions.billing_units" :key="v" :label="v" :value="v" />
        </el-select>
        <el-select v-model="feeForm.currency" placeholder="币种" filterable allow-create default-first-option clearable>
          <el-option v-for="v in feeOptions.currencies" :key="v" :label="v" :value="v" />
        </el-select>
        <el-input v-model="feeForm.amount" placeholder="金额" />
        <el-input v-model="feeForm.remark" placeholder="备注" />
        <el-button type="primary" @click="addFee">添加</el-button>
      </div>
    </el-dialog>

    <el-dialog v-model="textColorOpen" title="字色（整列）" width="460px">
      <div class="wb-textcolor">
        <el-select v-model="textColorColumn" placeholder="选择列" filterable style="width: 220px">
          <el-option v-for="c in displayColumns.filter((x) => x.kind === 'field')" :key="c.key" :label="c.label" :value="(c as any).column" />
        </el-select>
        <el-color-picker v-model="textColorValue" />
        <el-button type="primary" @click="applyTextColor">应用</el-button>
        <el-button @click="clearTextColor">清除</el-button>
      </div>
      <p class="wb-col-tip">只改这一列的文字颜色，并按账号保存。</p>
    </el-dialog>

    <el-dialog v-model="columnOpen" title="自定义列" width="680px">
      <p class="wb-col-tip">只影响你自己的界面，配置保存在本机；同事看到的不变。</p>
      <div class="wb-col-row">
        <span class="wb-col-index">行高</span>
        <span class="wb-col-name">固定行高（调大后裁切内容会露出）</span>
        <el-input-number v-model="rowHeight" :min="30" :max="240" :step="2" size="small" @change="saveRowHeight" />
      </div>
      <div class="wb-col-list">
        <div
          v-for="(c, i) in displayColumns"
          :key="c.key"
          class="wb-col-row"
          draggable="true"
          @dragstart="startColumnDrag($event, c.key)"
          @dragover.prevent
          @drop="dropColumn($event, c.key)">
          <span class="wb-col-index">{{ i + 1 }}</span>
          <span class="wb-col-name">{{ c.label }}</span>
          <el-input-number
            size="small"
            :min="40"
            :max="900"
            :step="10"
            :value="columnWidths[c.key] || columnWidth(c)"
            style="width: 96px"
            @change="(v: number | undefined) => setColumnWidth(c.key, v)" />
          <el-tag size="small" type="info">{{ c.kind === 'field' ? c.type : c.kind === 'template' ? '拼接' : '本地列' }}</el-tag>
          <el-button v-if="c.kind === 'template'" link @click="editTemplate(c.key)">编辑</el-button>
          <!-- 只有国内操作台支持冻结列（妙搭 freezable:true 只开在那里） -->
          <el-checkbox
            v-if="role === 'domestic_ops'"
            :model-value="frozenColumns.includes(c.key)"
            @change="toggleFrozen(c.key)">冻结</el-checkbox>
          <el-button link :disabled="i === 0" @click="moveColumn(i, -1)">上移</el-button>
          <el-button link :disabled="i === displayColumns.length - 1" @click="moveColumn(i, 1)">下移</el-button>
          <el-button link type="danger" @click="removeColumn(i)">删除</el-button>
        </div>
        <p v-if="!displayColumns.length" class="wb-col-empty">还没有列，从下面添加。</p>
      </div>
      <div class="wb-col-add">
        <el-select v-model="addColumn" filterable clearable placeholder="搜索并添加字段" class="wb-col-picker">
          <el-option v-for="c in addableColumns" :key="c.column" :label="c.label" :value="c.column" />
        </el-select>
        <el-button :disabled="!addColumn" @click="appendColumn">添加</el-button>
      </div>
      <div class="wb-col-add">
        <el-input v-model="tplLabel" placeholder="拼接列名，如 货名+港口" class="wb-tpl-label" />
        <el-input
          v-model="tplExpr"
          type="textarea"
          :rows="2"
          placeholder="表达式，如 {货物品名} - {目的 港}"
          class="wb-tpl-expr" />
        <el-button :disabled="!tplLabel || !tplExpr" @click="addTemplate">
          {{ editingTemplateKey ? '保存拼接列' : '添加拼接列' }}
        </el-button>
        <el-button v-if="editingTemplateKey" @click="cancelEditTemplate">取消编辑</el-button>
      </div>
      <div class="wb-col-add">
        <el-select
          v-model="templateFieldPick"
          filterable
          clearable
          placeholder="搜索字段，选中后插入拼接表达式"
          class="wb-col-picker"
          @change="insertTemplateField">
          <el-option v-for="c in availableColumns" :key="c.column" :label="c.label" :value="c.label" />
        </el-select>
        <el-button @click="insertTemplateSeparator(' / ')">插入 /</el-button>
        <el-button @click="insertTemplateSeparator(' - ')">插入 -</el-button>
        <el-button @click="insertTemplateSeparator('、')">插入 、</el-button>
        <el-button @click="insertTemplateSeparator('\n')">插入换行</el-button>
      </div>
      <p class="wb-col-tip">
        拼接列用
        <code>{字段名}</code>
        引用字段，可写多个，例如
        <code>{货物品名} - {目的 港}</code>
        。本地列只存在本机，可当个人备注。
      </p>
      <div class="wb-col-add">
        <el-input v-model="blankLabel" placeholder="本地列名，如 我今天要跟的" class="wb-tpl-label" />
        <el-button :disabled="!blankLabel.trim()" @click="addBlank">添加本地列</el-button>
      </div>
      <template #footer>
        <el-button @click="resetColumns">恢复默认</el-button>
        <el-button @click="columnOpen = false">取消</el-button>
        <el-button @click="importInput?.click()">导入妙搭配置</el-button>
        <input ref="importInput" type="file" accept=".json,application/json" style="display: none" @change="importMiaodaConfig" />
        <el-button type="primary" @click="saveColumns">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="paintOpen" title="填色 / 字色（条件格式）" width="920px">
      <p class="wb-col-tip">
        满足条件自动上色，只影响你自己的界面。规则自上而下先命中先着色；整列不看条件，整列都涂。
      </p>
      <div class="wb-paint-list">
        <div v-for="(r, i) in paintDraft" :key="r.id" class="wb-paint-row">
          <el-radio-group v-model="r.scope" size="small">
            <el-radio-button label="cell">单元格</el-radio-button>
            <el-radio-button label="row">整行</el-radio-button>
            <el-radio-button label="column">整列</el-radio-button>
          </el-radio-group>
          <el-select v-model="r.column" filterable placeholder="列" class="wb-filter-col">
            <el-option v-for="c in displayColumns" :key="c.key" :label="c.label" :value="c.key" />
          </el-select>
          <template v-if="r.scope !== 'column'">
            <el-select v-model="r.op" class="wb-op">
              <el-option v-for="op in paintOps(r.column)" :key="op.value" :label="op.label" :value="op.value" />
            </el-select>
            <el-date-picker
              v-if="r.op === 'date_on' || r.op === 'date_before' || r.op === 'date_after'"
              v-model="r.value"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="日期"
              class="wb-filter-val" />
            <el-input
              v-else-if="!isValueless(r.op)"
              v-model="r.value"
              placeholder="值"
              class="wb-filter-val" />
          </template>
          <span v-else class="cell-empty">整列上色</span>
          <el-color-picker v-model="r.color" size="small" title="底色" />
          <el-input v-model="r.color" placeholder="底色 #hex / rgb()" style="width: 150px" />
          <div class="wb-color-presets">
            <button
              v-for="c in PAINT_PRESETS"
              :key="c"
              type="button"
              class="wb-color-dot"
              :style="{ backgroundColor: c }"
              :title="c"
              @click="r.color = c" />
          </div>
          <el-color-picker v-model="r.textColor" size="small" title="字色" />
          <el-input v-model="r.textColor" placeholder="字色 #hex / rgb()" style="width: 150px" />
          <el-checkbox v-model="r.bold">B</el-checkbox>
          <el-checkbox v-model="r.italic">I</el-checkbox>
          <el-select v-model="r.fontSize" placeholder="字号" style="width: 78px" clearable>
            <el-option label="12" :value="12" />
            <el-option label="14" :value="14" />
            <el-option label="16" :value="16" />
          </el-select>
          <el-button link :disabled="i === 0" @click="movePaint(i, -1)">上移</el-button>
          <el-button link :disabled="i === paintDraft.length - 1" @click="movePaint(i, 1)">下移</el-button>
          <el-button link type="danger" @click="paintDraft.splice(i, 1)">删除</el-button>
        </div>
        <p v-if="!paintDraft.length" class="wb-col-empty">还没有规则。</p>
      </div>
      <el-button @click="addPaintRule">+ 加一条</el-button>
      <template #footer>
        <el-button @click="paintDraft = []">清空全部</el-button>
        <el-button @click="paintOpen = false">取消</el-button>
        <el-button type="primary" @click="savePaintRules">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bindOpen" title="人员与账号" width="860px">
      <div class="wb-staff-head">
        <span class="wb-col-tip">维护人员姓名、角色、手机和登录账号；开通后即可用该账号登录。</span>
        <el-button type="primary" size="small" @click="openStaffEdit()">新增人员</el-button>
      </div>
      <el-table :data="bindItems" size="small" max-height="480">
        <el-table-column prop="name" label="姓名" width="120" />
        <el-table-column label="身份" min-width="180">
          <template #default="{ row }">{{ roleText(row) || '—' }}</template>
        </el-table-column>
        <el-table-column label="账号" min-width="150">
          <template #default="{ row }">
            <span v-if="row.username">{{ row.username }}</span>
            <span v-else class="cell-empty">未开通</span>
          </template>
        </el-table-column>
        <el-table-column prop="mobile" label="手机" width="120" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'info'" size="small">
              {{ row.active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="{ row }">
            <el-button v-if="!row.local_user_id" link type="primary" @click="openAccount(row)">开通账号</el-button>
            <el-button v-else link type="primary" @click="openReset(row)">重置密码</el-button>
            <el-button link @click="openStaffEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="bindOpen = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="staffAccountOpen"
      :title="
        staffAccountMode === 'create'
          ? `开通账号 - ${staffAccountTarget?.name || ''}`
          : `重置密码 - ${staffAccountTarget?.name || ''}`
      "
      width="460px">
      <el-form label-position="top">
        <el-form-item v-if="staffAccountMode === 'create'" label="登录账号" required>
          <el-input v-model="staffAccountForm.username" placeholder="至少 3 个字符" />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input v-model="staffAccountForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="beforeCloseStaffAccount(() => (staffAccountOpen = false))">取消</el-button>
        <el-button type="primary" :loading="staffSaving" @click="submitStaffAccount">
          {{ staffAccountMode === 'create' ? '开通' : '重置' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="staffEditOpen"
      :title="staffEditForm.id ? '编辑人员' : '新增人员'"
      width="520px"
      :before-close="beforeCloseStaffEdit">
      <el-form label-position="top">
        <el-form-item label="姓名" required>
          <el-input v-model="staffEditForm.name" placeholder="人员姓名" />
        </el-form-item>
        <el-form-item label="身份（可多选）" required>
          <el-select v-model="staffEditForm.roles" multiple style="width: 100%">
            <el-option v-for="o in ROLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="staffEditForm.mobile" placeholder="选填" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="staffEditForm.email" placeholder="选填" />
        </el-form-item>
        <el-form-item v-if="staffEditForm.id" label="状态">
          <el-switch v-model="staffEditForm.active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="beforeCloseStaffEdit(() => (staffEditOpen = false))">取消</el-button>
        <el-button type="primary" :loading="staffSaving" @click="saveStaff">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="quoteDialogOpen"
      :title="quoteEditId ? '修改航线报价' : '新增航线报价'"
      width="760px">
      <el-form label-position="top">
        <div class="wb-quote-grid">
          <el-form-item v-for="f in QUOTE_FIELDS" :key="f.key" :label="f.label">
            <el-select
              v-if="f.kind === 'supplier'"
              v-model="quoteForm[f.key]"
              filterable
              clearable
              placeholder="选择供应商"
              style="width: 100%">
              <el-option v-for="v in vendorOptions" :key="v.id" :label="v.name" :value="v.name" />
            </el-select>
            <el-select
              v-else-if="f.kind === 'port'"
              v-model="quoteForm[f.key]"
              filterable
              remote
              :remote-method="searchQuotePorts"
              clearable
              placeholder="输入代码或城市搜索"
              style="width: 100%">
              <el-option
                v-for="p in quotePortOptions"
                :key="p.code"
                :label="`${p.code} ${p.name}`"
                :value="p.code" />
            </el-select>
            <el-select
              v-else-if="f.kind === 'ports'"
              v-model="quoteForm[f.key]"
              multiple
              collapse-tags
              collapse-tags-tooltip
              filterable
              clearable
              placeholder="可多选中转点"
              style="width: 100%">
              <el-option
                v-for="p in quotePortOptions"
                :key="p.code"
                :label="`${p.code} ${p.name}`"
                :value="p.code" />
            </el-select>
            <el-select
              v-else-if="f.kind === 'currency'"
              v-model="quoteForm[f.key]"
              clearable
              placeholder="选择币种"
              style="width: 100%">
              <el-option label="CNY" value="CNY" />
              <el-option label="USD" value="USD" />
              <el-option label="EUR" value="EUR" />
            </el-select>
            <el-date-picker
              v-else-if="f.kind === 'date'"
              v-model="quoteForm[f.key]"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择日期"
              style="width: 100%" />
            <el-input
              v-else
              v-model="quoteForm[f.key]"
              :type="f.kind === 'textarea' ? 'textarea' : 'text'"
              :rows="f.kind === 'textarea' ? 2 : undefined" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="quoteDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="quoteSaving" @click="saveQuote">保存</el-button>
      </template>
    </el-dialog>


    <el-drawer v-model="filterOpen" title="筛选条件" size="560px">
      <div class="wb-filter-conj">
        <span>满足</span>
        <el-radio-group v-model="draftConjunction">
          <el-radio-button label="and">全部（且）</el-radio-button>
          <el-radio-button label="or">任一（或）</el-radio-button>
        </el-radio-group>
        <span>条件</span>
      </div>
      <div class="wb-filter-list">
        <div v-for="(c, i) in draftConditions" :key="i" class="wb-filter-row">
          <el-select v-model="c.column" placeholder="字段" filterable class="wb-filter-col">
            <el-option v-for="col in filterableColumns" :key="col.column" :label="col.label" :value="col.column" />
          </el-select>
          <el-select v-model="c.op" class="wb-op">
            <el-option v-for="op in opsFor(c.column)" :key="op.value" :label="op.label" :value="op.value" />
          </el-select>
          <el-date-picker
            v-if="c.op === 'date_on' || c.op === 'date_before' || c.op === 'date_after'"
            v-model="c.value"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            class="wb-filter-val" />
          <div v-else-if="c.op === 'date_between'" class="wb-filter-range">
            <el-date-picker v-model="c.value" type="date" value-format="YYYY-MM-DD" placeholder="开始" />
            <el-date-picker v-model="c.value2" type="date" value-format="YYYY-MM-DD" placeholder="结束" />
          </div>
          <el-select
            v-else-if="PERSON_FILTER_COLUMNS.has(c.column) && !isValueless(c.op)"
            v-model="c.value"
            filterable
            clearable
            placeholder="选择人员"
            class="wb-filter-val">
            <el-option
              v-for="p in staffOptions"
              :key="p.id"
              :label="p.name"
              :value="p.name" />
          </el-select>
          <el-select
            v-else-if="fieldType(c.column) === 'checkbox' && c.op === 'eq'"
            v-model="c.value"
            class="wb-filter-val">
            <el-option label="是" :value="1" />
            <el-option label="否" :value="0" />
          </el-select>
          <el-input v-else-if="!isValueless(c.op)" v-model="c.value" placeholder="值" class="wb-filter-val" />
          <el-button link type="danger" @click="draftConditions.splice(i, 1)">删除</el-button>
        </div>
        <el-button @click="addCondition">+ 添加条件</el-button>
      </div>
      <div class="wb-filter-save">
        <el-input v-model="viewName" placeholder="视图名称，如「我的待报价」" clearable />
        <el-button :disabled="!viewName.trim() || !draftConditions.length" @click="saveView">存为视图</el-button>
      </div>
      <template #footer>
        <el-button @click="resetFilters">清空</el-button>
        <el-button @click="filterOpen = false">取消</el-button>
        <el-button type="primary" @click="applyFilters">应用</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useSettingsStore } from '@stores/modules/setting.store'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@stores/modules/user.store'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import request from '@/utils/http'
import { Auth } from '@/utils/auth'
import WeidaFieldEditor from './components/FieldEditor.vue'
import MasterDataPanel from './components/MasterDataPanel.vue'
import ECharts from '@/components/ECharts/index.vue'

defineOptions({ name: 'WeidaWorkbench' })

type Col = {
  column: string
  label: string
  type: string
  editable: boolean
  virtual?: boolean
  options?: string[]
}
type TemplateColumn = { id: string; label: string; expression: string }
type BlankColumn = { id: string; label: string }
type DisplayCol =
  | { key: string; label: string; kind: 'field'; column: string; type: string; virtual?: boolean; options?: string[] }
  | { key: string; label: string; kind: 'template'; expression: string }
  | { key: string; label: string; kind: 'blank' }
type FieldValue = { label: string; value: any; step?: number | null }
type Row = {
  id: number
  is_new?: boolean
  fleet?: { count?: string; weight?: string; size?: string; volume?: number | null; ratio?: string | null; arrived?: boolean }
  fields: Record<string, FieldValue>
  handover?: { to_name?: string; end_at?: string; can_cancel?: boolean }
  weekend_check?: { pending: boolean; items: Record<string, boolean> }
  flight_changed?: boolean
  alerts?: Array<{
    severity: string
    title: string
    detail: string
    action?: { kind: string; table: string; record_id: number; field: string; label: string }
  }>
}
type RelatedRow = {
  id: number
  plan_id?: number | null
  opportunity_id?: number | null
  goods?: {
    piece_count?: number | null
    gross_weight_kg?: number | null
    volume_m3?: number | null
    size?: string | null
  }
  fields: Record<string, FieldValue>
}
type AttachmentItem = {
  id: number
  field: string
  file_name: string
  file_size: number
  mime: string
  create_time: string | null
}
type RelatedBlock = {
  key: string
  label: string
  table: string
  columns: Col[]
  when_field?: string | null
  when_fields?: string[]
  when_contains?: string | null
}
type LinkBlock = {
  key: string
  label: string
  table: string
  columns: Col[]
  footer_fields: Col[]
  expand_fields: Col[]
}
type Config = {
  role: string
  current_staff?: {
    id: number
    name: string
    role: string
    roles?: string[] | string
    suda_user_id?: string | null
  } | null
  name: string
  description: string
  table: string
  columns: Col[]
  editable: string[]
  search_fields: string[]
  detail_fields: Col[]
  detail_sections: Array<{ label: string; fields: Col[] }>
  can_admin?: boolean
  todo_checkboxes?: string[]
  detail_actions?: Array<{ field: string; label: string; done: string; tab: string }>
  commands?: Array<{ key: string; label: string; tab: string }>
  link_blocks?: LinkBlock[]
  related_blocks?: RelatedBlock[]
  no_paging?: boolean
}

const route = useRoute()
const userStore = useUserStore()
const router = useRouter()

const ROLE_MAP: Record<string, string> = {
  'customer-service': 'customer_service',
  pricing: 'pricing',
  'domestic-ops': 'domestic_ops',
  'route-quote': 'route_quote',
  plan: 'plan',
  'master-data': 'master-data',
}
const role = computed(() => {
  const seg = String(route.params.role || route.path.split('/').filter(Boolean).pop() || '')
  return (
    ROLE_MAP[seg] ||
    (['customer_service', 'pricing', 'domestic_ops', 'route_quote', 'plan', 'master-data'].includes(seg)
      ? seg
      : 'customer_service')
  )
})

const DASH_ROLE: Record<string, string> = {
  'dashboard-cs': 'customer_service',
  'dashboard-pricing': 'pricing',
  'dashboard-ops': 'domestic_ops',
}
const isDashboard = computed(() => String(route.path || '').includes('dashboard'))
const dashRole = computed(() => {
  const seg = String(route.path.split('/').filter(Boolean).pop() || '')
  return DASH_ROLE[seg] || role.value
})
type DashSection = {
  key: string
  title: string
  caption: string
  type: string
  data: Array<{ name: string; value: number | null; value2?: number; caption?: string; detail?: string }>
}
type DashChart = {
  key: string
  title: string
  type: 'bar' | 'pie'
  data: Array<{ name: string; value: number; value2?: number; value3?: number }>
}
const dashboard = ref<{
  title: string
  subtitle?: string
  cards: Array<{ label: string; value: number | string }>
  charts: DashChart[]
  sections?: DashSection[]
  summary?: Record<string, any>
  summary2?: Record<string, any>
  reasons?: Array<{ name: string; value: number }>
  operatorTable?: Array<Record<string, any>>
  todayWarehouse?: Array<Record<string, any>>
  todayFlights?: Array<Record<string, any>>
}>({
  title: '',
  cards: [],
  charts: [],
})
const config = ref<Config | null>(null)
const rows = ref<Row[]>([])
const columns = ref<Col[]>([])
const columnOrder = ref<string[]>([])
const frozenColumns = ref<string[]>([])
const templateColumns = ref<TemplateColumn[]>([])
const blankColumns = ref<BlankColumn[]>([])
const blankValues = ref<Record<string, string>>({})
const blankLabel = ref('')
const editingTemplateKey = ref('')
const columnWidths = ref<Record<string, number>>({})
const rowHeight = ref(54)
const ZOOM_OPTIONS = [70, 80, 90, 100, 110, 120, 130, 150]
const tableZoom = ref(100)

// 缩放后表格内部可用高度要换算回「未缩放」的 px，否则缩小只留白、不多显示行
const tableScrollY = computed(
  () => `calc((100vh - 168px) * ${(100 / tableZoom.value).toFixed(4)})`,
)

function saveTableZoom() {
  localStorage.setItem(`weida_zoom:${role.value}`, String(tableZoom.value))
}

function restoreTableZoom() {
  const saved = Number(localStorage.getItem(`weida_zoom:${role.value}`) || 100)
  tableZoom.value = ZOOM_OPTIONS.includes(saved) ? saved : 100
}
const addColumn = ref('')
const bindOpen = ref(false)
const bindItems = ref<Array<Record<string, any>>>([])
const bindUsers = ref<Array<{ id: number; username: string; name: string }>>([])
const staffOptions = ref<Array<{ id: number; name: string; role: string; suda_user_id?: string | null }>>([])
const ROLE_OPTIONS = [
  { value: 'customer_service', label: '国内客服' },
  { value: 'pricing', label: '航线' },
  { value: 'domestic_ops', label: '国内操作' },
  { value: 'admin', label: '管理员' },
  { value: 'super_admin', label: '超级管理员' },
]

function normalizeRoles(row: Record<string, any>): string[] {
  const raw = row?.roles
  if (Array.isArray(raw)) return raw.filter(Boolean)
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.filter(Boolean)
    } catch {
      return raw
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean)
    }
  }
  return row?.role ? [row.role] : []
}

function roleText(row: Record<string, any>): string {
  const roles = normalizeRoles(row)
  return roles.map((r) => ROLE_OPTIONS.find((o) => o.value === r)?.label || r).join('、')
}

const staffAccountOpen = ref(false)
const staffAccountMode = ref<'create' | 'reset'>('create')
const staffAccountTarget = ref<Record<string, any> | null>(null)
const staffAccountForm = reactive({ username: '', password: '' })
const staffEditOpen = ref(false)
const staffSaving = ref(false)
const staffEditForm = reactive({
  id: 0,
  name: '',
  role: '',
  roles: [] as string[],
  mobile: '',
  email: '',
  active: true,
})
const tplLabel = ref('')
const tplExpr = ref('')
const templateFieldPick = ref('')
type PaintRule = {
  id: string
  scope: 'cell' | 'row' | 'column'
  column: string
  op: string
  value: any
  color: string
  textColor?: string
  bold?: boolean
  italic?: boolean
  fontSize?: number
}
const paintOpen = ref(false)
const paintRules = ref<PaintRule[]>([])
const paintDraft = ref<PaintRule[]>([])
type InlineEdit = {
  rowId: number
  key: string
  kind: 'field' | 'template'
  column?: string
  expression?: string
  original: string
  type?: string
  options?: string[]
}
const inlineCalcVolume = ref<number | null>(null)
const inlineEdit = ref<InlineEdit | null>(null)
const inlineEditValue = ref('')
const inlineSaving = ref(false)
const PAINT_PRESETS = [
  '#fde68a',
  '#bbf7d0',
  '#bfdbfe',
  '#a5f3fc',
  '#e9d5ff',
  '#fbcfe8',
  '#fed7aa',
  '#fecaca',
  '#e5e7eb',
]
const STATUS_TINTS: Record<string, string> = {
  未拿到货: 'rgba(210, 168, 24, 0.30)',
  已拿到货: 'rgba(91, 151, 197, 0.26)',
  未发送航线询价: 'rgba(189, 169, 129, 0.11)',
  发送航线询价: 'rgba(189, 169, 129, 0.22)',
  收到航线报价: 'rgba(91, 151, 197, 0.26)',
  完成客户报价: 'rgba(210, 168, 24, 0.30)',
  航线完成核价: 'rgba(199, 108, 90, 0.28)',
  完成代理订舱: 'rgba(134, 93, 170, 0.26)',
  完成操作交接: 'rgba(61, 139, 68, 0.24)',
  未报价: 'rgba(199, 108, 90, 0.25)',
  已报价: 'rgba(91, 151, 197, 0.20)',
  待主管核价: 'rgba(134, 93, 170, 0.26)',
  已完成: 'rgba(61, 139, 68, 0.24)',
  未交接: 'transparent',
  已交接: 'transparent',
}
const columnOpen = ref(false)
const importInput = ref<HTMLInputElement | null>(null)
const sortField = ref('')
const sortDesc = ref<boolean | null>(null)
const availableColumns = computed<Col[]>(() => {
  const map = new Map<string, Col>()
  for (const c of columns.value) map.set(c.column, c)
  for (const c of config.value?.detail_fields || []) {
    if (!map.has(c.column)) map.set(c.column, c)
  }
  return [...map.values()]
})
const columnMap = computed(() => new Map(availableColumns.value.map((c) => [c.column, c])))
const displayColumns = computed<DisplayCol[]>(() =>
  columnOrder.value
    .map((key): DisplayCol | null => {
      if (key.startsWith('tpl:')) {
        const tpl = templateColumns.value.find((t) => t.id === key)
        return tpl ? { key, label: tpl.label, kind: 'template', expression: tpl.expression } : null
      }
      if (key.startsWith('blank:')) {
        const blank = blankColumns.value.find((b) => b.id === key)
        return blank ? { key, label: blank.label, kind: 'blank' } : null
      }
      const c = columnMap.value.get(key)
      return c
        ? {
            key,
            label: c.label,
            kind: 'field',
            column: c.column,
            type: c.type,
            virtual: c.virtual,
            options: c.options,
          }
        : null
    })
    .filter((c): c is DisplayCol => !!c)
)
const labelToColumn = computed(() => {
  const map = new Map<string, string>()
  for (const c of availableColumns.value) {
    map.set(c.label, c.column)
    map.set(c.column, c.column)
  }
  return map
})
function colOf(key: string) {
  return displayColumns.value.find((c) => c.key === key)
}
function colField(key: string): string {
  const c = colOf(key)
  return c && c.kind === 'field' ? c.column : ''
}
function colType(key: string): string {
  const c = colOf(key)
  if (!c || c.kind !== 'field') return 'text'
  // 客服台「货好时间」只到日（用户 2026-11-23）
  if (role.value === 'customer_service' && c.column === 'goods_ready_time') return 'date'
  return c.type
}
function colExpression(key: string): string {
  const c = colOf(key)
  return c && c.kind === 'template' ? c.expression : ''
}

const seenIds = ref<Set<number>>(new Set())

function seenStorageKey(): string {
  return `weida_seen_rows:${role.value}`
}

function loadSeenRows() {
  try {
    const raw = localStorage.getItem(seenStorageKey())
    seenIds.value = new Set(Array.isArray(JSON.parse(raw || '[]')) ? JSON.parse(raw || '[]').map(Number) : [])
  } catch {
    seenIds.value = new Set()
  }
}

function markSeen(id: number) {
  if (!id || seenIds.value.has(id)) return
  seenIds.value.add(id)
  localStorage.setItem(seenStorageKey(), JSON.stringify([...seenIds.value]))
  void request({
    url: '/weida/read-state',
    method: 'post',
    data: { role: role.value, ids: [id] },
  }).catch(() => undefined)
}

function isUnread(record: Row): boolean {
  // 妙搭 ops-baseline：发版那一刻已有的历史单永不加粗
  if (record.is_new === false) return false
  return !seenIds.value.has(Number(record.id))
}

const PREF_LOCAL_KEYS: Record<string, string> = {
  columns: 'weida_columns_v11',
  paint: 'weida_paint_rules',
  blank: 'weida_blank_values',
  views: 'weida_filter_views',
  drawer: 'weida_drawer_width',
  text_colors: 'weida_text_colors',
}

async function loadAccountState() {
  try {
    const [prefRes, readRes] = await Promise.all([
      request<any>({ url: '/weida/preferences', method: 'get', params: { role: role.value } }),
      request<any>({ url: '/weida/read-state', method: 'get', params: { role: role.value } }),
    ])
    const prefs = prefRes.data?.data ?? {}
    for (const [key, localBase] of Object.entries(PREF_LOCAL_KEYS)) {
      if (!(key in prefs) || prefs[key] == null) continue
      localStorage.setItem(
        `${localBase}:${role.value}`,
        typeof prefs[key] === 'string' ? prefs[key] : JSON.stringify(prefs[key])
      )
    }
    const serverIds = (readRes.data?.data?.ids ?? []).map(Number)
    const localIds = [...seenIds.value]
    seenIds.value = new Set([...serverIds, ...localIds])
    const extra = localIds.filter((id) => !serverIds.includes(id))
    if (extra.length) {
      void request({ url: '/weida/read-state', method: 'post', data: { role: role.value, ids: extra } })
    }
  } catch {
    // 网络失败时保留本机配置
  }
}

function syncPref(key: string, value: any) {
  void request({
    url: '/weida/preferences',
    method: 'put',
    data: { role: role.value, key, value },
  }).catch(() => undefined)
}

const sortableColumns = computed(() =>
  displayColumns.value
    .filter((c) => c.kind === 'field')
    .map((c) => ({ key: (c as any).column as string, label: c.label }))
    .slice(0, 40),
)

function openDashboard() {
  const suffix = role.value === 'customer_service' ? 'cs' : role.value === 'pricing' ? 'pricing' : 'ops'
  router.push(`/weida/dashboard-${suffix}`)
}

function sortBy(column: string) {
  if (sortField.value === column) sortDesc.value = sortDesc.value === true ? false : true
  else {
    sortField.value = column
    sortDesc.value = true
  }
  page.value = 1
  void load()
}

const antColumns = computed<any[]>(() => {
  const cols: any[] = [
    { title: '#', key: '__index', width: 32, align: 'center' },
    {
      title: role.value === 'pricing' ? '航线状态' : '单票状态',
      key: '__status',
      width: columnWidths.value.__status || 128,
      fixed: 'left',
      customHeaderCell: () => ({
        onMousedown: (event: MouseEvent) => {
          const target = event.currentTarget as HTMLElement
          if (target && target.getBoundingClientRect().right - event.clientX <= 8) {
            startColumnResize(event, '__status')
          }
        },
      }),
    },
  ]
  // 「查看」列紧挨着常驻状态列（妙搭 detailColumn 置顶），且不依赖账单状态列
  if (role.value === 'domestic_ops') {
    cols.push({ title: '查看', key: '__view_left', width: 64, fixed: 'left', align: 'center' })
  }
  for (const c of displayColumns.value) {
    const prop = c.kind === 'field' ? c.column : c.key
    cols.push({
      title: c.label,
      key: c.key,
      dataIndex: prop,
      width: columnWidths.value[c.key] || columnWidth(c),
      fixed: frozenColumns.value.includes(c.key) ? 'left' : undefined,
      ellipsis: false,
      customCell: (record: Row) => ({
        style: paintCellStyle({ row: record, column: { property: prop } }),
        onClick: () => onCellClick(record, { property: prop }),
        // 双击也进编辑（日期这类字段用户习惯双击）
        onDblclick: () => onCellClick(record, { property: prop }),
      }),
      customHeaderCell: () => ({
        draggable: true,
        onDragstart: (event: DragEvent) => startColumnDrag(event, c.key),
        onDragover: (event: DragEvent) => event.preventDefault(),
        onDrop: (event: DragEvent) => dropColumn(event, c.key),
        onMousedown: (event: MouseEvent) => {
          const target = event.currentTarget as HTMLElement
          if (target && target.getBoundingClientRect().right - event.clientX <= 8) {
            startColumnResize(event, c.key)
          }
        },
      }),
    })
  }
  if (todoFields.value.length) {

  }
  if (role.value !== 'domestic_ops') {
    cols.push({ title: '', key: '__view', width: 72, fixed: 'right', align: 'center' })
  }
  return cols
})

function antCustomRow(record: Row) {
  return {
    style: rowTintStyle({ row: record }),
    // 未读加粗只有国内操作台配了（妙搭 rowHints.unreadBold），别的台子不加
    class: role.value === 'domestic_ops' && isUnread(record) ? 'wb-row-unread' : '',
  }
}

function onAntTableChange(_pagination: any, _filters: any, sorter: any) {
  if (!sorter || !sorter.order) {
    sortField.value = ''
    sortDesc.value = null
  } else {
    sortField.value = String(sorter.field || sorter.columnKey || '')
    sortDesc.value = sorter.order === 'descend'
  }
  page.value = 1
  load()
}

const addableColumns = computed(() => availableColumns.value.filter((c) => !columnOrder.value.includes(c.column)))
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const keyword = ref('')
type FilterCondition = { column: string; op: string; value: any; value2?: any }
type FilterView = {
  id: string
  name: string
  conjunction: 'and' | 'or'
  conditions: FilterCondition[]
}
const TEXT_OPS = [
  { label: '包含', value: 'contains' },
  { label: '等于', value: 'eq' },
  { label: '不等于', value: 'ne' },
  { label: '为空', value: 'is_empty' },
  { label: '不为空', value: 'is_not_empty' },
  { label: '大于', value: 'gt' },
  { label: '小于', value: 'lt' },
]
const DATE_OPS = [
  { label: '今天', value: 'date_today' },
  { label: '明天', value: 'date_tomorrow' },
  { label: '本周', value: 'date_this_week' },
  { label: '下周', value: 'date_next_week' },
  { label: '最近 7 天', value: 'date_last_7_days' },
  { label: '未来 7 天', value: 'date_next_7_days' },
  { label: '等于某天', value: 'date_on' },
  { label: '早于某天', value: 'date_before' },
  { label: '晚于某天', value: 'date_after' },
  { label: '区间', value: 'date_between' },
  { label: '为空', value: 'is_empty' },
  { label: '不为空', value: 'is_not_empty' },
]
const CHECK_OPS = [
  { label: '等于', value: 'eq' },
  { label: '未勾选', value: 'is_empty' },
  { label: '已勾选', value: 'is_not_empty' },
]
const PERSON_FILTER_COLUMNS = new Set([
  'opportunity_person_in_charge',
  'responsible_route',
  'domestic_operation',
  'sales_person',
  'operator',
])
const VALUELESS_OPS = new Set([
  'is_empty',
  'is_not_empty',
  'date_today',
  'date_tomorrow',
  'date_this_week',
  'date_next_week',
  'date_last_7_days',
  'date_next_7_days',
])
const TODO_LABELS: Record<string, string> = {
  create_booking: '创建订舱',
  send_pickup_email: '发送提货邮件',
  customs_declaration: '报关',
  issue_operation_plan: '出操作计划',
  send_forecast: '发送预报',
}
const alertDialogOpen = ref(false)
const alertDialogRecord = ref<any>(null)

function openAlerts(record: any): void {
  if (!record?.alerts?.length) return
  alertDialogRecord.value = record
  alertDialogOpen.value = true
}

/** 弹框里取消：调原来的 dismissAlert，并把这一条从本地副本里摘掉（立刻消失）。 */
function dismissAlertFromDialog(a: any): void {
  const rec = alertDialogRecord.value
  if (!rec) return
  dismissAlert(rec, a)
  rec.alerts = rec.alerts.filter((x: any) => x !== a)
  if (rec.alerts.length === 0) alertDialogOpen.value = false
}

function alertTimeText(a: any): string {
  const ms = Number(a?.at || 0)
  if (!ms) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const FEEDBACK_TITLES = new Set(['报价单录入反馈', '国内账单录入反馈', '国外账单录入反馈', '系统开票反馈'])
function alertBadgeText(a: any): string {
  if (a?.severity === 'info') return '已完成'
  if (a?.severity === 'critical') return FEEDBACK_TITLES.has(a?.title) ? '待处理 · 失败' : '待处理'
  return '待处理'
}
const todoFields = computed(() => config.value?.todo_checkboxes ?? [])
const linkBlocks = computed(() => config.value?.link_blocks ?? [])
const relatedBlocks = computed(() => config.value?.related_blocks ?? [])
const visibleRelatedBlocks = computed(() =>
  relatedBlocks.value.filter((rb) => {
    if (role.value === 'domestic_ops' && rb.key === 'insurance') {
      return String(detail.value?.fields.need_insurance?.value ?? '').trim() === '是'
    }
    if (!rb.when_contains) return true
    // 任一列命中（车队页：提货车队 或 二程车队 含云港先达）
    const fields = rb.when_fields?.length ? rb.when_fields : rb.when_field ? [rb.when_field] : []
    if (!fields.length) return true
    return fields.some((field) => {
      const raw = editModel[field] ?? detail.value?.fields[field]?.value ?? ''
      return String(raw).includes(rb.when_contains as string)
    })
  }),
)
const relatedData = reactive<Record<string, RelatedRow[]>>({})
const relatedLoading = reactive<Record<string, boolean>>({})
const approvalBusy = ref(false)
const approvalOpen = ref(false)
const approvals = ref<Array<Record<string, any>>>([])
const approvalComment = ref('')
const goodsOpen = ref(false)
const goodsBusy = ref(false)
const goodsOrderId = ref(0)
const goodsRows = ref<Array<Record<string, any>>>([])
const notifOpen = ref(false)
const dashPeriod = ref('week')
const dashGroup = ref('customer')
const dashRange = ref<[string, string] | null>(null)
const dashTrendRange = ref<[string, string] | null>(null)
const dashOwner = ref('')
const dashOwnerOptions = computed(() => {
  const owners = dashboard.value.sections?.find((s) => s.key === 'owners')?.data || []
  return owners.map((d) => String(d.name)).filter(Boolean)
})
const dashDetailOpen = ref(false)
const dashDetailTitle = ref('')
const dashDetailRows = ref<Array<Record<string, any>>>([])
const textColorOpen = ref(false)
const textColorColumn = ref('')
const textColorValue = ref('')
const columnTextColors = ref<Record<string, string>>({})
const notifications = ref<Array<Record<string, any>>>([])
const unreadCount = ref(0)
const finalPlanOptions = computed(() =>
  relatedPlans.value.map((p) => ({
    id: p.id,
    label: (p.fields.scheme_no?.value as string) || (p.fields.app_scheme_name?.value as string) || `#${p.id}`,
  }))
)
const detailActions = computed(() => config.value?.detail_actions ?? [])
const actionsForTab = (label: string) => detailActions.value.filter((a) => a.tab === label)
const commands = computed(() => config.value?.commands ?? [])
const commandsForTab = (label: string) => commands.value.filter((c) => c.tab === label)
const actionBusy = ref('')
const todoLabel = (f: string) => TODO_LABELS[f] || f
const todoChecked = (f: string) => !!detail.value?.fields[f]?.value
const todoDone = (row: Row) => todoFields.value.filter((f) => row.fields[f]?.value).length
const conditions = ref<FilterCondition[]>([])
const draftConditions = ref<FilterCondition[]>([])
const conjunction = ref<'and' | 'or'>('and')
const draftConjunction = ref<'and' | 'or'>('and')
const savedViews = ref<FilterView[]>([])
const activeViewId = ref('')
const viewName = ref('')
const filterOpen = ref(false)
const loading = ref(false)
const tableRef = ref<any>(null)
const initialBottomDone = ref(false)
const tailOffset = ref(0)
const earlierBusy = ref(false)
const arrivalBusy = ref(false)
const docBusy = ref('')
const aiBusy = ref<number | null>(null)
const aiFees = ref<Record<string, any>[]>([])
const aiFeesLoading = ref(false)
const extraFees = ref<Record<string, any>[]>([])
const detailAttachments = reactive<Record<string, AttachmentItem[]>>({})
const pwdOpen = ref(false)
const pwdSaving = ref(false)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const handoverOpen = ref(false)
const weekendOpen = ref(false)
const weekendOrderId = ref(0)
const handoverTo = ref('')
const weekendChecked = ref<string[]>([])
const feeAddOpen = ref(false)
const feeAdd = reactive({
  fee_category: '',
  currency: 'CNY',
  billing_unit: 'PER SHIPMENT',
  app_quantity: '1',
  unit_price: '',
  tax_point: '',
  tax_calc_type: '',
  fee_entry_status: '',
  domestic_abroad: '国内',
  ifreight_id: null as number | null,
})
const billlibOpen = ref(false)
const billlibValue = ref<number | null>(null)
const billlibTarget = ref<RelatedRow | null>(null)
// iFreight 展开体上的字段类型（妙搭里全是选项/人员/客商/三字代码，不该是自由输入）
const IFREIGHT_PERSON_COLUMNS = new Set(['sales_person', 'opportunity_person', 'domestic_operation'])
const IFREIGHT_VENDOR_COLUMNS = new Set(['foreign_agent', 'booking_agent'])
const IFREIGHT_MULTILINE = new Set([
  'client_address',
  'shipper_address',
  'consignee_address',
  'notify_party_address',
  'goods_description',
  'shipping_mark',
  'remark',
])
const IFREIGHT_CITY_COLUMNS = new Set(['origin_city_code', 'dest_city_code'])
const IFREIGHT_OPTION_COLUMNS = [
  'document_type',
  'cargo_receiving_method',
  'business_type',
  'pp_currency',
  'business_department',
  'route',
  'airline_company',
  'freight_payment_method',
  'surcharge_payment_method',
  'piece_unit',
] as const
const ifreightOptions = reactive<Record<string, string[]>>({})
const promptTemplates = ref<Array<{ type_name: string; customer?: string | null; prompt?: string }>>([])

async function loadPromptTemplates() {
  try {
    const res = await request<any>({
      url: '/weida/prompts',
      method: 'get',
      params: { kind: '委托书' },
    })
    promptTemplates.value = res.data?.data ?? []
  } catch {
    promptTemplates.value = []
  }
}

async function applyPromptTemplate(row: RelatedRow, typeName: string) {
  if (!typeName) return
  const tpl = promptTemplates.value.find((t) => t.type_name === typeName)
  if (!tpl?.prompt) return
  ifreightCard[row.id].prompt = tpl.prompt
  await saveRelatedField(row as any, row, 'prompt_type', typeName)
  await saveRelatedField(row as any, row, 'prompt', tpl.prompt)
}



async function loadIfreightOptions() {
  await Promise.all(
    IFREIGHT_OPTION_COLUMNS.map(async (column) => {
      if (ifreightOptions[column]?.length) return
      try {
        const res = await request<any>({
          url: '/weida/options',
          method: 'get',
          params: { table: 'biz_ifreight', column },
        })
        ifreightOptions[column] = res.data?.data ?? []
      } catch {
        ifreightOptions[column] = []
      }
    }),
  )
  // 单据类型是规则字段，库里可能还没有选项：按妙搭口径补两份兜底
  if (!ifreightOptions.document_type.length) ifreightOptions.document_type = ['直单', '分单']
}

function ifreightOptionsFor(column: string): string[] {
  if (column === 'document_type' && !ifreightOptions.document_type?.length) {
    return ['直单', '分单']
  }
  return ifreightOptions[column] || []
}

const ifreightDetailRows = [
  // 照妙搭 detailRows 逐行摆（一行 2~4 格）
  [{ column: 'document_type', label: '单据类型' }, { column: 'house_bl_no', label: '分单号' }, { column: 'cargo_receiving_method', label: '揽货方式' }, { column: 'master_no', label: '主单号' }],
  [{ column: 'business_type', label: '业务类型' }, { column: 'customer_ref_no', label: '客户 REF NO.' }],
  [{ column: 'client_name', label: '委托人名称' }, { column: 'client_address', label: '委托人地址' }],
  [{ column: 'shipper_name', label: '发货人名称' }, { column: 'shipper_address', label: '发货人地址' }],
  [{ column: 'consignee_name', label: '收货人名称' }, { column: 'consignee_address', label: '收货人地址' }],
  [{ column: 'foreign_agent', label: '国外代理' }, { column: 'airline_company', label: '航空公司' }],
  [{ column: 'notify_party_name', label: '通知人名称' }, { column: 'notify_party_address', label: '通知人地址' }],
  [{ column: 'booking_agent', label: '订舱代理' }],
  [{ column: 'quantity', label: '件数' }, { column: 'route', label: '航线' }],
  [{ column: 'gross_weight', label: '毛重' }, { column: 'volume', label: '体积' }],
  [{ column: 'origin_city_code', label: '起运地城市代码' }, { column: 'flight_number', label: '航班号' }, { column: 'estimated_departure_time', label: '预计起运时间' }],
  [{ column: 'dest_city_code', label: '目的地城市代码' }, { column: 'estimated_arrival_time', label: '预计到港时间' }, { column: 'pp_currency', label: 'PP币别' }],
  [{ column: 'freight_payment_method', label: '运费付款方式' }, { column: 'surcharge_payment_method', label: '杂费付款方式' }],
  [{ column: 'sales_person', label: '业务员' }, { column: 'domestic_operation', label: '国内操作' }, { column: 'opportunity_person', label: '商机负责人' }, { column: 'business_department', label: '业务所属部门' }],
  [{ column: 'customer_billing_weight', label: '客户计费重' }, { column: 'agent_billing_weight', label: '代理计费重' }],
  [{ column: 'shipping_mark', label: '唛头' }, { column: 'goods_description', label: '货品描述' }],
  [{ column: 'dimension', label: '尺寸' }],
]
const billlibOptions = ref<Array<Record<string, any>>>([])
const ifreightCard = reactive<Record<number, { prompt_type: string; prompt: string }>>({})
const premiumAmount = ref('')
const premiumCurrency = ref('CNY')
const premiumRate = ref('1')
const premiumPct = ref('0.1')
const premiumResult = computed(() => {
  const amount = Number(premiumAmount.value) || 0
  const rate = Number(premiumRate.value) || 1
  const pct = Number(premiumPct.value) || 0
  return amount * 1.1 * rate * pct / 100
})
const extraTotals = ref<Record<string, number>>({})
const aiFeesBusy = ref(false)
const arrivalAttachments = ref<AttachmentItem[]>([])
const billStatusOptions = [
  '没有国内账单',
  '没有国外账单',
  '未发送账单确认',
  '已发送账单确认',
  '客人未确认',
  '账单已确认',
  '已申请开票（发票未开具）',
  '发票已开具（未提供客户发票）',
  '开票完成',
]
let boundScrollEl: HTMLElement | null = null

const drawerOpen = ref(false)
const vendorOptions = ref<Array<{ id: number; name: string; full_name: string }>>([])
const DRAWER_MIN = 480
const drawerWidth = ref(loadDrawerWidth())

function loadDrawerWidth() {
  const raw = Number(localStorage.getItem(`weida_drawer_width:${role.value}`))
  return Number.isFinite(raw) && raw >= DRAWER_MIN ? raw : 720
}

function startDrawerResize(event: MouseEvent) {
  const startX = event.clientX
  const startWidth = drawerWidth.value
  const maxWidth = Math.max(DRAWER_MIN, window.innerWidth - 24)
  const onMove = (e: MouseEvent) => {
    const next = startWidth + (startX - e.clientX)
    drawerWidth.value = Math.round(Math.min(Math.max(next, DRAWER_MIN), maxWidth))
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
    localStorage.setItem(`weida_drawer_width:${role.value}`, String(drawerWidth.value))
    syncPref('drawer', drawerWidth.value)
  }
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

watch(role, () => {
  initialBottomDone.value = false
  loadSeenRows()
  restoreConfigCache()
  restoreRowsCache()
  drawerWidth.value = loadDrawerWidth()
  void loadAccountState()
  void loadConfig()
  void load()
})
const drawerMode = ref<'edit' | 'create'>('edit')
const activeTab = ref('')
const detail = ref<Row | null>(null)
const relatedPricing = ref<RelatedRow[]>([])
const quoteDialogOpen = ref(false)
const quoteEditId = ref<number | null>(null)
const quoteSaving = ref(false)
const reviewBusy = ref(false)
const quoteForm = reactive<Record<string, any>>({})
const QUOTE_FIELDS = [
  { key: 'supplier', label: '供应商', kind: 'supplier' },
  { key: 'cost_amount', label: '成本金额', kind: 'text' },
  { key: 'cost_currency', label: '成本币种', kind: 'currency' },
  { key: 'level_ratio_requirement', label: '等级、比例要求', kind: 'textarea' },
  { key: 'airline', label: '航空公司', kind: 'text' },
  { key: 'departure_port', label: '启运港', kind: 'port' },
  { key: 'destination_port', label: '目的港', kind: 'port' },
  { key: 'transit_point', label: '中转点', kind: 'ports' },
  { key: 'estimated_transit_days', label: '预计运输时效（天）', kind: 'text' },
  { key: 'quotation_validity_period', label: '报价有效期', kind: 'date' },
  { key: 'earliest_flight_time', label: '最早航班时间', kind: 'date' },
  { key: 'latest_arrival_time', label: '最晚到港时间', kind: 'date' },
  { key: 'risk_reminder', label: '风险提醒', kind: 'text' },
  { key: 'route_plan_suggestion', label: '航线方案建议', kind: 'textarea' },
  { key: 'flight_schedule', label: '航班班次', kind: 'text' },
  { key: 'origin_port_special_fee', label: '启运港特殊费用', kind: 'textarea' },
  { key: 'destination_port_demurrage_fee', label: '目的港滞港费', kind: 'textarea' },
]
const relatedPlans = ref<RelatedRow[]>([])
const quotePortOptions = ref<Array<{ code: string; name: string }>>([])
const roleOptions = [
  { value: 'customer_service', label: '国内客服' },
  { value: 'pricing', label: '航线价格' },
  { value: 'domestic_ops', label: '国内操作' },
  { value: 'home', label: '总览' },
]

function switchRole(value: string) {
  if (!value) return
  if (value === 'home') router.push('/weida')
  else if (value !== role.value) router.push(`/weida/${value}`)
}

const relatedOrders = ref<RelatedRow[]>([])
const flightSource = computed<Row | RelatedRow | null>(() =>
  role.value === 'domestic_ops' ? detail.value : relatedOrders.value[0] || null,
)

function flightField(key: string): string {
  const source = flightSource.value
  if (!source) return ''
  return display((source.fields as any)?.[key]?.value, 'text')
}
const orderDraft = reactive<Record<number, Record<string, any>>>({})
const feeOpen = ref(false)
const feePlanId = ref<number | null>(null)
const feePlanLabel = ref('')
const fees = ref<RelatedRow[]>([])
const planAttachments = reactive<Record<number, AttachmentItem[]>>({})
const feeLoading = ref(false)
const quoteBusy = ref<number | null>(null)
const bookingBusy = ref<number | null>(null)
const handoverBusy = ref<number | null>(null)
const orderAttachments = reactive<Record<number, AttachmentItem[]>>({})
// 交接操作页的三个附件列（相关资料 / 国内邮件 / 国外邮件），按订单挂
const ORDER_ATTACH_FIELDS = ['相关资料', '国内邮件', '国外邮件'] as const
// 投保页行展开的明细（妙搭 detailColumns，逐字对齐）
const INSURANCE_DETAIL: Array<{ column: string; label: string; type: string }> = [
  { column: 'app_insurance', label: '投保', type: 'checkbox' },
  { column: 'insurance_clause', label: '投保条款', type: 'text' },
  { column: 'client_name', label: '被保险人', type: 'text' },
  { column: 'quantity', label: '件数', type: 'number' },
  { column: 'piece_unit', label: '单位', type: 'text' },
  { column: 'customs_declaration_goods_name', label: '中文品名', type: 'text' },
  { column: 'goods_description', label: '英文品名', type: 'text' },
  { column: 'consignee_address', label: '派送地址', type: 'text' },
  { column: 'declaration_amount', label: '总价', type: 'number' },
  { column: 'currency', label: '币制', type: 'text' },
  { column: 'port_of_departure', label: '启运地', type: 'text' },
  { column: 'transport_mode', label: '运输方式', type: 'text' },
  { column: 'customs_invoice', label: '发票', type: 'text' },
]
const orderExtraAttachments = reactive<Record<number, Record<string, AttachmentItem[]>>>({})
const orderSnapshots = reactive<Record<number, string>>({})
const goodsSnapshots = reactive<Record<number, string>>({})
const portOptions = ref<Array<{ code: string; name: string }>>([])
const portOptionsLoading = ref(false)
const orderGoodsDraft = reactive<
  Record<number, { piece_count: string; gross_weight_kg: string; volume_m3: string; size: string }>
>({})
const detailSnapshot = ref('')
const feeSnapshot = ref('')
const feeEditing = ref<{ id: number; column: string } | null>(null)
const feeEditValue = ref<any>('')
const feeOptions = reactive({
  fee_categories: [] as string[],
  groups: [] as string[],
  billing_units: [] as string[],
  currencies: [] as string[],
})
const feeForm = reactive({
  fee_category: '',
  group: '',
  billing_unit: '',
  currency: 'CNY',
  amount: '',
  remark: '',
})
const planBusy = ref<number | null>(null)
const editModel = reactive<Record<string, any>>({})
const saving = ref(false)
let eventSource: EventSource | null = null
let pollTimer: number | null = null

const filterableColumns = computed(() => availableColumns.value.filter((c) => !c.virtual))
type CreateFieldDef = {
  column: string
  label: string
  required?: boolean
  wide?: boolean
}

const CREATE_FIELDS: CreateFieldDef[] = [
  { column: 'opportunity_person_in_charge', label: '商机负责人' },
  { column: 'customer_name', label: '客户', required: true },
  { column: 'client_ref_no', label: '客户 REF NO.', required: true, wide: true },
  { column: 'piece_count', label: '件数', required: true },
  { column: 'gross_weight_kg', label: '毛重（kg）', required: true },
  { column: 'volume_m3', label: '体积（m³）', required: true },
  { column: 'size_length_width_height_quantity', label: '尺寸（长*宽*高 * 数量）', wide: true },
  { column: 'goods_name', label: '货物品名', required: true },
  { column: 'goods_value', label: '货值' },
  { column: 'goods_special_attr', label: '货物特殊属性' },
  { column: 'source_location', label: '货源地', required: true, wide: true },
  { column: 'goods_ready_time', label: '货好时间', required: true },
  { column: 'request_arrival_time', label: '要求到港时间', required: true },
  { column: 'transport_clause', label: '运输条款', required: true },
  { column: 'delivery_address', label: '派送地址', wide: true },
  { column: 'destination_port', label: '目的 港', required: true },
  { column: 'responsible_route', label: '负责航线' },
  { column: 'customer_core_concern', label: '客户核心关注点', wide: true },
  { column: 'route_requirement', label: '路线要求', wide: true },
  { column: 'latest_quote_deadline', label: '最晚报价截止时间', required: true },
]

const createFieldList = computed(() => {
  const map = new Map(detailFields.value.map((f) => [f.column, f]))
  return CREATE_FIELDS.map((item) => ({ ...item, field: map.get(item.column) })).filter((item) => item.field)
})

const createMissing = computed(() =>
  createFieldList.value
    .filter((item) => item.required)
    .filter((item) => {
      const value = editModel[item.column]
      if (value === null || value === undefined || String(value).trim() === '') return true
      if (['piece_count', 'gross_weight_kg', 'volume_m3'].includes(item.column)) {
        return !(Number(value) > 0)
      }
      return false
    })
    .map((item) => item.label)
)
const canCreate = computed(() => createMissing.value.length === 0)

const fieldOptions = reactive({
  transport_clause: [] as string[],
  destination_port: [] as Array<{ code: string; name: string }>,
  goods_special_attr: [] as string[],
})
const portSearching = ref(false)
let portSearchTimer: ReturnType<typeof setTimeout> | null = null

async function searchDestinationPorts(keyword: string) {
  if (portSearchTimer) clearTimeout(portSearchTimer)
  portSearchTimer = setTimeout(async () => {
    if (!keyword || !keyword.trim()) {
      fieldOptions.destination_port = []
      return
    }
    portSearching.value = true
    try {
      const res = await request<any>({
        url: '/weida/port-options',
        method: 'get',
        params: { keyword: keyword || undefined, limit: 6000 },
      })
      fieldOptions.destination_port = res.data?.data ?? []
    } catch {
      fieldOptions.destination_port = []
    } finally {
      portSearching.value = false
    }
  }, 250)
}

function isCreateSelect(column: string) {
  return ['transport_clause', 'destination_port', 'goods_special_attr'].includes(column)
}

function createSelectOptions(column: string) {
  return fieldOptions[column as keyof typeof fieldOptions] ?? []
}

async function loadFieldOptions() {
  const columns = ['transport_clause', 'destination_port', 'goods_special_attr'] as const
  await Promise.all(
    columns.map(async (column) => {
      try {
        if (column === 'destination_port') {
          // 港口一律远程按三字代码搜（铁律），不预载全量避免客户端模糊匹配出杂项
          fieldOptions.destination_port = []
          return
        }
        const res = await request<any>({
          url: '/weida/options',
          method: 'get',
          params: { table: 'biz_opportunity', column, limit: 100 },
        })
        fieldOptions[column] = res.data?.data ?? []
      } catch {
        fieldOptions[column] = []
      }
    })
  )
}

const detailFields = computed<Col[]>(() => {
  if (drawerMode.value === 'create') {
    const colSet = new Set(columns.value.map((c) => c.column))
    return (config.value?.detail_fields || []).filter((f) => f.editable || colSet.has(f.column))
  }
  if (!detail.value) return []
  const known = new Map<string, Col>()
  for (const c of config.value?.detail_fields || []) known.set(c.column, c)
  for (const c of columns.value) known.set(c.column, c)
  return Object.keys(detail.value.fields).map((col) => {
    return (
      known.get(col) || {
        column: col,
        label: detail.value?.fields[col]?.label || col,
        type: 'text',
        editable: false,
      }
    )
  })
})
const detailSections = computed<Array<{ label: string; fields: Col[] }>>(() => {
  const sections = config.value?.detail_sections || []
  if (!sections.length) return []
  const visible = new Set(Object.keys(detail.value?.fields || {}))
  const colSet = new Set(columns.value.map((c) => c.column))
  return sections
    .filter((section) => {
      if (role.value !== 'domestic_ops') return true
      // 客服交接操作决定操作工作台要不要出现这两页（秒级：轮询时详情一起刷新）
      if (section.label === '报关') return String(detail.value?.fields.need_declaration?.value ?? '').trim() === '是'
      return true
    })
    .map((section) => ({
      label: section.label,
      fields: section.fields.filter((f) => {
        if (drawerMode.value === 'create') return f.editable || colSet.has(f.column)
        return visible.has(f.column)
      }),
    }))
    .filter((section) => section.fields.length > 0)
})

const detailTitle = computed(() => {
  if (drawerMode.value === 'create') return '新建记录'
  if (!detail.value) return '记录详情'
  const first = columns.value[0]
  return first ? String(detail.value.fields[first.column]?.value || `#${detail.value.id}`) : `#${detail.value.id}`
})

function goHome() {
  router.push('/')
}

function cellValue(row: Row, col: string): any {
  return row.fields[col]?.value
}

function isEmpty(v: any): boolean {
  return v === null || v === undefined || v === ''
}

function display(v: any, type: string): string {
  if (isEmpty(v)) return '—'
  if (type === 'checkbox') return v === 1 || v === true || v === '1' ? '是' : '否'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  if (type === 'date' && typeof v === 'string') return v.slice(0, 10)
  if (type === 'datetime' && typeof v === 'string') return v.replace('T', ' ').slice(0, 19)
  return String(v)
}

function columnWidth(c: DisplayCol): number {
  if (c.kind === 'template') return 200
  if (c.kind === 'blank') return 160
  if (c.kind === 'field' && role.value === 'domestic_ops') {
    const map: Record<string, number> = {
      bill_status: 150,
      flight: 110,
      master_no: 130,
      house_bl_no: 110,
      goods_data: 160,
      transport_clause: 100,
      opportunity_no: 120,
      supplier: 120,
      customer_name: 110,
      foreign_agent_name: 110,
      client_ref_no: 130,
      warehouse_in_operation_time: 160,
      fee_record: 150,
    }
    if (map[c.column]) return map[c.column]
  }
  if (c.kind === 'field' && c.column === 'bill_status') return 224
  return 160
}

function renderTemplate(expression: string, row: Row): string {
  return expression.replace(/\{([^{}]+)\}/g, (_all, rawKey) => {
    const key = String(rawKey).trim()
    const col = labelToColumn.value.get(key) || key
    const meta = columnMap.value.get(col)
    const value = cellValue(row, col)
    return meta ? display(value, meta.type) : display(value, 'text')
  })
}

async function loadStats() {
  try {
    const res = await request<any>({
      url: `/weida/${dashRole.value}/dashboard`,
      method: 'get',
      params: {
        period: dashPeriod.value,
        from_date: dashPeriod.value === 'range' ? dashRange.value?.[0] : undefined,
        to_date: dashPeriod.value === 'range' ? dashRange.value?.[1] : undefined,
        group: dashGroup.value,
        trend_from: dashTrendRange.value?.[0],
        trend_to: dashTrendRange.value?.[1],
        owner: dashOwner.value || undefined,
      },
    })
    dashboard.value = res.data?.data ?? { title: '', cards: [], charts: [] }
  } catch {
    try {
      const res = await request<any>({ url: `/weida/${dashRole.value}/stats`, method: 'get' })
      const data = res.data?.data ?? {}
      dashboard.value = {
        title: data.title || '',
        cards: Object.entries(data.labels || {}).map(([key, label]) => ({
          label: String(label),
          value: Number(data.values?.[key] || 0),
        })),
        charts: [],
      }
    } catch {
      dashboard.value = { title: '', cards: [], charts: [] }
    }
  }
}

async function onSectionClick(s: DashSection, params: any) {
  const name = String(params?.name || '')
  if (!name) return
  const dimension =
    s.key === 'customers'
      ? 'customer'
      : s.key === 'pricing-customers'
        ? dashGroup.value
        : s.key === 'owners'
        ? 'owner'
        : s.key === 'operator'
          ? 'operator'
          : ''
  if (!dimension) return
  dashDetailTitle.value = `${s.title} · ${name}`
  dashDetailRows.value = []
  dashDetailOpen.value = true
  try {
    const res = await request<any>({
      url: `/weida/${dashRole.value}/dashboard-detail`,
      method: 'get',
      params: {
        dimension,
        name,
        period: dashPeriod.value,
        from_date: dashPeriod.value === 'range' ? dashRange.value?.[0] : undefined,
        to_date: dashPeriod.value === 'range' ? dashRange.value?.[1] : undefined,
      },
    })
    dashDetailRows.value = res.data?.data?.items ?? []
  } catch {
    dashDetailRows.value = []
  }
}

function chartValue(d: any) {
  return Number(d?.value ?? d?.created ?? 0)
}

function chartTop(s: DashSection) {
  const max = Math.max(...(s.data || []).map((d) => chartValue(d)), 0)
  return max <= 4 ? 4 : Math.ceil(max / 4) * 4
}

function chartTicks(s: DashSection) {
  const top = chartTop(s)
  return [1, 0.75, 0.5, 0.25, 0].map((f) => Math.round(top * f))
}

function barHeight(d: any, s: DashSection) {
  const top = chartTop(s)
  const value = chartValue(d)
  if (!top) return 0
  return Math.max((value / top) * 100, value > 0 ? 1.5 : 0)
}

function barPart(d: any) {
  const total = chartValue(d)
  if (!total) return 0
  return Math.min(100, (Number(d.value2 ?? d.won ?? 0) / total) * 100)
}

function sectionOption(s: DashSection) {
  const labels = s.data.map((d) => String(d.name))
  const captions = new Map(s.data.map((d) => [String(d.name), String(d.caption || '')]))
  const zoom =
    labels.length > 9
      ? [
          {
            type: 'slider',
            show: true,
            bottom: 6,
            height: 16,
            startValue: 0,
            endValue: 8,
            brushSelect: false,
          },
          { type: 'inside', startValue: 0, endValue: 8 },
        ]
      : undefined
  if (s.type === 'line') {
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 48, right: 24, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: labels, boundaryGap: false },
      yAxis: { type: 'value' },
      series: [
        {
          type: 'line',
          data: s.data.map((d) => d.value),
          connectNulls: false,
          symbolSize: 7,
          lineStyle: { width: 2, color: '#0f4c46' },
          itemStyle: { color: '#0f4c46', borderRadius: [2, 2, 0, 0] },
        },
      ],
    }
  }
  const hasSecond = s.data.some((d) => d.value2 !== undefined)
  const series: any[] = []
  if (s.type === 'stacked' && hasSecond) {
    series.push({
      name: '未完成',
      type: 'bar',
      stack: 'total',
      data: s.data.map((d) => Math.max(0, Number(d.value || 0) - Number(d.value2 || 0))),
      barMaxWidth: 34,
      itemStyle: { color: '#d8e6e2', borderRadius: [2, 2, 0, 0] },
    })
    series.push({
      name: '已完成',
      type: 'bar',
      stack: 'total',
      data: s.data.map((d) => d.value2 ?? 0),
      barMaxWidth: 34,
      itemStyle: { color: '#0f4c46' },
    })
  } else {
    series.push({
      name: '数量',
      type: 'bar',
      data: s.data.map((d) => d.value),
      barMaxWidth: 34,
      itemStyle: { color: '#0f4c46', borderRadius: [2, 2, 0, 0] },
    })
  }
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 24, top: 20, bottom: zoom ? 90 : 70 },
    dataZoom: zoom,
    xAxis: {
      type: 'category',
      data: labels,
      axisTick: { alignWithLabel: true },
      axisLabel: {
        interval: 0,
        rotate: 0,
        formatter: (name: string) => `${name}\n${captions.get(name) || ''}`,
        fontSize: 10,
        lineHeight: 14,
        overflow: 'break',
        width: 76,
      },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eee7db' } } },
    series,
  }
}

function chartOption(c: DashChart) {
  if (c.type === 'pie') {
    return {
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, type: 'scroll' },
      series: [
        {
          type: 'pie',
          radius: ['38%', '62%'],
          center: ['50%', '45%'],
          data: c.data,
          label: { formatter: '{b} {d}%' },
        },
      ],
    }
  }
  const names = c.data.map((d) => d.name)
  const series: any[] = [{ name: '新建', type: 'bar', data: c.data.map((d) => d.value), barMaxWidth: 34 }]
  if (c.data.some((d) => d.value2 !== undefined)) {
    series.push({ name: '拿到货/已交接', type: 'bar', data: c.data.map((d) => d.value2 ?? 0), barMaxWidth: 34 })
  }
  if (c.data.some((d) => d.value3 !== undefined)) {
    series.push({ name: '已完成', type: 'bar', data: c.data.map((d) => d.value3 ?? 0), barMaxWidth: 34 })
  }
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, data: series.map((x) => x.name) },
    grid: { left: 48, right: 20, top: 36, bottom: 70 },
    xAxis: {
      type: 'category',
      data: names,
      axisLabel: { rotate: 25, width: 90, overflow: 'truncate' },
    },
    yAxis: { type: 'value' },
    series,
  }
}

async function loadVendors() {
  try {
    const res = await request<any>({ url: '/weida/vendors', method: 'get' })
    vendorOptions.value = res.data?.data ?? []
  } catch {
    vendorOptions.value = []
  }
}

function restoreConfigCache() {
  try {
    const cached = JSON.parse(localStorage.getItem(`weida_config_cache:${role.value}`) || 'null')
    if (cached?.columns) {
      config.value = cached
      columns.value = cached.columns ?? []
    }
  } catch {
    // ignore
  }
}

function restoreRowsCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(`weida_rows_cache:${role.value}`) || '[]')
    if (Array.isArray(cached) && cached.length) {
      rows.value = cached
      lastRowsKey = JSON.stringify(cached)
    }
  } catch {
    // ignore
  }
}

function saveRowsCache() {
  try {
    sessionStorage.setItem(`weida_rows_cache:${role.value}`, JSON.stringify(rows.value.slice(0, 150)))
  } catch {
    // ignore
  }
}

async function loadConfig() {
  const res = await request<any>({ url: `/weida/${role.value}/config`, method: 'get' })
  config.value = res.data?.data ?? null
  columns.value = config.value?.columns ?? []
  try {
    localStorage.setItem(`weida_config_cache:${role.value}`, JSON.stringify(config.value))
  } catch {
    // ignore
  }
  if (config.value?.no_paging) pageSize.value = 100
  restoreViewConfig()
  restoreFilterViews()
}

function restoreViewConfig() {
  const defaults = columns.value.map((c) => c.column)
  let order = defaults
  let frozen: string[] = []
  let templates: TemplateColumn[] = []
  let blanks: BlankColumn[] = []
  let widths: Record<string, number> = {}
  let blanksValues: Record<string, string> = {}
  const saved = localStorage.getItem(`weida_columns_v11:${role.value}`)
  if (saved) {
    try {
      const parsed = JSON.parse(saved)
      if (Array.isArray(parsed.order)) order = parsed.order
      if (Array.isArray(parsed.frozen)) frozen = parsed.frozen
      if (Array.isArray(parsed.templates)) templates = parsed.templates
      if (Array.isArray(parsed.blanks)) blanks = parsed.blanks
      if (parsed.blankValues && typeof parsed.blankValues === 'object') blanksValues = parsed.blankValues
      if (parsed.widths && typeof parsed.widths === 'object') widths = parsed.widths
    } catch {
      // 旧配置坏了就回默认
    }
  }
  try {
    const rawValues = localStorage.getItem(`weida_blank_values:${role.value}`)
    if (rawValues) blanksValues = { ...blanksValues, ...JSON.parse(rawValues) }
  } catch {
    // ignore broken local data
  }
  try {
    const rawPaint = localStorage.getItem(`weida_paint_rules:${role.value}`)
    paintRules.value = rawPaint ? JSON.parse(rawPaint) : []
  } catch {
    paintRules.value = []
  }
  try {
    const rawColors = localStorage.getItem(`weida_text_colors:${role.value}`)
    columnTextColors.value = rawColors ? JSON.parse(rawColors) : {}
  } catch {
    columnTextColors.value = {}
  }
  try {
    const savedHeight = Number(localStorage.getItem(`weida_row_height:${role.value}`) || '')
    rowHeight.value = savedHeight >= 30 && savedHeight <= 240 ? savedHeight : 54
    restoreTableZoom()
  } catch {
    rowHeight.value = 54
  }
  templateColumns.value = templates
  blankColumns.value = blanks
  blankValues.value = blanksValues
  columnWidths.value = widths
  columnOrder.value = order.filter((name: string) => {
    if (name.startsWith('tpl:')) return templates.some((t) => t.id === name)
    if (name.startsWith('blank:')) return blanks.some((b) => b.id === name)
    return columnMap.value.has(name)
  })
  if (!columnOrder.value.length) columnOrder.value = defaults
  frozenColumns.value = frozen.filter((name) => columnOrder.value.includes(name))
}

function restoreFilterViews() {
  try {
    const raw = localStorage.getItem(`weida_filter_views:${role.value}`)
    savedViews.value = raw ? JSON.parse(raw) : []
  } catch {
    savedViews.value = []
  }
  activeViewId.value = ''
}

let lastRowsKey = ''

async function load(silent = false) {
  if (!sortField.value) {
    sortDesc.value = null
  }
  if (!silent) loading.value = true
  try {
    const filters = conditions.value.map((c) => ({
      column: c.column,
      op: c.op,
      value: c.value,
      value2: c.value2,
    }))
    const res = await request<any>({
      url: `/weida/${role.value}/records`,
      method: 'get',
      params: {
        page: page.value,
        keyword: keyword.value || undefined,
        filters: filters.length ? JSON.stringify(filters) : undefined,
        conjunction: conjunction.value,
        sort_field: sortField.value || undefined,
        sort_desc: sortDesc.value === null ? undefined : sortDesc.value,
        page_size: role.value === 'domestic_ops' ? 100 : pageSize.value,
        tail: role.value === 'domestic_ops' || undefined,
      },
    })
    const data = res.data?.data ?? {}
    const nextRows = data.items || []
    const nextKey = JSON.stringify(nextRows)
    if (nextKey !== lastRowsKey) {
      rows.value = nextRows
      lastRowsKey = nextKey
    }
    total.value = data.total || 0
    saveRowsCache()
    if (role.value === 'domestic_ops') tailOffset.value = Number(data.offset || 0)
    if (data.columns?.length) columns.value = data.columns
  } catch (e: any) {
    if (!silent) ElMessage.error(e?.message || '加载失败')
  } finally {
    if (!silent) loading.value = false
    void loadNotifications()
    if (role.value === 'domestic_ops' && !initialBottomDone.value && rows.value.length) {
      initialBottomDone.value = true
      scrollTableToBottom()
    }
  }
}

function tableRoot(): HTMLElement | null {
  const value = tableRef.value as any
  const root = (value?.$el as HTMLElement | undefined) ?? (value as HTMLElement | undefined) ?? null
  return (root?.querySelector('.ant-table-body') as HTMLElement | null) ?? root
}

function scrollTableToBottom() {
  // Ant 表体渲染晚于数据更新，多试几次，避免首次到底被后续布局覆盖。
  for (const delay of [0, 80, 250, 600, 1200]) {
    window.setTimeout(() => {
      void nextTick(() => {
        const root = tableRoot()
        if (!root) return
        root.scrollTop = root.scrollHeight
        const wrap = root.querySelector('.ant-table-body, .ant-table-content, .el-scrollbar__wrap') as
          | HTMLElement
          | null
        if (wrap) wrap.scrollTop = wrap.scrollHeight
      })
    }, delay)
  }
}

function bindTableScroll() {
  nextTick(() => {
    const root = tableRoot()
    if (root && root !== boundScrollEl) {
      boundScrollEl = root
      root.addEventListener('scroll', () => {
        if (root.scrollTop < 80) void loadEarlier()
      })
    }
  })
}

async function loadEarlier() {
  if (role.value !== 'domestic_ops' || earlierBusy.value || tailOffset.value <= 0) return
  earlierBusy.value = true
  const wrap = boundScrollEl || tableRoot()
  const oldHeight = wrap?.scrollHeight || 0
  const oldTop = wrap?.scrollTop || 0
  try {
    const res = await request<any>({
      url: `/weida/${role.value}/records`,
      method: 'get',
      params: {
        page_size: Math.min(100, tailOffset.value),
        offset: Math.max(tailOffset.value - 100, 0),
        keyword: keyword.value || undefined,
        filters: conditions.value.length
          ? JSON.stringify(conditions.value.map((c) => ({ column: c.column, op: c.op, value: c.value, value2: c.value2 })))
          : undefined,
        conjunction: conjunction.value,
        sort_field: sortField.value || undefined,
        sort_desc: sortDesc.value === null ? undefined : sortDesc.value,
      },
    })
    const data = res.data?.data ?? {}
    const older = data.items || []
    if (older.length) {
      rows.value = [...older, ...rows.value]
      tailOffset.value = Number(data.offset || 0)
      total.value = data.total || total.value
      await nextTick()
      if (wrap) wrap.scrollTop = oldTop + (wrap.scrollHeight - oldHeight)
    } else {
      tailOffset.value = 0
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '加载更早记录失败')
  } finally {
    earlierBusy.value = false
  }
}

function onSortChange({ prop, order }: { prop: string; order: string | null }) {
  if (!order) {
    sortField.value = ''
    sortDesc.value = null
  } else {
    sortField.value = prop
    sortDesc.value = order === 'descending'
  }
  page.value = 1
  load()
}

function setColumnWidth(key: string, width: number | undefined) {
  if (!width) return
  columnWidths.value = { ...columnWidths.value, [key]: Math.round(width) }
}

function applyTextColor() {
  if (!textColorColumn.value || !textColorValue.value) return
  columnTextColors.value = {
    ...columnTextColors.value,
    [textColorColumn.value]: textColorValue.value,
  }
  localStorage.setItem(`weida_text_colors:${role.value}`, JSON.stringify(columnTextColors.value))
  void syncPref('text_colors', columnTextColors.value)
  textColorOpen.value = false
}

function clearTextColor() {
  if (!textColorColumn.value) return
  const next = { ...columnTextColors.value }
  delete next[textColorColumn.value]
  columnTextColors.value = next
  localStorage.setItem(`weida_text_colors:${role.value}`, JSON.stringify(next))
  void syncPref('text_colors', next)
}

function saveRowHeight() {
  localStorage.setItem(`weida_row_height:${role.value}`, String(rowHeight.value))
}

function onHeaderDragend(newWidth: number, _oldWidth: number, column: any) {
  const key = column?.property
  if (key) columnWidths.value = { ...columnWidths.value, [key]: Math.round(newWidth) }
}

function toggleFrozen(name: string) {
  if (frozenColumns.value.includes(name)) {
    frozenColumns.value = frozenColumns.value.filter((n) => n !== name)
  } else {
    frozenColumns.value = [...frozenColumns.value, name]
  }
}

const resizingColumn = ref('')
let resizeStartX = 0
let resizeStartWidth = 0

function startColumnResize(event: MouseEvent, key: string) {
  event.preventDefault()
  event.stopPropagation()
  resizingColumn.value = key
  resizeStartX = event.clientX
  resizeStartWidth = columnWidths.value[key] || 160
  const move = (e: MouseEvent) => {
    if (!resizingColumn.value) return
    const next = Math.max(60, Math.min(600, resizeStartWidth + e.clientX - resizeStartX))
    columnWidths.value = { ...columnWidths.value, [resizingColumn.value]: next }
  }
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    if (resizingColumn.value) saveColumns()
    resizingColumn.value = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

const draggedColumn = ref('')

function startColumnDrag(event: DragEvent, key: string) {
  draggedColumn.value = key
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', key)
  }
}

function dropColumn(event: DragEvent, targetKey: string) {
  event.preventDefault()
  const sourceKey = draggedColumn.value || event.dataTransfer?.getData('text/plain') || ''
  draggedColumn.value = ''
  if (!sourceKey || sourceKey === targetKey) return
  const order = [...columnOrder.value]
  const from = order.indexOf(sourceKey)
  const to = order.indexOf(targetKey)
  if (from === -1 || to === -1) return
  const [item] = order.splice(from, 1)
  order.splice(to, 0, item)
  columnOrder.value = order
  saveColumns()
}

function moveColumn(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= columnOrder.value.length) return
  const next = [...columnOrder.value]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  columnOrder.value = next
}

function removeColumn(index: number) {
  if (columnOrder.value.length <= 1) {
    ElMessage.warning('至少保留一列')
    return
  }
  const name = columnOrder.value[index]
  columnOrder.value.splice(index, 1)
  if (name.startsWith('tpl:')) {
    templateColumns.value = templateColumns.value.filter((t) => t.id !== name)
  }
  if (name.startsWith('blank:')) {
    blankColumns.value = blankColumns.value.filter((b) => b.id !== name)
  }
  frozenColumns.value = frozenColumns.value.filter((n) => n !== name)
}

function appendColumn() {
  if (!addColumn.value) return
  columnOrder.value.push(addColumn.value)
  addColumn.value = ''
}

function addTemplate() {
  const label = tplLabel.value.trim()
  const expression = tplExpr.value.trim()
  if (!label || !expression) return
  if (editingTemplateKey.value) {
    templateColumns.value = templateColumns.value.map((t) =>
      t.id === editingTemplateKey.value ? { ...t, label, expression } : t
    )
    tplLabel.value = ''
    tplExpr.value = ''
    editingTemplateKey.value = ''
    return
  }
  const id = `tpl:${Date.now()}`
  templateColumns.value = [...templateColumns.value, { id, label, expression }]
  columnOrder.value.push(id)
  tplLabel.value = ''
  tplExpr.value = ''
}

function editTemplate(key: string) {
  const tpl = templateColumns.value.find((t) => t.id === key)
  if (!tpl) return
  editingTemplateKey.value = key
  tplLabel.value = tpl.label
  tplExpr.value = tpl.expression
}

function cancelEditTemplate() {
  editingTemplateKey.value = ''
  tplLabel.value = ''
  tplExpr.value = ''
}

function addBlank() {
  const label = blankLabel.value.trim()
  if (!label) return
  const id = `blank:${Date.now()}`
  blankColumns.value = [...blankColumns.value, { id, label }]
  columnOrder.value.push(id)
  blankLabel.value = ''
}

async function openBind() {
  bindOpen.value = true
  await loadBind()
}

function confirmDiscard(message: string): Promise<boolean> {
  return ElMessageBox.confirm(message, '未保存的修改', {
    type: 'warning',
    confirmButtonText: '退出丢失',
    cancelButtonText: '留下继续编辑',
    distinguishCancelAndClose: true,
  })
    .then(() => true)
    .catch(() => false)
}

async function beforeCloseDetail(done: () => void) {
  if (JSON.stringify(editModel) === detailSnapshot.value) {
    done()
    return
  }
  if (await confirmDiscard('当前有未保存的修改，退出后这些内容会丢失。')) done()
}

async function beforeCloseFee(done: () => void) {
  if (JSON.stringify(feeForm) === feeSnapshot.value) {
    done()
    return
  }
  if (await confirmDiscard('费用明细还没保存，退出后填写内容会丢失。')) done()
}

async function beforeCloseStaffAccount(done: () => void) {
  if (!staffAccountForm.username && !staffAccountForm.password) {
    done()
    return
  }
  if (await confirmDiscard('账号/密码还没提交，退出后填写内容会丢失。')) done()
}

async function beforeCloseStaffEdit(done: () => void) {
  const empty = !staffEditForm.name && !staffEditForm.mobile && !staffEditForm.email
  if (empty) {
    done()
    return
  }
  if (await confirmDiscard('人员信息还没保存，退出后填写内容会丢失。')) done()
}

async function loadStaffOptions() {
  try {
    const res = await request<any>({ url: '/weida/staff-options', method: 'get' })
    staffOptions.value = res.data?.data ?? []
  } catch {
    staffOptions.value = []
  }
}

function applyDefaultResponsible() {
  const current = config.value?.current_staff
  if (current && !editModel.opportunity_person_in_charge) {
    // 有妙搭 id 用 id 形式；没有就是本地账号 → 用姓名（人员下拉按名字）
    editModel.opportunity_person_in_charge = current.suda_user_id
      ? `(${current.suda_user_id})`
      : current.name || ''
  }
  if (editModel.responsible_route) return
  const wanted = ['kevin', 'fairy']
  const values = wanted
    .map((key) => staffOptions.value.find((p) => p.name.toLowerCase().includes(key)))
    .filter(Boolean)
    .map((p: any) => p.suda_user_id || p.name)
  if (values.length) editModel.responsible_route = `{(${values.join('),(')})}`
}

async function loadBind() {
  try {
    const res = await request<any>({ url: '/weida/admin/staff', method: 'get' })
    bindItems.value = res.data?.data?.items ?? []
    bindUsers.value = res.data?.data?.users ?? []
  } catch (e: any) {
    ElMessage.error(e?.message || '读取人员绑定失败')
  }
}

function openAccount(row: Record<string, any>) {
  staffAccountMode.value = 'create'
  staffAccountTarget.value = row
  staffAccountForm.username = ''
  staffAccountForm.password = ''
  staffAccountOpen.value = true
}

function openReset(row: Record<string, any>) {
  staffAccountMode.value = 'reset'
  staffAccountTarget.value = row
  staffAccountForm.username = row.username || ''
  staffAccountForm.password = ''
  staffAccountOpen.value = true
}

async function submitStaffAccount() {
  const target = staffAccountTarget.value
  if (!target) return
  if (staffAccountMode.value === 'create' && staffAccountForm.username.trim().length < 3) {
    ElMessage.warning('登录账号至少 3 个字符')
    return
  }
  if (staffAccountForm.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  staffSaving.value = true
  try {
    if (staffAccountMode.value === 'create') {
      await request<any>({
        url: `/weida/admin/staff/${target.id}/account`,
        method: 'post',
        data: {
          username: staffAccountForm.username.trim(),
          password: staffAccountForm.password,
        },
      })
    } else {
      await request<any>({
        url: `/weida/admin/staff/${target.id}/password`,
        method: 'post',
        data: { password: staffAccountForm.password },
      })
    }
    ElMessage.success(staffAccountMode.value === 'create' ? '账号已开通' : '密码已重置')
    staffAccountOpen.value = false
    await loadBind()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '操作失败')
  } finally {
    staffSaving.value = false
  }
}

function openStaffEdit(row?: Record<string, any>) {
  staffEditForm.id = row?.id || 0
  staffEditForm.name = row?.name || ''
  staffEditForm.role = row?.role || 'customer_service'
  staffEditForm.roles = row ? normalizeRoles(row) : ['customer_service']
  staffEditForm.mobile = row?.mobile || ''
  staffEditForm.email = row?.email || ''
  staffEditForm.active = row ? !!row.active : true
  staffEditOpen.value = true
}

async function saveStaff() {
  if (!staffEditForm.name.trim() || !staffEditForm.roles.length) {
    ElMessage.warning('姓名和身份必填')
    return
  }
  staffSaving.value = true
  try {
    if (staffEditForm.id) {
      await request<any>({
        url: `/weida/admin/staff/${staffEditForm.id}`,
        method: 'patch',
        data: {
          name: staffEditForm.name.trim(),
          role: staffEditForm.roles[0],
          roles: staffEditForm.roles,
          mobile: staffEditForm.mobile || null,
          email: staffEditForm.email || null,
          active: staffEditForm.active ? 1 : 0,
        },
      })
    } else {
      await request<any>({
        url: '/weida/admin/staff',
        method: 'post',
        data: {
          name: staffEditForm.name.trim(),
          role: staffEditForm.roles[0],
          roles: staffEditForm.roles,
        },
      })
    }
    ElMessage.success('已保存')
    staffEditOpen.value = false
    await loadBind()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    staffSaving.value = false
  }
}

function paintOps(key: string) {
  const c = displayColumns.value.find((x) => x.key === key)
  if (c?.kind === 'field') return opsFor(c.column)
  return TEXT_OPS
}

function addPaintRule() {
  paintDraft.value.push({
    id: `${Date.now()}-${Math.random()}`,
    scope: 'cell',
    column: displayColumns.value[0]?.key || '',
    op: 'contains',
    value: '',
    color: '#fde68a',
    textColor: '',
    bold: false,
    italic: false,
    fontSize: undefined,
  })
}

function movePaint(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= paintDraft.value.length) return
  const next = [...paintDraft.value]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  paintDraft.value = next
}

function ruleValue(row: Row, key: string): any {
  const c = displayColumns.value.find((x) => x.key === key)
  if (!c) return ''
  if (c.kind === 'field') return cellValue(row, c.column)
  if (c.kind === 'template') return renderTemplate(c.expression, row)
  return blankValues.value[`${row.id}:${c.key}`] || ''
}

function rowTintStyle({ row }: { row: Row }) {
  // 客服那一屏整行罩色只看「是否拿到货」（妙搭 rowTint：否=未拿到货、是=已拿到货、
  // 没填不上色）；六档单票状态只出现在状态格里，不上行底色。
  // 国内操作 / 航线价格的整行色调走各自的状态列。
  const status =
    role.value === 'customer_service'
      ? (() => {
          const got = String(row.fields.is_goods_received?.value ?? '').trim()
          if (got === '否') return '未拿到货'
          if (got === '是') return '已拿到货'
          return ''
        })()
      : String(row.fields.single_status?.value || '')
  const color = STATUS_TINTS[status]
  return color ? { backgroundColor: color } : {}
}

async function runAlertAction(row: Row, alert: NonNullable<Row['alerts']>[number]) {
  const action = alert.action
  if (!action) return
  try {
    if (action.table === 'extrafee') {
      await request({
        url: `/weida/extrafees/${action.record_id}`,
        method: 'patch',
        data: { extra_fee_entry: true },
      })
      ElMessage.success('已重新提交录入')
      await load()
      return
    }
    if (action.table === 'ifreight') {
      await request({
        url: `/weida/domestic_ops/records/${row.id}/related/ifreight/${action.record_id}`,
        method: 'patch',
        data: { fields: { [action.field]: 1 } },
      })
      ElMessage.success('已重新提交录入')
      await load()
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function markBillConfirmed(row: Row) {
  try {
    await request({
      url: `/weida/domestic_ops/records/${row.id}`,
      method: 'patch',
      data: { bill_confirm_time: new Date().toISOString() },
    })
    ElMessage.success('已标记客人确认账单')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

function flightStatusClass(value: string) {
  const text = String(value || '').toLowerCase()
  if (text.includes('not found') || text.includes('未找到')) return 'is-danger'
  if (text.includes('book')) return 'is-info'
  return ''
}

function canClickStatus(row: Row) {
  return (
    role.value === 'customer_service' &&
    row.fields.single_status?.value === '未发送航线询价' &&
    !row.fields.send_inquiry?.value
  )
}

async function onStatusClick(row: Row) {
  if (!canClickStatus(row)) return
  try {
    await request({
      url: `/weida/customer_service/records/${row.id}`,
      method: 'patch',
      data: {
        fields: { send_inquiry: 1 },
        expected_update_time: row.fields.update_time?.value || undefined,
      },
    })
    row.fields.send_inquiry = { label: '发送询价', value: 1 }
    row.fields.single_status = { label: '单票状态', value: '发送航线询价' }
    ElMessage.success('已标记为发送航线询价')
    await load(true)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

function statusPillStyle(value: string) {
  // 航线价格：只有「未报价」上色（妙搭），其余三档是中性字
  if (role.value === 'pricing' && value !== '未报价') {
    return { backgroundColor: 'transparent', border: '1px solid #ddd7ca', color: '#6b655c' }
  }
  const color = STATUS_TINTS[value]
  const match = color && /^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/.exec(color)
  const strong = match
    ? `rgba(${match[1]}, ${match[2]}, ${match[3]}, ${Math.min(1, Number(match[4]) * 2)})`
    : color
  return {
    backgroundColor: strong && strong !== 'transparent' ? strong : '#f1f0ec',
    border: `1px solid ${strong && strong !== 'transparent' ? strong : '#ddd7ca'}`,
  }
}

function paintMatch(rule: PaintRule, row: Row): boolean {
  if (rule.scope === 'column') return true
  const value = ruleValue(row, rule.column)
  if (rule.op === 'is_empty') return isEmpty(value)
  if (rule.op === 'is_not_empty') return !isEmpty(value)
  if (rule.op === 'eq') return String(value ?? '') === String(rule.value ?? '')
  if (rule.op === 'ne') return String(value ?? '') !== String(rule.value ?? '')
  if (rule.op === 'contains') return String(value ?? '').includes(String(rule.value ?? ''))
  if (rule.op === 'gt') return Number(value) > Number(rule.value)
  if (rule.op === 'lt') return Number(value) < Number(rule.value)
  return false
}

function rowTint(row: Row): string | undefined {
  const status = String(row.fields.single_status?.value || '')
  if (role.value === 'customer_service') {
    const got = String(row.fields.is_goods_received?.value ?? '').trim()
    if (got === '否') return STATUS_TINTS['未拿到货']
    if (got === '是') return STATUS_TINTS['已拿到货']
    return undefined
  }
  if (role.value === 'domestic_ops') return status === '已完成' ? STATUS_TINTS['已完成'] : undefined
  if (role.value === 'pricing') {
    if (status === '未报价') return STATUS_TINTS['未报价']
    if (status === '已报价') return STATUS_TINTS['已报价']
  }
  return undefined
}

function paintCellStyle({ row, column }: { row: Row; column: any }) {
  const key = String(column?.property || '')
  if (!key) return {}
  const statusColor = rowTint(row)
  for (const rule of paintRules.value) {
    if (rule.scope === 'column') {
      if (rule.column !== key) continue
    } else if (rule.scope === 'cell' && rule.column !== key) {
      continue
    } else if (!paintMatch(rule, row)) {
      continue
    }
    const style: Record<string, any> = {}
    if (rule.color) style.backgroundColor = rule.color
    if (rule.textColor) style.color = rule.textColor
    if (rule.bold) style.fontWeight = 'bold'
    if (rule.italic) style.fontStyle = 'italic'
    if (rule.fontSize) style.fontSize = `${rule.fontSize}px`
    if (!style.textColor && columnTextColors.value[key]) style.color = columnTextColors.value[key]
    if (!style.backgroundColor && statusColor) style.backgroundColor = statusColor
    if (Object.keys(style).length) return style
  }
  const base: Record<string, any> = {}
  if (statusColor) base.backgroundColor = statusColor
  if (columnTextColors.value[key]) base.color = columnTextColors.value[key]
  return base
}

const INLINE_READONLY = new Set([
  'single_status',
  'goods_data',
  'final_plan',
  'bill_status',
  'opportunity_no',
  'plan_no',
  'master_no', // 查找引用：改的是关联订单，不能在商机行里就地写
  'create_time',
  'update_time',
  'id',
])
// 人 / 客商 字段在列表里也用选择器，不是自由输入
const inlinePersonColumns = new Set([
  'opportunity_person_in_charge',
  'domestic_operation',
  'responsible_route',
  'sales_person',
])
const inlineVendorColumns = new Set(['customer_name', 'supplier', 'foreign_agent_name'])

function setInlineDate(value: any, format: string, text?: string) {
  inlineEditValue.value = text !== undefined ? text : value ? value.format(format) : ''
  void saveInlineEdit()
}

function onCellClick(row: Row, column: any) {
  const key = String(column?.property || '')
  if (!key) return
  if (inlineEdit.value && inlineEdit.value.rowId === row.id && inlineEdit.value.key === key) return
  if (inlineEdit.value) void saveInlineEdit()
  const c = displayColumns.value.find((x) => x.key === key)
  if (!c || c.kind === 'blank') return
  if (c.kind === 'field' && c.column === 'goods_data' && role.value === 'domestic_ops') {
    void openGoodsEditor(row)
    return
  }
  if (c.kind === 'field' && (INLINE_READONLY.has(c.column) || c.virtual)) return
  const original =
    c.kind === 'field' ? String(cellValue(row, c.column) ?? '') : renderTemplate(c.expression, row)
  inlineEdit.value = {
    rowId: row.id,
    key,
    kind: c.kind,
    column: c.kind === 'field' ? c.column : undefined,
    expression: c.kind === 'template' ? c.expression : undefined,
    original,
    type: c.kind === 'field' ? c.type : undefined,
    options: c.kind === 'field' ? c.options : undefined,
  }
  inlineEditValue.value = original
  inlineCalcVolume.value = null
  nextTick(() => {
    const input = document.querySelector('.wb-inline-edit input, .wb-inline-edit textarea') as
      | HTMLInputElement
      | HTMLTextAreaElement
      | null
    input?.focus()
    input?.select?.()
  })
}

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function parseTemplateValues(expression: string, text: string): Record<string, string> | null {
  const fieldRe = /\{([^{}]+)\}/g
  const names: string[] = []
  let pattern = '^'
  let last = 0
  let match: RegExpExecArray | null
  while ((match = fieldRe.exec(expression))) {
    pattern += escapeRegExp(expression.slice(last, match.index))
    pattern += '(.*?)'
    names.push(match[1].trim())
    last = match.index + match[0].length
  }
  pattern += escapeRegExp(expression.slice(last)) + '$'
  const parsed = text.match(new RegExp(pattern, 's'))
  if (!parsed) return null
  const out: Record<string, string> = {}
  names.forEach((name, index) => {
    const column = labelToColumn.value.get(name) || name
    out[column] = (parsed[index + 1] || '').trim()
  })
  return out
}

function calcCreateVolume() {
  const volume = calcVolumeFromSize(String(editModel.size_length_width_height_quantity || ''))
  if (!volume) {
    ElMessage.warning('尺寸格式无法识别，请输入如 120*100*60*2')
    return
  }
  editModel.volume_m3 = formatDecimal(String(volume), 4)
  ElMessage.success(`已计算体积 ${formatDecimal(String(volume), 4)} m³`)
}

function calcInlineVolume() {
  const volume = calcVolumeFromSize(inlineEditValue.value)
  if (!volume) {
    ElMessage.warning('尺寸格式无法识别')
    return
  }
  inlineCalcVolume.value = volume
  ElMessage.success(`体积 ${formatDecimal(String(volume), 4)} m³，保存时一并写入`)
}

async function saveInlineEdit() {
  const editing = inlineEdit.value
  if (!editing || inlineSaving.value) return
  const value = inlineEditValue.value
  if (value === editing.original) {
    if (inlineEdit.value === editing) inlineEdit.value = null
    return
  }
  const row = rows.value.find((r) => r.id === editing.rowId)
  if (!row) {
    if (inlineEdit.value === editing) inlineEdit.value = null
    return
  }
  let fields: Record<string, any> = {}
  if (editing.kind === 'field' && editing.column) {
    fields = { [editing.column]: value }
    if (editing.column === 'size_length_width_height_quantity' && inlineCalcVolume.value) {
      fields.volume_m3 = formatDecimal(String(inlineCalcVolume.value), 4)
    }
  } else if (editing.kind === 'template' && editing.expression) {
    const parsed = parseTemplateValues(editing.expression, value)
    if (!parsed || !Object.keys(parsed).length) {
      ElMessage.warning('拼接内容与列格式不匹配，未保存')
      return
    }
    fields = parsed
  }
  inlineSaving.value = true
  try {
    const res = await request<any>({
      url: `/weida/${role.value}/records/${row.id}`,
      method: 'patch',
      data: {
        fields,
        expected_update_time: row.fields.update_time?.value || undefined,
      },
    })
    const updated: Row = res.data?.data
    if (updated) {
      const idx = rows.value.findIndex((r) => r.id === updated.id)
      if (idx >= 0) rows.value[idx] = updated
      if (detail.value?.id === updated.id) detail.value = updated
    }
    if (inlineEdit.value === editing) inlineEdit.value = null
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    inlineSaving.value = false
  }
}

function savePaintRules() {
  paintRules.value = paintDraft.value.map((r) => ({ ...r }))
  localStorage.setItem(`weida_paint_rules:${role.value}`, JSON.stringify(paintRules.value))
  syncPref('paint', paintRules.value)
  paintOpen.value = false
}

function insertTemplateField(label: string) {
  if (!label) return
  tplExpr.value = `${tplExpr.value}${tplExpr.value ? ' ' : ''}{${label}}`
  templateFieldPick.value = ''
}

function insertTemplateSeparator(sep: string) {
  tplExpr.value = `${tplExpr.value}${sep}`
}

watch(
  paintOpen,
  (open) => {
    if (open) paintDraft.value = paintRules.value.map((r) => ({ ...r }))
  }
)

watch(
  blankValues,
  (value) => {
    localStorage.setItem(`weida_blank_values:${role.value}`, JSON.stringify(value))
    syncPref('blank', value)
  },
  { deep: true }
)

async function importMiaodaConfig(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const parsed = JSON.parse(await file.text())
    const entries = (Object.entries(parsed || {}) as Array<[string, any]>).map(([key, value]) => {
      let parsedValue = value
      if (typeof value === 'string') {
        try {
          parsedValue = JSON.parse(value)
        } catch {
          parsedValue = value
        }
      }
      return [key, parsedValue] as [string, any]
    })
    let mine = entries.find(([key]) => key.endsWith(`:${role.value}`))?.[1] || parsed
    if (typeof mine === 'string') {
      try {
        mine = JSON.parse(mine)
      } catch {
        mine = null
      }
    }
    if (!mine || !Array.isArray(mine.columns)) {
      ElMessage.warning('这个文件里没有找到当前工作台的配置')
      return
    }
    const labelToColumn = new Map<string, string>()
    for (const c of config.value?.columns || []) labelToColumn.set(c.label, c.column)
    for (const c of config.value?.detail_fields || []) labelToColumn.set(c.label, c.column)
    const order: string[] = []
    for (const c of mine.columns) {
      const column = labelToColumn.get(String(c.name || c.label || '').trim())
      if (column && !order.includes(column)) order.push(column)
    }
    if (!order.length) {
      ElMessage.warning('没有匹配到当前工作台的列')
      return
    }
    const widthSource = mine.columnWidths || {}
    const widths: Record<string, number> = {}
    for (const [name, width] of Object.entries(widthSource)) {
      const column = labelToColumn.get(String(name).trim())
      if (column && Number(width) > 0) widths[column] = Number(width)
    }
    columnOrder.value = order
    columnWidths.value = widths
    frozenColumns.value = []
    saveColumns()
    ElMessage.success(`已导入 ${order.length} 列及列宽`)
  } catch (e: any) {
    ElMessage.error(e?.message || '配置文件解析失败')
  } finally {
    ;(event.target as HTMLInputElement).value = ''
  }
}

function saveColumns() {
  if (!columnOrder.value.length) {
    ElMessage.warning('至少保留一列')
    return
  }
  localStorage.setItem(
    `weida_columns_v11:${role.value}`,
    JSON.stringify({
      order: columnOrder.value,
      frozen: frozenColumns.value,
      templates: templateColumns.value,
      blanks: blankColumns.value,
      blankValues: blankValues.value,
      widths: columnWidths.value,
    })
  )
  syncPref('columns_v6', {
    order: columnOrder.value,
    frozen: frozenColumns.value,
    templates: templateColumns.value,
    blanks: blankColumns.value,
    blankValues: blankValues.value,
    widths: columnWidths.value,
  })
  columnOpen.value = false
}

function resetColumns() {
  columnOrder.value = columns.value.map((c) => c.column)
  frozenColumns.value = []
  templateColumns.value = []
  blankColumns.value = []
  blankValues.value = {}
  columnWidths.value = {}
  tplLabel.value = ''
  tplExpr.value = ''
  editingTemplateKey.value = ''
  blankLabel.value = ''
  localStorage.removeItem(`weida_columns_v11:${role.value}`)
}

function applySearch() {
  page.value = 1
  load()
}

function normalizePorts(value: any): string[] {
  if (value === null || value === undefined) return []
  const raw = Array.isArray(value)
    ? value
    : String(value)
        .replace(/[{}\[\]"]/g, '')
        .split(',')
  const seen = new Set<string>()
  for (const item of raw) {
    const code = String(item).trim().toUpperCase()
    if (code) seen.add(code)
  }
  return [...seen]
}

function detailTabKey() {
  return `weida_detail_tab:${role.value}`
}

function restoreDetailTab() {
  const saved = localStorage.getItem(detailTabKey())
  activeTab.value = saved || '商机详情'
}

function openCreate() {
  void loadVendors()
  void loadFieldOptions()
  void loadStaffOptions().then(applyDefaultResponsible)
  drawerMode.value = 'create'
  detail.value = { id: 0, fields: {} }
  relatedPricing.value = []
  relatedPlans.value = []
  relatedOrders.value = []
  Object.keys(editModel).forEach((k) => delete editModel[k])
  for (const f of detailFields.value) {
    editModel[f.column] = f.column === 'destination_port' ? [] : f.type === 'checkbox' ? 0 : ''
  }
  activeTab.value = '商机详情'
  detailSnapshot.value = JSON.stringify(editModel)
  drawerOpen.value = true
}

async function openDetail(row: Row) {
  markSeen(Number(row.id))
  // 客商下拉（客户/供应商/国外代理/订舱代理）在抽屉里用，进抽屉时确保已加载
  if (!vendorOptions.value.length) void loadVendors()
  drawerMode.value = 'edit'
  try {
    const res = await request<any>({
      url: `/weida/${role.value}/records/${row.id}`,
      method: 'get',
    })
    detail.value = res.data?.data ?? null
    if (!detail.value) return
    Object.keys(editModel).forEach((k) => delete editModel[k])
    if (role.value === 'customer_service' || role.value === 'pricing') {
      await Promise.all([
        loadRelatedPricing(detail.value.id),
        role.value === 'customer_service' ? loadRelatedPlans(detail.value.id) : Promise.resolve(),
        loadRelatedOrders(detail.value.id),
        searchPortOptions(''),
      ])
    }
    if (relatedBlocks.value.length) await loadRelatedBlocks(detail.value.id)
    if (role.value === 'domestic_ops') {
      await Promise.all([
        loadDetailAttachment('确认账单文件'),
        loadDetailAttachment('发票'),
        loadDetailAttachment('头程入货通知'),
        loadDetailAttachment('二程入货通知'),
        void loadIfreightOptions(),
        void loadPromptTemplates(),
        loadDetailAttachment('国内邮件'),
        loadDetailAttachment('国外邮件'),
        loadDetailAttachment('相关资料'),
        loadDetailAttachment('报价单'),
        loadDetailAttachment('订舱单'),
      ])
    }
    for (const c of detailFields.value) {
      if (c.editable) {
        const v = detail.value.fields[c.column]?.value
        if (c.column === 'destination_port') {
          editModel[c.column] = normalizePorts(v)
        } else if (c.type === 'checkbox') {
          editModel[c.column] = v ? 1 : 0
        } else if (c.type === 'json' && v !== null && v !== undefined && v !== '') {
          editModel[c.column] = typeof v === 'string' ? v : JSON.stringify(v, null, 2)
        } else {
          editModel[c.column] = v ?? ''
        }
      }
    }
    detailSnapshot.value = JSON.stringify(editModel)
    weekendChecked.value = Object.entries(detail.value.weekend_check?.items || {})
      .filter(([, v]) => v)
      .map(([k]) => k)
    restoreDetailTab()
    drawerOpen.value = true
    if (role.value === 'domestic_ops') {
      window.setTimeout(reorderDetailTabs, 50)
      window.setTimeout(reorderDetailTabs, 300)
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '读取详情失败')
  }
}

function buildPayload(): Record<string, any> {
  const payload: Record<string, any> = {}
  const createCols = drawerMode.value === 'create' ? new Set(createFieldList.value.map((item) => item.column)) : null
  for (const c of detailFields.value) {
    if (!c.editable) continue
    if (createCols && !createCols.has(c.column)) continue
    const next = editModel[c.column]
    const prev = detail.value?.fields[c.column]?.value
    if (c.column === 'destination_port') {
      const list = normalizePorts(next)
      const prevList = normalizePorts(prev)
      if (JSON.stringify(list) !== JSON.stringify(prevList)) {
        payload[c.column] = list.length ? JSON.stringify(list) : null
      }
      continue
    }
    if (c.type === 'json') {
      if (next === '' || next === null || next === undefined) {
        if (prev !== null && prev !== undefined) payload[c.column] = null
        continue
      }
      if (c.column === 'opportunity_person_in_charge' || c.column === 'responsible_route') {
        if (next !== prev) payload[c.column] = String(next)
        continue
      }
      try {
        JSON.parse(String(next))
      } catch {
        ElMessage.error(`${c.label} 不是合法 JSON`)
        throw new Error('invalid json')
      }
      if (next !== prev) payload[c.column] = String(next)
    } else if (c.type === 'checkbox') {
      payload[c.column] = next ? 1 : 0
    } else if (drawerMode.value === 'create' && (next === '' || next === null || next === undefined)) {
      continue
    } else if (next !== prev) {
      payload[c.column] = next === '' ? null : next
    }
  }
  return payload
}

function blockValue(row: RelatedRow, column: string): any {
  return row.fields[column]?.value
}

function isFeeEditing(row: RelatedRow, column: string) {
  return feeEditing.value?.id === row.id && feeEditing.value.column === column
}

function startFeeEdit(row: RelatedRow, column: string) {
  feeEditing.value = { id: row.id, column }
  feeEditValue.value = blockValue(row, column) ?? ''
}

async function saveFeeEdit(row: RelatedRow) {
  const editing = feeEditing.value
  if (!editing || editing.id !== row.id) return
  const column = editing.column
  let value: any = feeEditValue.value
  if (column === 'amount') {
    value = Number(value)
    if (!Number.isFinite(value)) {
      ElMessage.warning('金额必须是数字')
      return
    }
  } else if (value === '') {
    value = null
  }
  if (!feePlanId.value) return
  try {
    const res = await request<any>({
      url: `/weida/customer_service/plans/${feePlanId.value}/fees/${row.id}`,
      method: 'patch',
      data: { fields: { [column]: value } },
    })
    row.fields = res.data?.data?.fields ?? row.fields
    feeEditing.value = null
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function openFeeDialog(row: RelatedRow) {
  feePlanId.value = row.id
  feePlanLabel.value =
    (row.fields.scheme_no?.value as string) || (row.fields.app_scheme_name?.value as string) || `#${row.id}`
  feeSnapshot.value = JSON.stringify(feeForm)
  feeOpen.value = true
  await Promise.all([loadFees(), loadFeeOptions()])
}

async function loadFeeOptions() {
  try {
    const res = await request<any>({
      url: '/weida/customer_service/planfee-options',
      method: 'get',
    })
    const data = res.data?.data ?? {}
    feeOptions.fee_categories = data.fee_categories ?? []
    feeOptions.groups = data.groups ?? []
    feeOptions.billing_units = data.billing_units ?? []
    feeOptions.currencies = data.currencies ?? []
  } catch {
    // 拉不到就保留空选项；仍可手工输入
  }
}

async function loadFees() {
  if (!feePlanId.value) return
  feeLoading.value = true
  try {
    const res = await request<any>({
      url: `/weida/customer_service/plans/${feePlanId.value}/fees`,
      method: 'get',
    })
    fees.value = res.data?.data?.items ?? []
  } catch {
    fees.value = []
  } finally {
    feeLoading.value = false
  }
}

async function addFee() {
  if (!feePlanId.value) return
  if (!feeForm.fee_category.trim() || !String(feeForm.amount).trim()) {
    ElMessage.warning('费用类别和金额必填')
    return
  }
  try {
    await request<any>({
      url: `/weida/customer_service/plans/${feePlanId.value}/fees`,
      method: 'post',
      data: {
        fields: {
          fee_category: feeForm.fee_category,
          group: feeForm.group || null,
          billing_unit: feeForm.billing_unit || null,
          currency: feeForm.currency || null,
          amount: Number(feeForm.amount),
          remark: feeForm.remark || null,
        },
      },
    })
    feeForm.fee_category = ''
    feeForm.group = ''
    feeForm.billing_unit = ''
    feeForm.amount = ''
    feeForm.remark = ''
    feeSnapshot.value = JSON.stringify(feeForm)
    await loadFees()
    ElMessage.success('已添加')
  } catch (e: any) {
    ElMessage.error(e?.message || '添加失败')
  }
}

async function removeFee(row: RelatedRow) {
  if (!feePlanId.value) return
  try {
    await ElMessageBox.confirm('删除这条费用明细？', '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await request({
      url: `/weida/customer_service/plans/${feePlanId.value}/fees/${row.id}`,
      method: 'delete',
    })
    await loadFees()
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

async function generateQuote(row: RelatedRow) {
  if (quoteBusy.value) return
  quoteBusy.value = row.id
  try {
    await request({
      url: `/weida/customer_service/plans/${row.id}/generate-quote`,
      method: 'post',
    })
    row.fields.generate_quote = { label: '生成报价单', value: 1 }
    await loadPlanAttachments()
    ElMessage.success('已生成报价单')
  } catch (e: any) {
    ElMessage.error(e?.message || '生成报价单失败')
  } finally {
    quoteBusy.value = null
  }
}

async function loadRelatedPricing(opportunityId: number) {
  try {
    const res = await request<any>({
      url: `/weida/customer_service/${opportunityId}/pricing`,
      method: 'get',
    })
    relatedPricing.value = res.data?.data?.items ?? []
  } catch {
    relatedPricing.value = []
  }
}

async function loadPlanAttachments() {
  const plans = relatedPlans.value
  await Promise.all(
    plans.map(async (p) => {
      try {
        const res = await request<any>({
          url: `/weida/customer_service/plans/${p.id}/attachments`,
          method: 'get',
          params: { field: '报价单' },
        })
        planAttachments[p.id] = res.data?.data?.items ?? []
      } catch {
        planAttachments[p.id] = []
      }
    })
  )
}

async function previewAttachment(a: AttachmentItem) {
  const res = await fetch(`/api/v1/weida/attachments/${a.id}/preview`, {
    headers: { Authorization: `Bearer ${Auth.getAccessToken()}` },
  })
  if (!res.ok) {
    ElMessage.error('预览失败')
    return
  }
  const blob = await res.blob()
  window.open(URL.createObjectURL(blob))
}

async function changeBillStatus(row: Row, value: any) {
  const step = value && value !== '__auto' ? Number(value) : null
  try {
    await request({
      url: `/weida/domestic_ops/orders/${row.id}/bill-status`,
      method: 'patch',
      data: { step },
    })
    row.fields.bill_status = {
      label: '账单状态',
      value: step ? billStatusOptions[step - 1] : '',
      step,
    } as any
    ElMessage.success(step ? '账单状态已更新' : '已恢复自动推导')
  } catch (e: any) {
    ElMessage.error(e?.message || '更新账单状态失败')
  }
}

async function loadNotifications() {
  try {
    const res = await request<any>({
      url: '/weida/notifications',
      method: 'get',
      params: { role: role.value },
    })
    notifications.value = res.data?.data?.items ?? []
    unreadCount.value = Number(res.data?.data?.unread || 0)
  } catch {
    notifications.value = []
    unreadCount.value = 0
  }
}

function alertKey(orderId: number, alert: any) {
  return `${orderId}|${alert?.title || ''}|${alert?.detail || ''}`
}

function dismissedAlerts(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem('weida_alert_dismissed_v1') || '{}')
  } catch {
    return {}
  }
}

function isAlertDismissed(orderId: number, alert: any) {
  return Boolean(dismissedAlerts()[alertKey(orderId, alert)])
}

/** 屏幕上该显示的提醒：去掉本机已取消的（硬刷新同样生效）。 */
function activeAlerts(record: Row | null | undefined): any[] {
  if (!record) return []
  return (record.alerts || []).filter((a: any) => !isAlertDismissed(Number(record.id), a))
}

function dismissAlert(record: Row, alert: any) {
  const all = dismissedAlerts()
  // 只保留最近 30 天的取消记录，避免 localStorage 越积越多
  const cutoff = Date.now() - 30 * 86_400_000
  for (const [key, at] of Object.entries(all)) {
    if (Number(at) < cutoff) delete all[key]
  }
  all[alertKey(Number(record.id), alert)] = Date.now()
  localStorage.setItem('weida_alert_dismissed_v1', JSON.stringify(all))
  record.alerts = (record.alerts || []).filter((a: any) => alertKey(Number(record.id), a) !== alertKey(Number(record.id), alert))
  ElMessage.success('已取消这条提醒')
}

async function openNotifications() {
  notifOpen.value = true
  await loadNotifications()
  if (unreadCount.value > 0) {
    try {
      await request({ url: '/weida/notifications/read', method: 'post', params: { role: role.value } })
      unreadCount.value = 0
    } catch {
      // ignore
    }
  }
}

async function openGoodsEditor(row: Row) {
  goodsOrderId.value = Number(row.id)
  goodsRows.value = []
  goodsOpen.value = true
  try {
    const res = await request<any>({
      url: `/weida/domestic_ops/records/${row.id}/related/ifreight`,
      method: 'get',
    })
    goodsRows.value = (res.data?.data?.items ?? []).map((item: any) => ({
      id: item.id,
      hawb: item.fields?.house_bl_no?.value || '',
      quantity: item.fields?.quantity?.value ?? '',
      gross_weight: item.fields?.gross_weight?.value ?? '',
      volume: item.fields?.volume?.value ?? '',
      dimension: item.fields?.dimension?.value ?? '',
    }))
  } catch (e: any) {
    ElMessage.error(e?.message || '加载分单数据失败')
  }
}

async function saveGoodsEditor() {
  if (!goodsOrderId.value || goodsBusy.value) return
  goodsBusy.value = true
  try {
    for (const g of goodsRows.value) {
      await request({
        url: `/weida/domestic_ops/records/${goodsOrderId.value}/related/ifreight/${g.id}`,
        method: 'patch',
        data: {
          fields: {
            quantity: g.quantity,
            gross_weight: g.gross_weight,
            volume: g.volume,
            dimension: g.dimension,
          },
        },
      })
    }
    goodsOpen.value = false
    ElMessage.success('货物数据已保存')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    goodsBusy.value = false
  }
}

async function saveAiFee(row: Record<string, any>, field: string, value: string) {
  try {
    await request({
      url: `/weida/ai-fees/${row.id}`,
      method: 'patch',
      data: { [field]: value },
    })
    if (field === 'quantity' || field === 'unit_price') {
      row[field] = value === '' ? null : Number(value)
    } else {
      row[field] = value
    }
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
    await loadAiFees(detail.value?.id || 0)
  }
}

async function changeMyPassword() {
  if (pwdForm.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  if (pwdForm.new_password !== pwdForm.confirm) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  pwdSaving.value = true
  try {
    await request({
      url: '/weida/me/password',
      method: 'post',
      data: { old_password: pwdForm.old_password, new_password: pwdForm.new_password },
    })
    ElMessage.success('密码已修改')
    pwdOpen.value = false
    pwdForm.old_password = ''
    pwdForm.new_password = ''
    pwdForm.confirm = ''
  } catch (e: any) {
    ElMessage.error(e?.message || '修改失败')
  } finally {
    pwdSaving.value = false
  }
}

async function saveHandover() {
  if (!detail.value || !handoverTo.value) return
  try {
    await request({
      url: `/weida/domestic_ops/orders/${detail.value.id}/handover`,
      method: 'post',
      data: { to_name: handoverTo.value },
    })
    handoverOpen.value = false
    ElMessage.success('已周末交接')
    await openDetail({ id: detail.value.id, fields: {} })
  } catch (e: any) {
    ElMessage.error(e?.message || '交接失败')
  }
}

async function refreshDetailQuietly() {
  if (!detail.value || drawerMode.value !== 'edit') return
  // 有没保存的改动就不刷新，免得把输入冲掉
  if (detailSnapshot.value && JSON.stringify(editModel) !== detailSnapshot.value) return
  try {
    const res = await request<any>({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'get',
    })
    if (res.data?.data) detail.value = res.data.data
  } catch {
    /* 静默：下一轮再试 */
  }
}

async function refreshDetail() {
  if (!detail.value) return
  await openDetail({ id: detail.value.id, fields: {} })
}

async function cancelHandover() {
  if (!detail.value) return
  try {
    await request({
      url: `/weida/domestic_ops/orders/${detail.value.id}/handover`,
      method: 'delete',
    })
    ElMessage.success('已撤回交接')
    await openDetail({ id: detail.value.id, fields: {} })
  } catch (e: any) {
    ElMessage.error(e?.message || '撤回失败')
  }
}

function openWeekend(row: Row) {
  weekendOrderId.value = Number(row.id)
  weekendChecked.value = Object.entries(row.weekend_check?.items || {})
    .filter(([, v]) => v)
    .map(([k]) => k)
  weekendOpen.value = true
}

async function saveWeekendCheck() {
  const orderId = weekendOrderId.value || detail.value?.id
  if (!orderId) return
  const items = {
    insured: weekendChecked.value.includes('insured'),
    declDocs: weekendChecked.value.includes('declDocs'),
    declChecked: weekendChecked.value.includes('declChecked'),
    clearDocs: weekendChecked.value.includes('clearDocs'),
    customerContact: weekendChecked.value.includes('customerContact'),
  }
  try {
    await request({
      url: `/weida/domestic_ops/orders/${orderId}/checks`,
      method: 'post',
      data: { kind: 'weekend', items, confirmed: Object.values(items).every(Boolean) },
    })
    if (detail.value) detail.value.weekend_check = { pending: !Object.values(items).every(Boolean), items }
    weekendOpen.value = false
    await load()
    ElMessage.success('已保存确认')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}


async function markFlightSeen() {
  if (!detail.value) return
  await request({
    url: `/weida/domestic_ops/orders/${detail.value.id}/flight-seen`,
    method: 'post',
  }).catch(() => undefined)
}

async function addExtraFeeManual() {
  if (!detail.value) return
  try {
    await request({
      url: `/weida/domestic_ops/orders/${detail.value.id}/extrafees`,
      method: 'post',
      data: {
        ifreight_id: feeAdd.ifreight_id || relatedData.ifreight?.[0]?.id || null,
        domestic_abroad: feeAdd.domestic_abroad,
        fee_category: feeAdd.fee_category,
        currency: feeAdd.currency,
        billing_unit: feeAdd.billing_unit,
        app_quantity: Number(feeAdd.app_quantity) || 0,
        unit_price: Number(feeAdd.unit_price) || 0,
        tax_point: feeAdd.tax_point || undefined,
        tax_calc_type: feeAdd.tax_calc_type || undefined,
        fee_entry_status: feeAdd.fee_entry_status || undefined,
      },
    })
    feeAddOpen.value = false
    await loadAiFees(detail.value.id)
    ElMessage.success('已添加费用')
  } catch (e: any) {
    ElMessage.error(e?.message || '添加失败')
  }
}

async function applyPremium() {
  if (!detail.value) return
  try {
    await request({
      url: `/weida/domestic_ops/orders/${detail.value.id}/extrafees`,
      method: 'post',
      data: {
        domestic_abroad: '国内',
        fee_category: '保费',
        currency: premiumCurrency.value,
        billing_unit: 'PER SHIPMENT',
        app_quantity: 1,
        unit_price: Number(premiumResult.value.toFixed(2)),
      },
    })
    await loadAiFees(detail.value.id)
    ElMessage.success('保费已带入额外费用')
  } catch (e: any) {
    ElMessage.error(e?.message || '带入失败')
  }
}

async function saveExtraFee(row: Record<string, any>, field: string, value: string) {
  try {
    await request({
      url: `/weida/extrafees/${row.id}`,
      method: 'patch',
      data: { [field]: value },
    })
    await loadAiFees(detail.value?.id || 0)
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function removeExtraFee(row: Record<string, any>) {
  try {
    await ElMessageBox.confirm('确定删除这条做账费用？', '删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await request({ url: `/weida/extrafees/${row.id}`, method: 'delete' })
    await loadAiFees(detail.value?.id || 0)
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

async function confirmAiFees() {
  if (!detail.value || aiFeesBusy.value) return
  aiFeesBusy.value = true
  try {
    const res = await request<any>({
      url: `/weida/domestic_ops/orders/${detail.value.id}/ai-fees/confirm`,
      method: 'post',
    })
    ElMessage.success(`已确认 ${res.data?.data?.updated ?? 0} 条费用行`)
    await loadAiFees(detail.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '确认失败')
  } finally {
    aiFeesBusy.value = false
  }
}

async function loadAiFees(orderId: number) {
  aiFeesLoading.value = true
  try {
    const [aiRes, feeRes] = await Promise.all([
      request<any>({ url: `/weida/domestic_ops/orders/${orderId}/ai-fees`, method: 'get' }),
      request<any>({ url: `/weida/domestic_ops/orders/${orderId}/extrafees`, method: 'get' }),
    ])
    aiFees.value = aiRes.data?.data?.items ?? []
    extraFees.value = feeRes.data?.data?.items ?? []
    extraTotals.value = feeRes.data?.data?.totals ?? {}
  } catch {
    aiFees.value = []
    extraFees.value = []
  } finally {
    aiFeesLoading.value = false
  }
}

async function loadArrivalAttachments(orderId: number) {
  try {
    const res = await request<any>({
      url: `/weida/customer_service/orders/${orderId}/attachments`,
      method: 'get',
      params: { field: '到货预报表' },
    })
    arrivalAttachments.value = res.data?.data?.items ?? []
  } catch {
    arrivalAttachments.value = []
  }
}

async function runAiRecognize(row: RelatedRow, kind: 'foreign' | 'domestic' = 'foreign') {
  if (!row.id || aiBusy.value) return
  aiBusy.value = row.id
  try {
    const res = await request<any>({
      url: `/weida/ifreight/${row.id}/ai-recognize`,
      method: 'post',
      params: { kind },
    })
    const count = res.data?.data?.fees ?? 0
    if (kind === 'foreign') {
      row.fields.foreign_bill_ai_recog = { label: '国外账单 AI 识别', value: 1 }
    } else {
      row.fields.bill_ai_recognize = { label: '账单 AI 识别', value: 1 }
    }
    ElMessage.success(`AI 识别完成，提取 ${count} 条费用行`)
  } catch (e: any) {
    ElMessage.error(e?.message || 'AI 识别失败')
  } finally {
    aiBusy.value = null
  }
}

async function downloadArrivalReport() {
  const orderId = detail.value?.id
  if (!orderId || arrivalBusy.value) return
  arrivalBusy.value = true
  try {
    const res = await fetch(`/api/v1/weida/domestic_ops/orders/${orderId}/arrival-report`, {
      headers: { Authorization: `Bearer ${Auth.getAccessToken()}` },
    })
    if (!res.ok) throw new Error('生成失败')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `到货预报表-${orderId}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
    await loadArrivalAttachments(orderId)
  } catch (e: any) {
    ElMessage.error(e?.message || '生成到货预报表失败')
  } finally {
    arrivalBusy.value = false
  }
}

async function downloadAttachment(a: AttachmentItem) {
  const res = await fetch(`/api/v1/weida/attachments/${a.id}/download`, {
    headers: { Authorization: `Bearer ${Auth.getAccessToken()}` },
  })
  if (!res.ok) {
    ElMessage.error('下载失败')
    return
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = a.file_name
  link.click()
  URL.revokeObjectURL(url)
}

async function uploadAttachment(row: RelatedRow, options: any) {
  const form = new FormData()
  form.append('file', options.file)
  form.append('field', '报价单')
  try {
    await request({
      url: `/weida/customer_service/plans/${row.id}/attachments`,
      method: 'post',
      data: form,
    })
    await loadPlanAttachments()
    options.onSuccess?.({})
    ElMessage.success('已上传')
  } catch (e: any) {
    options.onError?.(e)
    ElMessage.error(e?.message || '上传失败')
  }
}

async function removeAttachment(a: AttachmentItem) {
  try {
    await ElMessageBox.confirm(`删除附件「${a.file_name}」？`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await request({
      url: `/weida/attachments/${a.id}`,
      method: 'delete',
    })
    await loadPlanAttachments()
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

function relatedColumnEditable(rb: RelatedBlock, column: string): boolean {
  // 查找引用列只能看；车队表只放开车队体积/比例（妙搭这两列才可写）
  if (column === 'master_no') return false
  if (rb.key === 'fleet') return ['fleet_volume', 'fleet_ratio'].includes(column)
  return true
}

function blockColumns(block: LinkBlock): Col[] {
  // 交接操作：提货车队/提货信息/提货费 只在「是否需要提货=是」时出现（妙搭 visibility）
  if (block.key !== 'handover') return block.columns
  const pickup = String(detail.value?.fields.need_pickup?.value ?? '').trim() === '是'
  if (pickup) return block.columns
  return block.columns.filter(
    (c) => !['pickup_fleet', 'pickup_info', 'pickup_fee'].includes(c.column),
  )
}

function detailGroups(section: { label: string; fields: Col[] }) {
  if (section.label !== '提货计划') return [{ label: '', fields: section.fields }]
  // 妙搭：分单号/货物数据/货物品名/起运地 四项独立摆在上方，头程 9 项、二程 7 项各成一块
  const head = new Set(['house_bl_no', 'goods_data', 'goods_name_special_attr', 'departure_location'])
  return [
    { label: '', fields: section.fields.filter((f) => head.has(f.column)) },
    {
      label: '头程',
      fields: section.fields.filter((f) => !head.has(f.column) && !f.column.startsWith('second_')),
    },
    { label: '二程', fields: section.fields.filter((f) => !head.has(f.column) && f.column.startsWith('second_')) },
  ]
}

function reorderDetailTabs() {
  const list = document.querySelector('.wb-tabs .ant-tabs-nav-list')
  if (!list) return
  const order = [
    '货物详情',
    '提货计划',
    '车队',
    '报关',
    '代理到货计划',
    '国外预报',
    '到货预报表',
    '投保',
    '做账明细',
    '开票',
    '航班查询',
    'iFreight 数据',
    'AI费用',
  ]
  const items = Array.from(list.querySelectorAll('.ant-tabs-tab')) as HTMLElement[]
  items.sort((a, b) => {
    const ai = order.indexOf(a.innerText.trim())
    const bi = order.indexOf(b.innerText.trim())
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })
  for (const item of items) list.appendChild(item)
}

function onDetailTabChange(name: string | number) {
  localStorage.setItem(detailTabKey(), String(name))
  if (name === 'plan' && detail.value) {
    void loadRelatedPlans(detail.value.id)
    void loadApprovals()
  }
  if (name === 'arrival_report' && detail.value) {
    void loadArrivalAttachments(detail.value.id)
  }
  if (name === 'ai_fees' && detail.value) {
    void loadAiFees(detail.value.id)
  }
  if (name === '做账明细' && detail.value) {
    void loadAiFees(detail.value.id)
    void loadDetailAttachment('报价单')
    void loadDetailAttachment('订舱单')
  }
  if (name === '航班查询' && detail.value) {
    void markFlightSeen()
    detail.value.flight_changed = false
  }
  if (name === '提货计划' && detail.value) {
    void loadDetailAttachment('头程入货通知')
    void loadDetailAttachment('二程入货通知')
  }
  if (name === '开票' && detail.value) {
    void loadDetailAttachment('确认账单文件')
    void loadDetailAttachment('发票')
  }
}

function tableRowsFor(block: LinkBlock) {
  if (block.key === 'pricing' || block.key === 'pricing_review') return relatedPricing.value
  if (block.key === 'plan') return relatedPlans.value
  return relatedOrders.value
}

function blockCount(block: LinkBlock) {
  if (block.key === 'pricing' || block.key === 'pricing_review') return relatedPricing.value.length
  if (block.key === 'plan') return relatedPlans.value.length
  return relatedOrders.value.length
}

function blockLabel(block: LinkBlock) {
  const n = blockCount(block)
  return n > 0 ? `${block.label}（${n}）` : block.label
}

async function loadRelatedOrders(opportunityId: number) {
  try {
    const res = await request<any>({
      url: `/weida/customer_service/${opportunityId}/orders`,
      method: 'get',
    })
    relatedOrders.value = res.data?.data?.items ?? []
    syncOrderDraft()
    syncOrderSnapshots()
    for (const order of relatedOrders.value) {
      void loadOrderAttachments(order.id)
      for (const field of ORDER_ATTACH_FIELDS) void loadOrderExtraAttachment(order.id, field)
    }
  } catch {
    relatedOrders.value = []
  }
}

async function searchPortOptions(keyword: string) {
  if (!keyword || !keyword.trim()) {
    portOptions.value = []
    return
  }
  portOptionsLoading.value = true
  try {
    const res = await request<any>({
      url: '/weida/port-options',
      method: 'get',
      params: { keyword: keyword || undefined, limit: 6000 },
    })
    portOptions.value = res.data?.data ?? []
  } catch {
    portOptions.value = []
  } finally {
    portOptionsLoading.value = false
  }
}

function syncOrderDraft() {
  const orderBlocks = linkBlocks.value.filter((b) => b.table === 'biz_order')
  const columns = new Map<string, LinkBlock['columns'][number]>()
  for (const b of orderBlocks) for (const c of b.columns) columns.set(c.column, c)
  if (!columns.size) return
  for (const order of relatedOrders.value) {
    const draft: Record<string, any> = {}
    for (const c of columns.values()) {
      let value = blockValue(order, c.column) ?? ''
      if ((c.column === 'warehouse_in_operation_time' || c.column === 'flight') && value) {
        value = String(value).replace('T', ' ').slice(0, 10)
      }
      draft[c.column] = value
    }
    orderDraft[order.id] = draft
  }
}

function orderGoods(order: RelatedRow) {
  const goods = order.goods || {}
  return {
    piece_count: goods.piece_count ?? '',
    gross_weight_kg: goods.gross_weight_kg ?? '',
    volume_m3: goods.volume_m3 ?? '',
    size: goods.size ?? '',
  }
}

function syncOrderSnapshots() {
  for (const order of relatedOrders.value) {
    const source = orderGoods(order)
    const draft = {
      piece_count: source.piece_count === null || source.piece_count === undefined ? '' : String(source.piece_count),
      gross_weight_kg:
        source.gross_weight_kg === null || source.gross_weight_kg === undefined ? '' : String(source.gross_weight_kg),
      volume_m3: source.volume_m3 === null || source.volume_m3 === undefined ? '' : String(source.volume_m3),
      size: source.size ? String(source.size) : '',
    }
    orderGoodsDraft[order.id] = draft
    orderSnapshots[order.id] = JSON.stringify(orderDraft[order.id] || {})
    goodsSnapshots[order.id] = JSON.stringify(draft)
  }
}

function isOrderDirty(order: RelatedRow) {
  return (
    JSON.stringify(orderDraft[order.id] || {}) !== orderSnapshots[order.id] ||
    JSON.stringify(orderGoodsDraft[order.id] || {}) !== goodsSnapshots[order.id]
  )
}

function opportunityGoodsText(fields: Record<string, any>) {
  const val = (key: string) => fields[key]?.value ?? ''
  const nums = [val('piece_count'), val('gross_weight_kg'), val('volume_m3')]
    .map((v, i) => {
      if (v === '' || v === null || v === undefined) return ''
      const n = Number(v)
      if (!Number.isFinite(n)) return String(v)
      return i === 0 ? String(Math.trunc(n)) : n.toFixed(i === 1 ? 2 : 4).replace(/0+$/, '').replace(/\.$/, '')
    })
    .filter(Boolean)
  const head = nums.join('/')
  const size = String(val('size_length_width_height_quantity') || '').trim()
  return [head, size].filter(Boolean).join('\n')
}

function goodsText(orderId: number) {
  const goods = orderGoodsDraft[orderId]
  if (!goods) return ''
  const head = [goods.piece_count, goods.gross_weight_kg, goods.volume_m3]
    .filter((v) => v !== '' && v !== null && v !== undefined)
    .join('/')
  return [head, goods.size].filter(Boolean).join('\n')
}

function formatDecimal(value: string, digits: number) {
  const num = Number(value)
  if (!Number.isFinite(num)) return ''
  return num.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')
}

function formatGoodsPiece(orderId: number) {
  const goods = orderGoodsDraft[orderId]
  if (!goods || goods.piece_count === '') return
  const num = Number(goods.piece_count)
  goods.piece_count = Number.isFinite(num) ? String(Math.trunc(num)) : ''
}

function formatGoodsGross(orderId: number) {
  const goods = orderGoodsDraft[orderId]
  if (!goods || goods.gross_weight_kg === '') return
  const num = Number(goods.gross_weight_kg)
  goods.gross_weight_kg = Number.isFinite(num) ? num.toFixed(2) : ''
}

function formatGoodsVolume(orderId: number) {
  const goods = orderGoodsDraft[orderId]
  if (!goods || goods.volume_m3 === '') return
  goods.volume_m3 = formatDecimal(goods.volume_m3, 4)
}

function calcVolumeFromSize(size: string): number {
  if (!size) return 0
  const re = /(\d+(?:\.\d+)?)\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*[*xX×]\s*(\d+(?:\.\d+)?)(?:\s*CM)?(?:\s*[*xX×]\s*(\d+))?/g
  let total = 0
  for (const match of size.matchAll(re)) {
    const qty = match[4] ? Number(match[4]) : 1
    total += (Number(match[1]) * Number(match[2]) * Number(match[3]) * qty) / 1_000_000
  }
  return total
}

function calcEditVolume() {
  const size = String(editModel.size_length_width_height_quantity || '')
  const volume = calcVolumeFromSize(size)
  if (!volume) {
    ElMessage.warning('尺寸格式无法识别，请按 长*宽*高*件数 填写')
    return
  }
  editModel.volume_m3 = formatDecimal(String(volume), 4)
  ElMessage.success(`已计算体积 ${editModel.volume_m3} m³`)
}

function recalcGoodsVolume(orderId: number) {
  const goods = orderGoodsDraft[orderId]
  if (!goods) return
  const volume = calcVolumeFromSize(goods.size)
  if (volume > 0) goods.volume_m3 = formatDecimal(String(volume), 4)
}

function isOrderWide(c: Col) {
  return ['operation_requirement', 'special_note', 'goods_data', 'goods_name_special_attr'].includes(c.column)
}

async function saveOrder(row: RelatedRow) {
  const draft = orderDraft[row.id]
  if (!draft) return
  try {
    const goods = orderGoodsDraft[row.id]
    if (JSON.stringify(goods) !== goodsSnapshots[row.id]) {
      await request<any>({
        url: `/weida/customer_service/orders/${row.id}/goods`,
        method: 'patch',
        data: {
          piece_count: goods.piece_count === '' ? null : Number(goods.piece_count),
          gross_weight_kg: goods.gross_weight_kg === '' ? null : Number(goods.gross_weight_kg),
          volume_m3: goods.volume_m3 === '' ? null : Number(goods.volume_m3),
          size: goods.size || null,
        },
      })
      row.goods = {
        piece_count: goods.piece_count === '' ? null : Number(goods.piece_count),
        gross_weight_kg: goods.gross_weight_kg === '' ? null : Number(goods.gross_weight_kg),
        volume_m3: goods.volume_m3 === '' ? null : Number(goods.volume_m3),
        size: goods.size || null,
      }
      row.fields.goods_data = { label: '货物数据', value: goodsText(row.id) }
    }
    const payload = { ...draft }
    if (JSON.stringify(goods) !== goodsSnapshots[row.id]) delete payload.goods_data
    const res = await request<any>({
      url: `/weida/customer_service/orders/${row.id}`,
      method: 'patch',
      data: { fields: payload },
    })
    row.fields = res.data?.data?.fields ?? row.fields
    orderSnapshots[row.id] = JSON.stringify(orderDraft[row.id] || {})
    goodsSnapshots[row.id] = JSON.stringify(orderGoodsDraft[row.id] || {})
    ElMessage.success('订仓单已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '订仓单保存失败')
  }
}

async function handoverOrder(row: RelatedRow) {
  if (blockValue(row, 'handover_domestic_operation') === 1) return
  const required: Array<[string, string]> = [
    ['need_insurance', '是否需要投保'],
    ['need_pickup', '是否需要提货'],
    ['need_appraisal', '是否需要鉴定'],
    ['need_declaration', '是否需要代报关'],
    ['is_direct', '是否直单'],
    ['client_ref_no', '客户 REF NO.'],
    ['domestic_operation', '国内操作'],
    ['supplier', '供应商'],
    ['sales_person', '业务员'],
    ['app_remark', '备注'],
    ['agent_deadline', '代理deadline'],
    ['latest_port_arrival_time', '最晚到港时间'],
    ['pickup_fleet', '提货车队'],
    ['pickup_fee', '提货费'],
  ]
  const draft = orderDraft[row.id] || {}
  const missing = required
    .filter(([col]) => {
      if ((col === 'pickup_fleet' || col === 'pickup_fee') && String(draft.need_pickup || '') !== '是') {
        return false
      }
      const v = draft[col] ?? blockValue(row, col)
      return v === null || v === undefined || String(v).trim() === ''
    })
    .map(([, label]) => label)
  // 运输条款以 D 开头（DAP/DDP…）时「要求到门时间」必填（妙搭 requiredWhen）
  const clause = String(draft.transport_clause ?? blockValue(row, 'transport_clause') ?? '').trim()
  const door = draft.required_door_time ?? blockValue(row, 'required_door_time')
  if (clause.toUpperCase().startsWith('D') && (door === null || door === undefined || String(door).trim() === '')) {
    missing.push('要求到门时间')
  }
  if (missing.length) {
    ElMessage.warning(`交接前请先填写：${missing.join('、')}`)
    return
  }
  handoverBusy.value = row.id
  try {
    if (isOrderDirty(row)) await saveOrder(row)
    await request<any>({
      url: `/weida/customer_service/orders/${row.id}`,
      method: 'patch',
      data: { fields: { handover_domestic_operation: 1 } },
    })
    row.fields.handover_domestic_operation = { label: '交接给国内操作', value: 1 }
    ElMessage.success('已交接给国内操作')
  } catch (e: any) {
    ElMessage.error(e?.message || '交接失败')
  } finally {
    handoverBusy.value = null
  }
}

async function loadDetailAttachment(field: string) {
  if (!detail.value) return
  try {
    const res = await request<any>({
      url: `/weida/customer_service/orders/${detail.value.id}/attachments`,
      method: 'get',
      params: { field },
    })
    detailAttachments[field] = res.data?.data?.items ?? []
  } catch {
    detailAttachments[field] = []
  }
}

async function uploadDetailAttachment(field: string, options: any) {
  if (!detail.value) return
  const form = new FormData()
  form.append('file', options.file)
  form.append('field', field)
  try {
    await request({
      url: `/weida/customer_service/orders/${detail.value.id}/attachments`,
      method: 'post',
      data: form,
    })
    await loadDetailAttachment(field)
    options.onSuccess?.({})
    ElMessage.success('已上传')
  } catch (e: any) {
    options.onError?.(e)
    ElMessage.error(e?.message || '上传失败')
  }
}

async function removeDetailAttachment(a: AttachmentItem, field: string) {
  try {
    await ElMessageBox.confirm(`删除附件「${a.file_name}」？`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await request({ url: `/weida/attachments/${a.id}`, method: 'delete' })
  await loadDetailAttachment(field)
  ElMessage.success('已删除')
}

async function loadOrderAttachments(orderId: number) {
  try {
    const res = await request<any>({
      url: `/weida/customer_service/orders/${orderId}/attachments`,
      method: 'get',
      params: { field: '订舱单' },
    })
    orderAttachments[orderId] = res.data?.data?.items ?? []
  } catch {
    orderAttachments[orderId] = []
  }
}

async function loadOrderExtraAttachment(orderId: number, field: string) {
  if (!orderExtraAttachments[orderId]) orderExtraAttachments[orderId] = {}
  try {
    const res = await request<any>({
      url: `/weida/customer_service/orders/${orderId}/attachments`,
      method: 'get',
      params: { field },
    })
    orderExtraAttachments[orderId][field] = res.data?.data?.items ?? []
  } catch {
    orderExtraAttachments[orderId][field] = []
  }
}

async function uploadOrderExtraAttachment(orderId: number, field: string, options: any) {
  const form = new FormData()
  form.append('file', options.file)
  form.append('field', field)
  try {
    await request({
      url: `/weida/customer_service/orders/${orderId}/attachments`,
      method: 'post',
      data: form,
    })
    await loadOrderExtraAttachment(orderId, field)
    options.onSuccess?.({})
    ElMessage.success('已上传')
  } catch (e: any) {
    options.onError?.(e)
    ElMessage.error(e?.message || '上传失败')
  }
}

async function removeOrderExtraAttachment(a: AttachmentItem, orderId: number, field: string) {
  try {
    await ElMessageBox.confirm(`删除附件「${a.file_name}」？`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await request({ url: `/weida/attachments/${a.id}`, method: 'delete' })
  await loadOrderExtraAttachment(orderId, field)
  ElMessage.success('已删除')
}

async function generateOrderBooking(row: RelatedRow) {
  if (isOrderDirty(row)) {
    ElMessage.warning('请先保存订仓单，再生成订舱单')
    return
  }
  bookingBusy.value = row.id
  try {
    const res = await request<any>({
      url: `/weida/customer_service/orders/${row.id}/generate-booking`,
      method: 'post',
    })
    row.fields = res.data?.data?.order?.fields ?? row.fields
    await loadOrderAttachments(row.id)
    ElMessage.success(res.data?.data?.file_name ? `已生成 ${res.data.data.file_name}` : '订舱单已生成')
  } catch (e: any) {
    ElMessage.error(e?.message || '生成订舱单失败')
  } finally {
    bookingBusy.value = null
  }
}

async function uploadOrderAttachment(row: RelatedRow, options: any) {
  const form = new FormData()
  form.append('file', options.file)
  form.append('field', '订舱单')
  try {
    await request({
      url: `/weida/customer_service/orders/${row.id}/attachments`,
      method: 'post',
      data: form,
    })
    await loadOrderAttachments(row.id)
    options.onSuccess?.({})
    ElMessage.success('已上传')
  } catch (e: any) {
    options.onError?.(e)
    ElMessage.error(e?.message || '上传失败')
  }
}

async function removeOrderAttachment(a: AttachmentItem, orderId: number) {
  try {
    await ElMessageBox.confirm(`删除附件「${a.file_name}」？`, '删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await request({ url: `/weida/attachments/${a.id}`, method: 'delete' })
    await loadOrderAttachments(orderId)
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

async function searchQuotePorts(keyword: string) {
  if (!keyword || !keyword.trim()) {
    quotePortOptions.value = []
    return
  }
  try {
    const res = await request<any>({
      url: '/weida/port-options',
      method: 'get',
      params: { keyword: keyword.trim(), limit: 50 },
    })
    quotePortOptions.value = res.data?.data ?? []
  } catch {
    quotePortOptions.value = []
  }
}

function openQuoteDialog(row?: RelatedRow) {
  quoteEditId.value = row?.id ?? null
  Object.keys(quoteForm).forEach((k) => delete quoteForm[k])
  for (const f of QUOTE_FIELDS) {
    const raw = row ? blockValue(row, f.key) : ''
    if (f.kind === 'ports') {
      let list: string[] = []
      if (Array.isArray(raw)) list = raw.map(String)
      else if (typeof raw === 'string' && raw.trim()) {
        try {
          const parsed = JSON.parse(raw)
          list = Array.isArray(parsed) ? parsed.map(String) : [raw]
        } catch {
          list = raw.replace(/[{}]/g, '').split(',').map((x) => x.trim()).filter(Boolean)
        }
      }
      quoteForm[f.key] = list
    } else if (f.kind === 'port') {
      quoteForm[f.key] = Array.isArray(raw) ? String(raw[0] ?? '') : raw == null ? '' : String(raw)
    } else {
      quoteForm[f.key] = raw === null || raw === undefined ? '' : String(raw)
    }
  }
  quoteDialogOpen.value = true
  quotePortOptions.value = []
}

async function saveQuote() {
  if (!detail.value) return
  quoteSaving.value = true
  try {
    const payload = { ...quoteForm }
    if (quoteEditId.value) {
      await request({
        url: `/weida/pricing/quotes/${quoteEditId.value}`,
        method: 'patch',
        data: { fields: payload },
      })
    } else {
      await request({
        url: `/weida/pricing/${detail.value.id}/quotes`,
        method: 'post',
        data: { fields: payload },
      })
    }
    quoteDialogOpen.value = false
    await loadRelatedPricing(detail.value.id)
    await openDetail({ id: detail.value.id, fields: {} })
    await load()
    ElMessage.success('航线报价已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存航线报价失败')
  } finally {
    quoteSaving.value = false
  }
}

async function approvePricing() {
  if (!detail.value) return
  reviewBusy.value = true
  try {
    await request({
      url: `/weida/pricing/${detail.value.id}/approve`,
      method: 'post',
    })
    await loadRelatedOrders(detail.value.id)
    await load()
    ElMessage.success('已核价')
  } catch (e: any) {
    ElMessage.error(e?.message || '核价失败')
  } finally {
    reviewBusy.value = false
  }
}

async function loadBilllibOptions() {
  try {
    const res = await request<any>({ url: '/weida/billlib/options', method: 'get' })
    billlibOptions.value = res.data?.data ?? []
  } catch {
    billlibOptions.value = []
  }
}

async function copyBlFromCard(row: RelatedRow, billlibId: any) {
  if (!billlibId) return
  try {
    await request({
      url: `/weida/ifreight/${row.id}/copy-bl`,
      method: 'post',
      data: { billlib_id: billlibId },
    })
    ElMessage.success('已带入提单库')
    if (detail.value) await loadRelatedBlocks(detail.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '带入失败')
  }
}

async function saveIfreightCard(row: RelatedRow) {
  const card = ifreightCard[row.id] || { prompt_type: '', prompt: '' }
  try {
    await request({
      url: `/weida/domestic_ops/records/${detail.value?.id}/related/ifreight/${row.id}`,
      method: 'patch',
      data: {
        fields: {
          poa_ai_prompt_type: card.prompt_type,
          poa_ai_prompt: card.prompt,
        },
      },
    })
    ElMessage.success('已保存这一条')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function onDocCommand(row: RelatedRow, cmd: string) {
  const orderId = detail.value?.id
  if (!orderId) return
  docBusy.value = cmd
  try {
    const res = await fetch(`/api/v1/weida/domestic_ops/orders/${orderId}/doc?kind=${cmd}`, {
      headers: { Authorization: `Bearer ${Auth.getAccessToken()}` },
    })
    if (!res.ok) {
      let detailText = '单据生成失败'
      try {
        const payload = await res.json()
        detailText = payload?.detail || detailText
      } catch {
        /* 非 JSON 就沿用默认文案 */
      }
      ElMessage.error(detailText)
      return
    }
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = /filename\*=UTF-8''([^;]+)/.exec(disposition)
    const fallback = `${cmd}-${orderId}.${blob.type === 'application/pdf' ? 'pdf' : 'zip'}`
    const fileName = match ? decodeURIComponent(match[1]) : fallback
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
    ElMessage.success(`已生成 ${fileName}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '单据生成失败')
  } finally {
    docBusy.value = ''
  }
}

async function uploadIfreightAttachment(row: RelatedRow, field: string, options: any) {
  const form = new FormData()
  form.append('file', options.file)
  form.append('field', field)
  try {
    await request({
      url: `/weida/${role.value}/records/${row.id}/attachments`,
      method: 'post',
      data: form,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    if (detail.value) await loadRelatedBlocks(detail.value.id)
    ElMessage.success('已上传')
  } catch (e: any) {
    ElMessage.error(e?.message || '上传失败')
  }
}

async function openBilllib(row: RelatedRow) {
  billlibTarget.value = row
  billlibValue.value = null
  billlibOpen.value = true
  try {
    const res = await request<any>({ url: '/weida/billlib/options', method: 'get' })
    billlibOptions.value = res.data?.data ?? []
  } catch {
    billlibOptions.value = []
  }
}

async function confirmCopyBl() {
  if (!billlibTarget.value || !billlibValue.value) return
  try {
    await request({
      url: `/weida/ifreight/${billlibTarget.value.id}/copy-bl`,
      method: 'post',
      data: { billlib_id: billlibValue.value },
    })
    billlibOpen.value = false
    if (detail.value) await loadRelatedBlocks(detail.value.id)
    ElMessage.success('已带入提单信息')
  } catch (e: any) {
    ElMessage.error(e?.message || '带入失败')
  }
}

async function calcRelatedVolume(rb: RelatedBlock, row: RelatedRow) {
  try {
    const res = await request<any>({
      url: `/weida/ifreight/${row.id}/calc-volume`,
      method: 'post',
    })
    const data = res.data?.data ?? {}
    row.fields.volume = { label: '体积', value: data.volume }
    row.fields.customer_billing_weight = {
      label: '客户计费重',
      value: data.customer_billing_weight,
    }
    ElMessage.success(`体积 ${data.volume}，计费重 ${data.customer_billing_weight}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '计算失败')
  }
}

async function triggerInstruction(rb: RelatedBlock, row: RelatedRow) {
  try {
    await request({
      url: `/weida/domestic_ops/records/${detail.value?.id}/related/${rb.key}/${row.id}`,
      method: 'patch',
      data: { fields: { power_of_attorney_ai_checkbox: 1 } },
    })
    ElMessage.success('已触发托书识别')
    if (detail.value) await loadRelatedBlocks(detail.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '触发失败')
  }
}

async function syncRelated(rb: RelatedBlock, row: RelatedRow) {
  try {
    const res = await request<any>({
      url: `/weida/domestic_ops/ifreight/${row.id}/sync-ifreight`,
      method: 'post',
    })
    const result = res.data?.data || {}
    if (result.status === '成功') {
      ElMessage.success(result.bl_no ? `同步成功，分单号 ${result.bl_no}` : '同步成功')
    } else {
      ElMessage.error(`同步失败：${result.reason || '未知原因'}`)
    }
    if (detail.value) await loadRelatedBlocks(detail.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '同步失败')
  }
}

async function savePlanField(row: RelatedRow, column: string, value: any) {
  try {
    await request({
      url: `/weida/plans/${row.id}`,
      method: 'patch',
      data: { fields: { [column]: value } },
    })
    if (!row.fields) row.fields = {}
    row.fields[column] = { label: column, value } as any
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

function parseSizeVolume(text: string): { volume: number; sizes: string } | null {
  // 与妙搭 parseSizes / parseSizeGroups 同一套：一组 `长*宽*高[*件数]`，单位厘米
  const groups = text
    .split(/[\s,;，；、]+/)
    .map((x) => x.trim())
    .filter(Boolean)
  if (!groups.length) return null
  let total = 0
  const normalized: string[] = []
  for (const group of groups) {
    const parts = group.split(/[*xX×]/).map((x) => Number(x))
    if (parts.length < 3 || parts.length > 4) return null
    if (parts.some((n) => !Number.isFinite(n) || n <= 0)) return null
    const [l, w, h, n = 1] = parts
    total += (l / 100) * (w / 100) * (h / 100) * n
    normalized.push(parts.join('*'))
  }
  return { volume: Math.round(total * 1000) / 1000, sizes: normalized.join('\n') }
}

async function calcIfreightVolume(rb: RelatedBlock, row: RelatedRow) {
  const text = String(row.fields.dimension?.value ?? '')
  const parsed = parseSizeVolume(text)
  if (!parsed) {
    ElMessage.warning('尺寸格式认不出：一组写 长*宽*高 或 长*宽*高*件数，多个尺寸用空格/逗号/换行分隔')
    return
  }
  const factor = 167
  const weight = Math.ceil(Math.round(parsed.volume * factor * 1e6) / 1e6 * 2 - 1e-9) / 2
  const gross = Number(row.fields.gross_weight?.value ?? 0)
  const billing = gross > 0 ? Math.max(weight, Math.ceil(gross * 2 - 1e-9) / 2) : weight
  await saveRelatedField(rb, row, 'dimension', parsed.sizes)
  await saveRelatedField(rb, row, 'volume', String(parsed.volume))
  await saveRelatedField(rb, row, 'volume_weight', String(weight))
  await saveRelatedField(rb, row, 'customer_billing_weight', String(billing))
  ElMessage.success(`体积 ${parsed.volume} m³，客户计费重 ${billing} kg`)
}

async function saveMasterNo(rb: RelatedBlock, row: RelatedRow, value: string) {
  if (!detail.value) return
  try {
    // 主单号真身在订单表（iFreight 上是查找引用），改订单后把这一票所有分单行一起换掉
    await request({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'patch',
      data: { fields: { master_no: value } },
    })
    detail.value.fields.master_no = { label: '主单号', value }
    for (const item of relatedData[rb.key] || []) {
      item.fields.master_no = { label: '主单号', value }
    }
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function saveOrderField(column: string, value: string) {
  if (!detail.value) return
  try {
    await request({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'patch',
      data: { fields: { [column]: value } },
    })
    detail.value.fields[column] = { label: column, value }
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function saveRelatedField(rb: RelatedBlock, row: RelatedRow, column: string, value: string) {
  if (!detail.value) return
  try {
    await request({
      url: `/weida/${role.value}/records/${detail.value.id}/related/${rb.key}/${row.id}`,
      method: 'patch',
      data: { fields: { [column]: value } },
    })
    row.fields[column] = { label: column, value }
    if (
      rb.key === 'ifreight' &&
      ['quantity', 'gross_weight', 'volume', 'dimension', 'house_bl_no', 'volume_weight', 'customer_billing_weight'].includes(column) &&
      detail.value
    ) {
      const fresh = await request<any>({
        url: `/weida/${role.value}/records/${detail.value.id}`,
        method: 'get',
      })
      if (fresh.data?.data) detail.value = fresh.data.data
    }
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}

async function loadRelatedBlocks(recordId: number) {
  await Promise.all(
    visibleRelatedBlocks.value.map(async (rb) => {
      relatedLoading[rb.key] = true
      try {
        const res = await request<any>({
          url: `/weida/${role.value}/records/${recordId}/related/${rb.key}`,
          method: 'get',
        })
        relatedData[rb.key] = res.data?.data?.items ?? []
        if (rb.key === 'ifreight') {
          for (const row of relatedData[rb.key]) {
            ifreightCard[row.id] = {
              prompt_type: String(row.fields.poa_ai_prompt_type?.value || ''),
              prompt: String(row.fields.poa_ai_prompt?.value || ''),
            }
          }
          if (!billlibOptions.value.length) await loadBilllibOptions()
        }
      } catch {
        relatedData[rb.key] = []
      } finally {
        relatedLoading[rb.key] = false
      }
    }),
  )
}

async function loadRelatedPlans(opportunityId: number) {
  try {
    const res = await request<any>({
      url: `/weida/customer_service/${opportunityId}/plans`,
      method: 'get',
    })
    relatedPlans.value = res.data?.data?.items ?? []
    await loadPlanAttachments()
  } catch {
    relatedPlans.value = []
  }
}

async function loadApprovals() {
  if (!detail.value) return
  try {
    const res = await request<any>({
      url: '/weida/approvals',
      method: 'get',
      params: { biz_type: 'opportunity', biz_id: detail.value.id },
    })
    approvals.value = res.data?.data?.items ?? []
  } catch {
    approvals.value = []
  }
}

async function syncApprovalStatus() {
  if (!detail.value) return
  approvalBusy.value = true
  try {
    await request({ url: '/weida/approvals/sync', method: 'post' })
    const id = detail.value.id
    await openDetail({ id, fields: {} })
    ElMessage.success('审批状态已刷新')
  } catch (e: any) {
    ElMessage.error(e?.message || '刷新失败')
  } finally {
    approvalBusy.value = false
  }
}

async function requestApproval() {
  if (!detail.value || approvalBusy.value) return
  approvalBusy.value = true
  try {
    const res = await request<any>({
      url: `/weida/customer_service/opportunities/${detail.value.id}/feishu-approval`,
      method: 'post',
    })
    detail.value.fields.initiate_approval = { label: '发起审批', value: 1 }
    detail.value.fields.approval_status = { label: '审批状态', value: '审批中' }
    detail.value.fields.feishu_approval_code = {
      label: '飞书审批编码',
      value: res.data?.data?.instance_code || '',
    }
    ElMessage.success('已发起飞书审批')
    await loadApprovals()
  } catch (e: any) {
    ElMessage.error(e?.message || '发起飞书审批失败')
  } finally {
    approvalBusy.value = false
  }
}

async function decideApproval(row: Record<string, any>, approve: boolean) {
  if (approvalBusy.value) return
  approvalBusy.value = true
  try {
    await request({
      url: `/weida/approvals/${row.id}/decide`,
      method: 'post',
      data: { approve, comment: approvalComment.value || undefined },
    })
    approvalComment.value = ''
    const fresh = await request<any>({
      url: `/weida/${role.value}/records/${detail.value!.id}`,
      method: 'get',
    })
    if (fresh.data?.data) detail.value = fresh.data.data
    await loadApprovals()
    ElMessage.success(approve ? '审批已通过' : '审批已驳回')
  } catch (e: any) {
    ElMessage.error(e?.message || '审批处理失败')
  } finally {
    approvalBusy.value = false
  }
}

async function rerunPlan(row: RelatedRow) {
  try {
    await ElMessageBox.confirm('这条报价已经生成过对客方案，是否再生成一条？', '重新生成', {
      confirmButtonText: '继续生成',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  await generatePlanFromPricing(row, true)
}

async function generatePlanFromPricing(row: RelatedRow, force = false) {
  if (!detail.value || planBusy.value) return
  planBusy.value = row.id
  row.plan_id = -1
  try {
    const res = await request<any>({
      url: `/weida/customer_service/${detail.value.id}/generate-plan`,
      method: 'post',
      data: { pricing_id: row.id, force },
    })
    const data = res.data?.data
    ElMessage.success(data?.created ? `已生成对客方案 #${data.plan.id}` : `已存在对客方案 #${data.plan.id}`)
    await Promise.all([loadRelatedPricing(detail.value.id), loadRelatedPlans(detail.value.id)])
  } catch (e: any) {
    row.plan_id = null
    ElMessage.error(e?.message || '生成对客方案失败')
  } finally {
    planBusy.value = null
  }
}

async function runCommand(key: string) {
  if (!detail.value || actionBusy.value) return
  if (key !== 'generate_plan' && key !== 'send_invoice') return
  actionBusy.value = key
  try {
    if (key === 'send_invoice') {
      const res = await request<any>({
        url: `/weida/domestic_ops/orders/${detail.value.id}/send-invoice`,
        method: 'post',
      })
      ElMessage.success(`发票已发送，附件 ${res.data?.data?.attachments ?? 0} 个`)
      const fresh = await request<any>({
        url: `/weida/${role.value}/records/${detail.value.id}`,
        method: 'get',
      })
      detail.value = fresh.data?.data ?? detail.value
      return
    }
    const res = await request<any>({
      url: `/weida/route_quote/${detail.value.id}/generate-plan`,
      method: 'post',
    })
    const data = res.data?.data
    ElMessage.success(data?.created ? `已生成对客方案 #${data.plan.id}` : `已存在对客方案 #${data.plan.id}`)
    const fresh = await request<any>({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'get',
    })
    detail.value = fresh.data?.data ?? detail.value
  } catch (e: any) {
    ElMessage.error(e?.message || '生成对客方案失败')
  } finally {
    actionBusy.value = ''
  }
}

async function runAction(field: string) {
  if (actionBusy.value) return
  const done = todoChecked(field)
  if (done) {
    try {
      await ElMessageBox.confirm('已经执行过，是否重新执行？会先取消再重新勾选。', '重新执行', {
        confirmButtonText: '重新执行',
        cancelButtonText: '取消',
        type: 'warning',
      })
    } catch {
      return
    }
  }
  actionBusy.value = field
  try {
    const mailKinds: Record<string, string> = {
      send_pickup_email: 'pickup',
      invoice_issuance: 'invoice',
    }
    // 预报 / 报关 / 发货到计划：Java 引擎那三封信（模板与取数口径一致）
    const javaMailKinds: Record<string, string> = {
      send_forecast: 'prealert',
      customs_declaration: 'customs',
      issue_operation_plan: 'operation_plan',
    }
    if (javaMailKinds[field] && role.value === 'domestic_ops' && detail.value) {
      const res = await request<any>({
        url: `/weida/domestic_ops/orders/${detail.value.id}/action-mail`,
        method: 'post',
        params: { kind: javaMailKinds[field] },
      })
      const fresh = await request<any>({
        url: `/weida/${role.value}/records/${detail.value.id}`,
        method: 'get',
      })
      if (fresh.data?.data) {
        detail.value = fresh.data.data
        const index = rows.value.findIndex((r) => r.id === fresh.data.data.id)
        if (index >= 0) rows.value[index] = fresh.data.data
      }
      ElMessage.success(res.data?.msg || '邮件已发送')
      return
    }
    if (mailKinds[field] && role.value === 'domestic_ops' && detail.value) {
      const res = await request<any>({
        url: `/weida/domestic_ops/orders/${detail.value.id}/send-mail`,
        method: 'post',
        data: { kind: mailKinds[field] },
      })
      const fresh = await request<any>({
        url: `/weida/${role.value}/records/${detail.value.id}`,
        method: 'get',
      })
      if (fresh.data?.data) {
        detail.value = fresh.data.data
        const index = rows.value.findIndex((r) => r.id === fresh.data.data.id)
        if (index >= 0) rows.value[index] = fresh.data.data
      }
      const attached = res.data?.data?.attachments || 0
      ElMessage.success(`邮件已发送到操作邮箱${attached ? `，附 ${attached} 个附件` : ''}`)
      return
    }
    if (field === 'second_pickup_email' && role.value === 'domestic_ops' && detail.value) {
      const res = await request<any>({
        url: `/weida/domestic_ops/orders/${detail.value.id}/second-leg-mail`,
        method: 'post',
      })
      const data = res.data?.data || {}
      const fresh = await request<any>({
        url: `/weida/${role.value}/records/${detail.value.id}`,
        method: 'get',
      })
      if (fresh.data?.data) {
        detail.value = fresh.data.data
        const index = rows.value.findIndex((r) => r.id === fresh.data.data.id)
        if (index >= 0) rows.value[index] = fresh.data.data
      }
      ElMessage.success(data.message || '已执行')
      if (data.action === 'xanda' && detail.value) await loadRelatedBlocks(detail.value.id)
      return
    }
    if (field === 'create_ifreight_data' && role.value === 'domestic_ops' && detail.value) {
      await request({
        url: `/weida/domestic_ops/orders/${detail.value.id}/ifreight`,
        method: 'post',
      })
      detail.value.fields.create_ifreight_data = { label: '创建iFreight 数据', value: 1 }
      await loadRelatedBlocks(detail.value.id)
      ElMessage.success('已新增分单')
      return
    }
    if (done) {
      await toggleTodo(field, false)
      await toggleTodo(field, true)
    } else {
      await toggleTodo(field, true)
    }
  } finally {
    actionBusy.value = ''
  }
}

async function toggleTodo(field: string, value: boolean) {
  if (!detail.value || drawerMode.value !== 'edit') return
  try {
    const res = await request<any>({
      url: `/weida/${role.value}/records/${detail.value.id}/todo`,
      method: 'patch',
      data: { field, value: !!value },
    })
    const next = res.data?.data
    if (next) {
      detail.value = next
      const index = rows.value.findIndex((r) => r.id === next.id)
      if (index >= 0) rows.value[index] = next
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '待办更新失败')
  }
}

async function saveDetail() {
  if (!detail.value) return
  if (drawerMode.value === 'create' && !canCreate.value) {
    ElMessage.warning(`还差必填项：${createMissing.value.join('、')}`)
    return
  }
  // 对客方案的两道门禁（妙搭 resultGates / resultRules）：
  //   拿到货=是 之前必须先选定「最终对客方案」；拿到货=否 必须写「未拿到货备注」。
  if (role.value === 'customer_service' && drawerMode.value === 'edit') {
    const got = String(editModel.is_goods_received ?? '')
    if (got === '是' && !editModel.final_plan_id) {
      ElMessage.warning('请先选择「最终对客方案」，再把「是否拿到货」设为「是」')
      return
    }
    if (got === '否' && !String(editModel.goods_not_received_remark ?? '').trim()) {
      ElMessage.warning('「是否拿到货」选了「否」，「未拿到货备注」必须填写')
      return
    }
  }
  let fields: Record<string, any>
  try {
    fields = buildPayload()
  } catch {
    return
  }
  const dirtyOrder =
    role.value === 'customer_service' && relatedOrders.value.length === 1 ? relatedOrders.value[0] : null
  if (!Object.keys(fields).length) {
    if (dirtyOrder && isOrderDirty(dirtyOrder)) {
      await saveOrder(dirtyOrder)
    } else {
      ElMessage.info(drawerMode.value === 'create' ? '请填写至少一个字段' : '没有改动')
    }
    return
  }
  saving.value = true
  try {
    if (drawerMode.value === 'create') {
      await request<any>({
        url: `/weida/${role.value}/records`,
        method: 'post',
        data: { fields },
      })
      drawerOpen.value = false
      await load()
      return
    }
    const order = relatedOrders.value.length === 1 ? relatedOrders.value[0] : null
    if (role.value === 'customer_service' && order && isOrderDirty(order)) {
      await saveOrder(order)
    }
    const res = await request<any>({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'patch',
      data: {
        fields,
        expected_update_time: detail.value.fields.update_time?.value || undefined,
      },
    })
    const updated: Row = res.data?.data
    if (updated) {
      detail.value = updated
      const idx = rows.value.findIndex((r) => r.id === updated.id)
      if (idx >= 0) rows.value[idx] = updated
      detailSnapshot.value = JSON.stringify(editModel)
    }
    if (role.value === 'customer_service') {
      await Promise.all([loadRelatedPlans(detail.value.id), loadRelatedOrders(detail.value.id)])
    }
  } catch (e: any) {
    if (e?.status === 409 && detail.value) {
      try {
        const latestRes = await request<any>({
          url: `/weida/${role.value}/records/${detail.value.id}`,
          method: 'get',
        })
        const latest: Row = latestRes.data?.data
        const conflicts = Object.keys(fields).filter((key) => {
          const before = detail.value?.fields[key]?.value ?? null
          const now = latest?.fields[key]?.value ?? null
          return JSON.stringify(before) !== JSON.stringify(now)
        })
        if (!conflicts.length && latest) {
          const retry = await request<any>({
            url: `/weida/${role.value}/records/${detail.value.id}`,
            method: 'patch',
            data: {
              fields,
              expected_update_time: latest.fields.update_time?.value || undefined,
            },
          })
          const updated: Row = retry.data?.data
          if (updated) {
            detail.value = updated
            const idx = rows.value.findIndex((r) => r.id === updated.id)
            if (idx >= 0) rows.value[idx] = updated
            detailSnapshot.value = JSON.stringify(editModel)
          }
          if (role.value === 'customer_service') {
            await Promise.all([loadRelatedPlans(detail.value.id), loadRelatedOrders(detail.value.id)])
          }
          ElMessage.success('已自动合并并保存')
          return
        }
        const labels = conflicts
          .map((key) => detail.value?.fields[key]?.label || key)
          .join('、')
        ElMessage.error(`这些字段刚被别人改过：${labels}。请刷新后确认再保存`)
      } catch (retryError: any) {
        ElMessage.error(retryError?.message || '保存失败')
      }
    } else {
      ElMessage.error(e?.message || '保存失败')
    }
  } finally {
    saving.value = false
  }
}

async function deleteDetail() {
  if (!detail.value || drawerMode.value !== 'edit') return
  try {
    await ElMessageBox.confirm('删除后本地和飞书归档都会删除，确定？', '删除记录', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await request<any>({
      url: `/weida/${role.value}/records/${detail.value.id}`,
      method: 'delete',
    })
    drawerOpen.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPolling() {
  if (pollTimer !== null || isDashboard.value) return
  // 三个工作台共用同一批字段（商机↔订单），一边改了另一边要秒级看到：
  // 客服/航线 5 秒，国内操作 8 秒（整表不翻页，稍慢一点）
  const interval = role.value === 'domestic_ops' ? 8000 : 5000
  pollTimer = window.setInterval(() => {
    if (document.visibilityState !== 'visible' || loading.value || saving.value) return
    void load(true)
    // 抽屉开着时把详情也换一份：客服改了「是否需要投保/代报关」，操作这边几秒内就看到页签变化
    if (drawerOpen.value && detail.value) void refreshDetailQuietly()
  }, interval)
}

function fieldType(column: string): string {
  return columnMap.value.get(column)?.type || 'text'
}

function opsFor(column: string) {
  const type = fieldType(column)
  if (type === 'date' || type === 'datetime') return DATE_OPS
  if (type === 'checkbox') return CHECK_OPS
  return TEXT_OPS
}

function isValueless(op: string): boolean {
  return VALUELESS_OPS.has(op)
}

function saveView() {
  const name = viewName.value.trim()
  if (!name) {
    ElMessage.warning('请输入视图名称')
    return
  }
  const view: FilterView = {
    id: `${Date.now()}`,
    name,
    conjunction: draftConjunction.value,
    conditions: draftConditions.value.map((c) => ({ ...c })),
  }
  savedViews.value = [...savedViews.value, view]
  localStorage.setItem(`weida_filter_views:${role.value}`, JSON.stringify(savedViews.value))
  syncPref('views', savedViews.value)
  viewName.value = ''
  ElMessage.success('已存为视图')
}

function applyView(id: string | null) {
  if (!id) {
    conditions.value = []
    conjunction.value = 'and'
    page.value = 1
    load()
    return
  }
  const view = savedViews.value.find((v) => v.id === id)
  if (!view) return
  conditions.value = view.conditions.map((c) => ({ ...c }))
  conjunction.value = view.conjunction
  page.value = 1
  load()
}

function addCondition() {
  const column = filterableColumns.value[0]?.column || ''
  const ops = opsFor(column)
  draftConditions.value.push({
    column,
    op: ops[0]?.value || 'contains',
    value: '',
  })
}

const settingsStore = useSettingsStore()

function toggleMenu() {
  settingsStore.setMenuOpen(!settingsStore.menuOpen)
}

function onTopCommand(command: string) {
  if (command === 'home') void goHome()
  else if (command === 'columns') columnOpen.value = true
  else if (command === 'paint') paintOpen.value = true
  else if (command === 'create') openCreate()
  else if (command === 'password') pwdOpen.value = true
  else if (command === 'logout') void userStore.logout()
  else if (command === 'refresh') void load()
  else if (command === 'route-quote') void router.push('/weida/route-quote')
  else if (command === 'pricing') void router.push('/weida/pricing')
  else if (command === 'plan') void router.push('/weida/plan')
}

function openFilters() {
  draftConditions.value = conditions.value.map((c) => ({ ...c }))
  draftConjunction.value = conjunction.value
  viewName.value = ''
  if (!draftConditions.value.length) addCondition()
  filterOpen.value = true
}

function applyFilters() {
  conditions.value = draftConditions.value.filter(
    (c) => c.column && (isValueless(c.op) || (c.value !== '' && c.value !== null && c.value !== undefined))
  )
  conjunction.value = draftConjunction.value
  activeViewId.value = ''
  filterOpen.value = false
  page.value = 1
  load()
}

function resetFilters() {
  conditions.value = []
  draftConditions.value = []
  conjunction.value = 'and'
  draftConjunction.value = 'and'
  activeViewId.value = ''
  filterOpen.value = false
  page.value = 1
  load()
}

function disconnectEvents() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  stopPolling()
}

function connectEvents() {
  disconnectEvents()
  if (isDashboard.value) return
  const token = Auth.getAccessToken()
  if (!token) {
    startPolling()
    return
  }
  const base = import.meta.env.VITE_APP_BASE_API || '/api/v1'
  const es = new EventSource(`${base}/weida/events?token=${encodeURIComponent(token)}`)
  es.onopen = () => stopPolling()
  es.onerror = () => startPolling()
  es.addEventListener('record_changed', (ev: MessageEvent) => {
    try {
      const data = JSON.parse(ev.data)
      if (!config.value || data.table !== config.value.table) return
      const id = Number(data.id)
      if (role.value === 'domestic_ops' && data.table === 'biz_order') {
        void load(true)
        return
      }
      const idx = rows.value.findIndex((r) => r.id === id)
      if (idx < 0 && (role.value === 'pricing' || role.value === 'customer_service')) {
        void load(true)
        return
      }
      if (idx >= 0) {
        request<any>({ url: `/weida/${role.value}/records/${id}`, method: 'get' })
          .then((res) => {
            const updated: Row = res.data?.data
            if (updated) rows.value[idx] = updated
          })
          .catch(() => undefined)
      }
      if (detail.value?.id === id) {
        request<any>({ url: `/weida/${role.value}/records/${id}`, method: 'get' })
          .then((res) => {
            if (res.data?.data) detail.value = res.data.data
          })
          .catch(() => undefined)
      }
    } catch {
      // 忽略坏事件，3 秒轮询兜底
    }
  })
  eventSource = es
}

watch(
  () => [role.value, route.path],
  async () => {
    page.value = 1
    keyword.value = ''
    conditions.value = []
    draftConditions.value = []
    sortField.value = ''
    sortDesc.value = null
    if (isDashboard.value) {
      await loadStats()
      return
    }
    await loadConfig()
    await load()
    connectEvents()
  }
)

// 附件：记住最后一次点击的附件格，Ctrl+V 粘图片/文件就传到这一格
const activeAttachField = ref('')

async function onGlobalPaste(event: ClipboardEvent) {
  if (!drawerOpen.value || !activeAttachField.value) return
  const files = Array.from(event.clipboardData?.files || [])
  if (!files.length) return
  event.preventDefault()
  for (const file of files) {
    await uploadDetailAttachment(activeAttachField.value, { file, onSuccess: () => {}, onError: () => {} })
  }
}

onMounted(async () => {
  if (role.value === 'master-data') return
  if (isDashboard.value) {
    await loadStats()
    return
  }
  loadSeenRows()
  restoreConfigCache()
  restoreRowsCache()
  await Promise.all([loadAccountState(), loadConfig()])
  await load()
  void loadVendors()
  void loadStaffOptions()
  bindTableScroll()
  connectEvents()
  document.addEventListener('paste', onGlobalPaste)
})

onUnmounted(() => {
  disconnectEvents()
  document.removeEventListener('paste', onGlobalPaste)
})
</script>

<style scoped>
.wb {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 8px 12px;
  background: hsl(40 36% 95%);
  font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Tahoma,
    'PingFang SC', 'Microsoft Yahei', Arial, sans-serif;
  font-size: 13px;
  color: hsl(33 17% 10%);
}
.wb-breadcrumb {
  font-size: 11px;
  color: #8a8378;
  margin-bottom: 2px;
}
.wb-breadcrumb a {
  cursor: pointer;
}
.wb-head {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid hsl(38 25% 85%);
}
.wb-title {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}
.wb-title h2 {
  margin: 0;
  font-size: 16px;
  white-space: nowrap;
  color: #1f4b45;
}
.wb-actions :deep(.ant-btn),
.wb-actions :deep(.ant-select-selector) {
  height: 30px;
  font-size: 12px;
}
.wb-actions {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  align-items: center;
}
.wb-search {
  width: 260px;
}
.wb-t-search { order: 1; }
.wb-t-filtermode { order: 2; }
.wb-t-dashboard { order: 3; }
.wb-t-filter { order: 4; }
.wb-t-sort { order: 5; }
.wb-t-columns { order: 6; }
.wb-t-paint { order: 7; }
.wb-t-textcolor { order: 8; }
.wb-t-more { order: 9; }
.wb-t-new { order: 2; display: inline-flex; }
.wb-t-filtermode { order: 3; }
.wb-t-query { display: none; }
.wb-t-notify { order: 10; margin-left: auto; }
.wb-filter-col {
  width: 140px;
}
.wb-filter-val {
  width: 140px;
}
.wb-table {
  flex: 1;
  margin-top: 6px;
  overflow: auto;
}
.wb-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 10px;
}
.wb-total {
  font-size: 12px;
  color: #8a8378;
}
.cell-empty {
  color: #b5ada0;
}
.wb-status-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: stretch;
  min-width: 0;
}
.wb-status-top {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-width: 0;
}
.wb-status-flags {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}
.wb-flight-status {
  font-size: 11px;
  color: #6b7280;
  text-decoration: underline;
  text-underline-offset: 2px;
  white-space: nowrap;
}
.wb-flight-status.is-danger {
  color: #d92d20;
}
.wb-flight-status.is-empty {
  color: #b5ada0;
  text-decoration: none;
}
.wb-flight-status.is-info {
  color: #1570ef;
}
.wb-alert-dot.is-right {
  margin-left: auto;
}
.wb-alert-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  cursor: pointer;
}
.wb-alert-dot.is-critical {
  background: #d92d20;
}
.wb-alert-dot.is-warning {
  background: #f79009;
}
.wb-alert-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.wb-alert-hint { font-size: 12px; color: #8a8378; margin-bottom: 8px; }
.wb-alert-item {
  border: 1px solid #e8e2d6;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
/* 与妙搭同一套：失败/待处理用红边红底，提示用中性 */
.wb-alert-item.critical { border-color: hsl(0 84% 62%); background: hsl(0 84% 98%); }
.wb-alert-item.warning { border-color: hsl(38 92% 62%); background: hsl(38 92% 98%); }
.wb-alert-item.info { border-color: #e2dccf; background: #faf8f3; }
.wb-alert-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.wb-alert-time { font-size: 11px; color: #a8a094; flex-shrink: 0; }
.wb-alert-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
.wb-alert-badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.wb-alert-badge.critical { background: hsl(0 84% 55%); color: #fff; }
.wb-alert-badge.warning { background: hsl(38 92% 50%); color: #fff; }
.wb-alert-badge.info { background: #e8e2d6; color: #6b655c; }
.wb-alert-title {
  font-size: 13px;
  font-weight: 600;
  color: #2b2a28;
}
.wb-alert-detail {
  font-size: 12px;
  color: #6b655c;
  line-height: 1.5;
}
.wb-detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.wb-detail-drawer :deep(.el-drawer__header) {
  display: none;
}
.wb-detail-drawer :deep(.el-drawer__body) {
  padding: 0;
}
.wb-detail {
  padding: 0 20px 20px;
  height: 100%;
  overflow: auto;
}
.wb-detail-meta {
  padding: 12px 0;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-size: 12px;
  color: #8a8378;
}
.wb-detail-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wb-detail-title strong {
  font-size: 16px;
  color: #2b2a28;
}
.wb-detail-top-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.wb-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 24px;
  padding-top: 6px;
}
.wb-form :deep(.el-form-item) {
  margin-bottom: 10px;
  align-items: center;
}
.wb-form :deep(.el-form-item__label) {
  font-size: 12px;
  color: hsl(34 9% 44%);
  line-height: 32px;
  padding-right: 10px;
}
.wb-form :deep(.el-form-item__content) {
  line-height: 32px;
}
.wb-form :deep(.el-input__wrapper),
.wb-form :deep(.el-select__wrapper) {
  min-height: 32px;
  background: hsl(40 59% 98%);
}
.wb-form :deep(.el-textarea__inner) {
  background: hsl(40 59% 98%);
  font-size: 13px;
  line-height: 1.6;
}
.wb-form :deep(.el-form-item:has(textarea)),
.wb-form :deep(.el-form-item:has(.el-input-number)) {
  grid-column: span 2;
}
.wb-form-compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.wb-form-compact :deep(.el-form-item:has(textarea)) {
  grid-column: span 2;
}
.wb-weekend-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #f79009;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.wb-bill-alert-badge {
  display: inline-flex;
  width: 22px;
  height: 22px;
  cursor: pointer;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  border: 1px solid rgba(212, 160, 23, 0.55);
  background: rgba(212, 160, 23, 0.16);
  color: #b07d0a;
  font-weight: 700;
  font-size: 13px;
  line-height: 1;
}
.wb-status-clickable {
  cursor: pointer;
}
@keyframes wb-alert-flash {
  0%, 100% { box-shadow: 0 0 0 2px hsl(0 84% 45%), inset 0 0 0 1px hsl(0 84% 45% / 0.5); background: hsl(0 84% 45% / 0.1); }
  50% { box-shadow: 0 0 0 2px hsl(0 84% 45% / 0.18), inset 0 0 0 1px hsl(0 84% 45% / 0.12); background: transparent; }
}
.wb-alert-flashing {
  border-radius: 4px;
  padding: 2px;
  animation: wb-alert-flash 1.15s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
  .wb-alert-flashing { animation: none; }
}
.wb-status-pill {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
  border: 1px solid transparent;
}
.wb-color-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}
.wb-color-dot {
  width: 18px;
  height: 18px;
  border: 1px solid #d8d2c6;
  border-radius: 4px;
  cursor: pointer;
}
.cell-template {
  white-space: pre-line;
}
.wb-paint-list {
  max-height: 46vh;
  overflow-y: auto;
  margin-bottom: 10px;
  padding: 6px;
  border: 1px solid #e5dfd3;
  border-radius: 8px;
}
.wb-paint-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border-bottom: 1px solid #f0ece4;
}
.wb-view {
  width: 120px;
}
.wb-filter-conj {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  font-size: 13px;
  color: #6b655c;
}
.wb-filter-range {
  display: flex;
  flex: 1;
  gap: 6px;
  min-width: 0;
}
.wb-filter-range :deep(.el-date-editor) {
  flex: 1;
  width: auto;
}
.wb-filter-save {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.wb-col-tip {
  margin: 0 0 10px;
  font-size: 12px;
  color: #8a8378;
}
.wb-col-list {
  max-height: 46vh;
  overflow-y: auto;
  border: 1px solid #e5dfd3;
  border-radius: 8px;
  padding: 6px;
}
.wb-col-row {
  cursor: grab;
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 5px 8px;
  border-radius: 6px;
}
.wb-col-row:hover {
  background: #faf6ee;
}
.wb-col-index {
  width: 22px;
  color: #b5ada0;
  font-size: 12px;
  text-align: right;
}
.wb-col-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.wb-col-empty {
  margin: 16px 0;
  text-align: center;
  font-size: 12px;
  color: #8a8378;
}
.wb-col-add {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
.wb-col-picker {
  width: 220px;
}
.wb-tpl-label {
  width: 170px;
}
.wb-tpl-expr {
  flex: 1;
}
.wb-detail-drawer {
  position: relative;
}
.wb-drawer-resizer {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 8px;
  z-index: 10;
  cursor: col-resize;
  background: transparent;
}
.wb-drawer-resizer:hover {
  background: rgba(31, 75, 69, 0.08);
}
.wb-staff-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.wb-create {
  padding-top: 4px;
}
.wb-create-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 22px;
}
.wb-create-field {
  min-width: 0;
}
.wb-create-field label {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
  color: #4b463e;
}
.wb-create-wide {
  grid-column: span 2;
}
.wb-required {
  color: #d9534f;
}
.wb-size-create {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.wb-size-create > :first-child {
  flex: 1;
}
.wb-create-hint {
  margin-top: 14px;
  padding: 8px 10px;
  font-size: 12px;
  color: #b06a00;
  background: #fdf3e3;
  border-radius: 6px;
}
.wb-order-card {
  border: 1px solid #e5dfd3;
  border-radius: 8px;
  padding: 16px 18px 12px;
  background: #fffdf9;
}
.wb-order-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px 22px;
}
.wb-order-field {
  min-width: 0;
}
.wb-order-field :deep(.el-input__inner) {
  text-align: left;
}
.wb-order-field :deep(.el-input),
.wb-order-field :deep(.el-select),
.wb-order-field :deep(.el-date-editor) {
  width: 100%;
}
.wb-create-field :deep(.el-input),
.wb-create-field :deep(.el-select),
.wb-create-field :deep(.el-date-editor) {
  width: 100%;
}
.wb-order-field label {
  display: block;
  margin-bottom: 3px;
  font-size: 12px;
  color: #8a8378;
}
.wb-order-wide {
  grid-column: span 2;
}
.wb-order-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.wb-order-readonly {
  display: block;
  min-height: 32px;
  padding: 5px 0;
}
.wb-goods-edit {
  grid-column: span 2;
}
.wb-subhead {
  grid-column: 1 / -1;
  margin: 8px 0 2px;
  padding-bottom: 4px;
  border-bottom: 1px dashed #e5dfd3;
  color: #1f4b45;
  font-size: 13px;
}
.wb-size-edit {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.wb-size-edit > :first-child {
  flex: 1;
  min-width: 0;
}
.wb-readonly-text {
  min-height: 32px;
  line-height: 1.7;
  white-space: pre-line;
  word-break: break-all;
}
.wb-quote-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
  color: #6b655c;
  font-size: 13px;
}
.wb-quote-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}
.wb-review-card {
  margin-top: 14px;
  border: 1px solid #e5dfd3;
  border-radius: 8px;
  padding: 14px 16px;
}
.wb-review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.wb-review-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  font-size: 13px;
}
.wb-review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 24px;
  font-size: 13px;
}
.wb-review-grid label {
  display: inline-block;
  min-width: 92px;
  color: #8a847a;
}
.wb-goods-value {
  min-height: 22px;
  margin-bottom: 6px;
  color: #2b2a28;
  font-size: 13px;
  word-break: break-all;
  white-space: pre-line;
}
.wb-goods-size {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wb-goods-inputs {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
}
.wb-goods-inputs .el-input {
  width: 100%;
}
.wb-order-attach {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e5dfd3;
}
.wb-native-input {
  width: 100%;
  box-sizing: border-box;
  height: 24px;
  padding: 0 6px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  font-size: 12px;
  color: #303133;
  background: #fff;
}
.wb-native-input:focus {
  outline: none;
  border-color: #409eff;
}
.wb-ifreight-expand {
  position: relative;
  z-index: 2;
}
.wb-ifreight-detail {
  padding: 8px 14px;
}
.wb-ifreight-detail-row {
  display: grid;
  grid-template-columns: repeat(var(--cols, 2), minmax(0, 1fr));
  gap: 12px 28px;
  margin-bottom: 14px;
}
.wb-ifreight-detail-field {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 3px;
  min-width: 0;
}
.wb-ifreight-detail-field > span {
  font-size: 12px;
  color: #8a8378;
  white-space: nowrap;
}
.wb-native-area {
  height: auto;
  min-height: 46px;
  padding: 4px 6px;
  line-height: 1.4;
  resize: vertical;
  font-family: inherit;
}
.wb-ifreight-detail-field :deep(.el-input),
.wb-ifreight-detail-field :deep(.el-textarea) {
  flex: 1 1 auto;
  min-width: 0;
}
.wb-ifreight-detail-field.wb-ops-wide {
  flex-basis: 100%;
  max-width: none;
}
.wb-ins-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 6px 18px;
  padding: 8px 14px;
}
.wb-ins-field label {
  display: block;
  font-size: 12px;
  color: #8a8378;
}
.wb-ins-field div {
  font-size: 13px;
  color: #2f2a24;
  white-space: pre-wrap;
  word-break: break-word;
}
.wb-muted {
  color: #8a8378;
  font-size: 12px;
  white-space: pre-wrap;
}
.wb-attach-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e5dfd3;
}
.wb-attach-inner + .wb-attach-inner {
  margin-top: 10px;
}
.wb-attach-block h4 {
  margin: 0 0 6px;
  font-size: 13px;
  color: #6b655c;
}
.wb-attach-row {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 2px;
  min-width: 0;
}
.wb-attach-row :deep(.el-link__inner) {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wb-fee-plan {
  margin-bottom: 10px;
  color: #6b655c;
  font-size: 13px;
}
.wb-fee-cell {
  display: block;
  min-height: 22px;
  cursor: text;
}
.wb-fee-form {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr)) auto;
  gap: 8px;
  margin-top: 14px;
}
.wb-price-expand {
  padding: 4px 16px 12px 48px;
}
.wb-price-expand-row {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
  line-height: 1.6;
}
.wb-price-expand-label {
  color: #6b655c;
  text-align: right;
}
.wb-approval-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.wb-approval-label {
  font-size: 13px;
  color: #6b655c;
}
.wb-plan-footer {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #eee8dc;
}
.wb-plan-footer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}
.wb-plan-note {
  font-size: 12px;
  color: #8a8378;
}
.cell-template {
  color: #1f4b45;
}
.wb-readonly {
  display: block;
  min-height: 32px;
  padding: 5px 0;
  color: #2b2a28;
  word-break: break-all;
}

.wb-table :deep(.wb-row-unread td) { font-weight: 700; color: #000; }

/* 表头由 Ant 的 scroll.y 固定，横向滚动交给 .ant-table-body */
.wb-table :deep(.ant-table) {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.wb-table :deep(.ant-table-thead > tr > th) {
  position: relative;

  background: hsl(38 33% 91%);
  color: hsl(34 9% 44%);
  font-size: 12px;
  font-weight: 500;
  height: 40px;
  padding: 8px 12px;
}
.wb-table :deep(.ant-table-thead > tr > th::after) {
  content: '';
  position: absolute;
  top: 8px;
  right: 0;
  bottom: 8px;
  width: 5px;
  cursor: col-resize;
}
.wb-table :deep(.ant-table-tbody > tr:hover > td) {
  background: hsl(38 33% 94%);
}
.wb-table :deep(.ant-table-tbody > tr > td) {
  border-bottom: 1px solid hsl(38 25% 90%);
  padding: 7px 12px;
}
.wb-table :deep(.ant-table table) {
  table-layout: fixed;
}
.wb-table :deep(.ant-table-body) {
  overflow: auto !important;
}
.wb-flight {
  padding: 8px 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.wb-flight-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.wb-flight-row > label {
  width: 120px;
  flex: none;
  color: #8a8378;
  font-size: 13px;
}
.wb-flight-text {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
}

.wb-goods-edit-list { max-height: 520px; overflow: auto; }
.wb-goods-edit-item { border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.wb-goods-edit-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #374151; }
.wb-goods-edit-title span { margin-left: 8px; font-weight: 400; color: #6b7280; }
.wb-goods-edit-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; }
.wb-goods-edit-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #6b7280; }
.wb-goods-edit-grid label.wide { grid-column: 1 / -1; }

.wb-cell-clip {
  height: var(--wb-row-height, 34px);
  overflow: hidden;
  white-space: pre-wrap;
  line-height: 1.45;
  word-break: break-word;
}
.wb-table :deep(.ant-table-tbody > tr > td) {
  padding-top: 5px;
  padding-bottom: 5px;
  vertical-align: top;
}

.wb-title-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.wb-subtitle {
  font-size: 11px;
  color: #a39a8c;
  white-space: nowrap;
}
.wb-version {
  font-size: 11px;
  color: #a39a8c;
  border: 1px solid #e5dfd3;
  border-radius: 999px;
  padding: 0 6px;
  line-height: 16px;
}
.wb-role-switch {
  width: 104px;
  flex: none;
}

.wb-notify-list { max-height: 480px; overflow: auto; }
.wb-notify-item { border-bottom: 1px solid #eee7db; padding: 10px 2px; }
.wb-notify-item.unread { background: #fbf6ea; }
.wb-notify-title { font-size: 13px; font-weight: 600; }
.wb-notify-body { font-size: 12px; color: #6b655c; margin-top: 4px; }
.wb-notify-time { font-size: 11px; color: #a39a8c; margin-top: 4px; }

/* 操作台分 tag 顺序按妙搭截图 */
.wb-tabs :deep(.ant-tabs-nav-list) { display: flex; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-货物详情"])) { order: 1; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-提货计划"])) { order: 2; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-fleet"])) { order: 3; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-报关"])) { order: 4; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-代理到货计划"])) { order: 5; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-国外预报"])) { order: 6; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-insurance"])) { order: 7; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-做账明细"])) { order: 8; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-开票"])) { order: 9; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-flight"])) { order: 10; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-ifreight"])) { order: 11; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-ai_fees"])) { order: 12; }
.wb-tabs :deep(.ant-tabs-tab:has(.ant-tabs-tab-btn[id$="tab-arrival_report"])) { order: 13; }

/* 仪表盘：按妙搭页面重排 */
.wb-dash {
  height: 100%;
  overflow-y: auto;
  padding: 0 24px 40px;
  background: hsl(40 36% 95%);
}
.wb-dash-head {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid hsl(38 25% 88%);
  background: hsl(40 36% 95% / 0.94);
  backdrop-filter: blur(6px);
}
.wb-dash-title-group {
  display: flex;
  align-items: center;
  gap: 14px;
}
.wb-dash-back {
  font-size: 12px;
  color: hsl(34 9% 44%);
  cursor: pointer;
}
.wb-dash-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  line-height: 1.6;
}
.wb-dash-head p {
  margin: 0;
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wb-dash-tools {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.wb-dash-cards {
  margin-top: 18px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.wb-dash-card {
  min-width: 132px;
  padding: 12px 16px;
  border: 1px solid hsl(38 25% 85%);
  border-radius: 12px;
  background: hsl(40 59% 98%);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wb-dash-card .v {
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}
.wb-dash-card .l {
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wb-dash-summary {
  margin-top: 18px;
  padding: 24px;
  border: 1px solid hsl(38 25% 85%);
  border-radius: 12px;
  background: hsl(40 59% 98%);
  display: flex;
  align-items: flex-end;
  gap: 40px;
  flex-wrap: wrap;
}
.wb-dash-summary .big {
  font-size: 40px;
  line-height: 1;
  color: hsl(190 64% 26%);
  font-variant-numeric: tabular-nums;
}
.wb-dash-summary .items {
  display: flex;
  gap: 40px;
  font-size: 13px;
  color: hsl(34 9% 44%);
}
.wb-dash-summary .items span {
  display: block;
}
.wb-dash-reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 12px;
}
.wb-dash-reasons .lbl {
  font-size: 12px;
  color: hsl(34 9% 44%);
  margin-right: 4px;
}
.wb-dash-sections {
  display: flex;
  flex-direction: column;
  gap: 20px;
  margin-top: 20px;
}
.wb-dash-section {
  padding: 24px;
  border: 1px solid hsl(38 25% 85%);
  border-radius: 12px;
  background: hsl(40 59% 98%);
}
.wb-dash-section h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  line-height: 1.6;
}
.wb-dash-section > p {
  margin: 2px 0 0;
  font-size: 12px;
  color: hsl(34 9% 44%);
}
.wb-col-chart {
  display: flex;
  gap: 16px;
  margin-top: 20px;
}
.wb-col-axis {
  width: 34px;
  flex: none;
  height: 340px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  text-align: right;
  font-size: 11px;
  line-height: 1;
  color: hsl(34 9% 44%);
  font-variant-numeric: tabular-nums;
}
.wb-col-plot {
  position: relative;
  flex: 1;
  min-width: 0;
}
.wb-col-grid {
  position: absolute;
  inset: 0 0 auto 0;
  height: 340px;
  pointer-events: none;
}
.wb-col-grid i {
  position: absolute;
  left: 0;
  right: 0;
  border-top: 1px dashed hsl(38 25% 85%);
}
.wb-col-bars {
  position: relative;
  display: flex;
  gap: 16px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.wb-col-item {
  flex: 1 1 0;
  min-width: 96px;
  max-width: 192px;
  display: flex;
  flex-direction: column;
  align-items: center;
  cursor: pointer;
}
.wb-col-space {
  height: 340px;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.wb-col-bar {
  width: 100%;
  max-width: 64px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
  border-radius: 4px 4px 0 0;
}
.wb-col-dark {
  background: hsl(190 64% 26%);
}
.wb-col-light {
  flex: 1;
  background: hsl(190 64% 26% / 0.15);
}
.wb-col-name {
  margin-top: 8px;
  min-height: 32px;
  width: 100%;
  text-align: center;
  font-size: 12px;
  line-height: 1.25;
  word-break: break-word;
}
.wb-col-caption {
  margin-top: 2px;
  width: 100%;
  text-align: center;
  font-size: 11px;
  line-height: 16px;
  color: hsl(34 9% 44%);
  font-variant-numeric: tabular-nums;
}
.wb-col-empty {
  height: 256px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: hsl(34 9% 44%);
  font-size: 13px;
}
</style>
