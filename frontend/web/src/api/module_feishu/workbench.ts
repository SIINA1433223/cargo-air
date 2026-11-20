import request, { type ExtendedRequestConfig } from '@utils/http'

const API_PATH = '/feishu'

/**
 * 静默请求：成功时不弹 toast。
 *
 * 索引页「改完即写」逐格保存，每格弹一次「更新记录成功」会把界面淹掉，所以这几处
 * 走静默。单独包一层是因为 axios 实例的调用签名只认 `AxiosRequestConfig`，直接传对象
 * 字面量会被多余属性检查挡住；经由这个形参反倒能按 `ExtendedRequestConfig` 校验。
 */
function silent<T>(config: ExtendedRequestConfig) {
  return request<T>(config)
}

/** 飞书字段类型枚举（只列本系统用到的） */
export const FIELD_TYPE = {
  TEXT: 1,
  NUMBER: 2,
  SINGLE_SELECT: 3,
  MULTI_SELECT: 4,
  DATE: 5,
  CHECKBOX: 7,
  USER: 11,
  PHONE: 13,
  URL: 15,
  ATTACHMENT: 17,
  SINGLE_LINK: 18,
  LOOKUP: 19,
  FORMULA: 20,
  DUPLEX_LINK: 21,
  /**
   * 按钮字段。没有值，详情页只能摆个占位（点它得去飞书，那边才有对应的自动化）。
   *
   * 只随 `WorkbenchConfig.detail_fields` 下发，不进 `WorkbenchConfig.fields`——
   * 那个清单是「自定义列」的可选池，把没有值的按钮混进去是噪音。
   */
  BUTTON: 3001,
} as const

export interface FieldMeta {
  name: string
  type: number
  ui_type: string
  read_only: boolean
  editable: boolean
  options: string[]
  /** 该字段能否多选（目前只有人员字段会给出 false，取自飞书字段属性） */
  multiple: boolean
  /** 飞书里给这个字段写的说明；详情页显示在字段名下方（多数字段为空串） */
  description: string
  /**
   * 关联字段（单向 18 / 双向 21）**指向哪张表**；非关联字段为空串。
   *
   * 详情页靠它把「最终对客方案」和下方那张对客方案表格**对上号**：两者指向同一张表，
   * 所以前者的候选就是后者已经拉回来的那几条记录，不必再打一次接口。
   * 拿表名或字段名去猜都不行——要显示「对方叫什么」得知道查哪张表。
   */
  link_table_id?: string
}

export interface WorkbenchGroup {
  label: string
  fields: FieldMeta[]
}

/**
 * 详情抽屉里**另一张表**的一页（如国内操作订单详情里的「iFreight 数据」）。
 *
 * 与 `WorkbenchGroup` 不同：那一组的字段是本表字段的一个划分，这一页的字段长在**别的表**上，
 * 行也不来自本表——要拿当前记录的 id 去 `getLinkedRecords(key, [record_id])` 反查。
 * 一条本表记录可能对应**好几条**从记录（一张订单挂两条 iFreight 是常态），所以这一页摆成
 * 一行一条、末端箭头展开看整条的表格（见抽屉的 `isTableBlock`）。
 */
export interface RelatedBlock {
  /** 这一页的标识；也是 `linked-records` 接口上的 `field` 参数 */
  key: string
  label: string
  fields: FieldMeta[]
}

/** 列表排序的一项：按数组顺序依次比较（服务端排序，不是只排当前页） */
export interface WorkbenchSortField {
  field: string
  desc: boolean
}

export interface WorkbenchConfig {
  key: string
  name: string
  description: string
  /** 列表列顺序；其中「货物数据」是前端聚合列，不是真实字段 */
  columns: string[]
  cargo_fields: string[]
  /** 待办项（勾选框字段名），每个可单独筛选 */
  todo_checkboxes: string[]
  search_fields: string[]
  merge_master_field: string
  merge_hawb_field: string
  /** 列表排序规则，按顺序依次比较；为空表示用飞书默认顺序 */
  sort: WorkbenchSortField[]
  /** 详情页顶部常驻显示的基础信息（不是 Tab） */
  base_fields: FieldMeta[]
  groups: WorkbenchGroup[]
  /** 本表全部字段元数据（含不在任何分组里的），供「自定义列」挑字段用 */
  fields?: FieldMeta[]
  /**
   * 详情页要铺开的字段清单：**按飞书表里的字段顺序**、**含按钮**。
   *
   * 与 `fields` 的区别：那个是「自定义列」的可选池（按钮没有值，被剔掉了），
   * 这个是照飞书记录详情面板原样排的那一份。
   */
  detail_fields?: FieldMeta[]
  /**
   * 详情抽屉的 **Tab 划分**（`detail_fields` 的一个划分，合起来就是它）。
   *
   * 空数组 = 不切 Tab，把 `detail_fields` 铺在一页上。字段多到一屏放不完的表
   * （商机 49 个）才配它，别给每一张表都摆一排单按钮的 Tab。
   */
  detail_groups?: WorkbenchGroup[]
  /**
   * 详情抽屉里**另一张表**的页（如国内操作的「iFreight 数据」）。
   *
   * 与 `detail_groups` 并列、互不重叠：那一组是本表字段的划分，这一页要按 `key` 单独去取。
   * 空数组 = 本工作台只有一张表。
   */
  related_blocks?: RelatedBlock[]
  /** 索引页「新建」按钮上的字（如「新建商机」） */
  create_label: string
  /**
   * 是否有可用的新建表单（后端配了飞书**表单视图**才有）。
   *
   * 没有就别显示「新建」按钮：新建抽屉是照表单视图的字段渲染的，没有视图就是一个**空抽屉**，
   * 点开只会一脸茫然。订单表这类「记录由飞书自动化生成、业务根本没建过表单」的表就是这种。
   */
  can_create: boolean
  /**
   * 详情保存时是否允许「同步到同组合并组」。操作工作台为 `false`（单票单同步），
   * 此时不显示同步勾选框，后端也会忽略该参数。
   */
  allow_group_sync?: boolean
}

/**
 * 新建表单里的一个字段——**照飞书表单视图的定义来**，不是照 config 的分组。
 *
 * `title` 与 `name` 是两回事：表单里可以把标题改成任意文字（商机表的「目的港」
 * 实际指向字段「目的 港」）。显示用 `title`，提交用 `name`。
 */
export interface CreateFormField {
  /** 飞书字段名（提交时的 key） */
  name: string
  /** 表单上显示的标题 */
  title: string
  required: boolean
  description: string
  type: number
  ui_type: string
  /** 该挂什么控件；`readonly` 表示本系统填不了 */
  control: 'text' | 'number' | 'single' | 'multi' | 'date' | 'checkbox' | 'attachment' | 'link' | 'user' | 'readonly'
  editable: boolean
  options: string[]
  /** 关联字段指向的表 id（`control === 'link'` 时才有） */
  link_table_id: string
  /**
   * 该字段能否多选。
   *
   * 目前只有人员字段会用到（由飞书字段的 `property.multiple` 决定）：单选的人员字段
   * 被选上两个人，飞书会整单拒掉。其余控件一律为 `true`。
   */
  multiple: boolean
  /**
   * 打开新建抽屉时预选的值（open_id 数组，单选字段也包一层）。
   *
   * 由后端按 `WorkbenchConfig.user_defaults` 把姓名换成 id 后下发——配置里写姓名，
   * 控件认的是 id。没配默认值的字段是空数组，此时**不要**预填空值。
   */
  default: string[]
}

/** 关联字段的候选记录 */
export interface LinkOption {
  record_id: string
  text: string
}

/**
 * 候选范围的业务约束：关联表里某一列命中 `values` 中的**任一个**才进候选。
 *
 * 用在「字段指向整张表、但业务上只认其中一部分」的关联上——订单表的「供应商」指向
 * 客商表，可选值只有角色含「订舱代理」的那些。
 */
export interface LinkOptionFilter {
  /** 关联表里的字段名（不是当前表的） */
  field: string
  /** 命中任一项即可；多选字段按「含有其中一项」判 */
  values: readonly string[]
}

/** 关联记录的完整内容：目标表的字段定义 + 点名要的那几条记录 */
export interface LinkedRecordsResult {
  /**
   * 关联表的 table_id。
   *
   * 下载**这张表上的附件**时要把它一起发回去（飞书按 tableId 判权限，见 `downloadFile`），
   * 而这是前端唯一能学到它的地方——关联表不属于任何工作台，`getConfig` 取不到它。
   */
  table_id: string
  /**
   * 关联表的字段定义（按该表的字段顺序）。
   *
   * 必须由后端一起给：关联表不属于任何工作台，`getConfig` 取不到它，前端没法自己拼。
   */
  fields: FieldMeta[]
  records: BitableRecord[]
}

/**
 * 该工作台全部反查页的数据，按 `RelatedBlock.key` 索引。
 *
 * 形状与 `LinkedRecordsResult` 逐字段一致（服务端就是这么裁的），所以消费方不必区分
 * 这两条路径。没有配反查页的工作台得到空对象。
 */
export type RelatedBlocksResult = Record<string, LinkedRecordsResult>

/** 人员字段的候选成员（飞书通讯录） */
export interface UserOption {
  /** open_id，写回飞书时用的就是它 */
  open_id: string
  name: string
}

/** 记录里人员字段当前的成员（`fields` 里只有姓名，改人的时候要用 id） */
export interface UserRef {
  id: string
  name: string
}

export interface BitableRecord {
  record_id: string
  fields: Record<string, unknown>
  /** 关联字段的原始 record_id（合并组计算用；fields 里存的是给人看的文本） */
  link_ids: Record<string, string[]>
  /** 人员字段当前的成员（改人要用 open_id；fields 里存的是给人看的姓名） */
  user_refs: Record<string, UserRef[]>
  /**
   * 「单票状态」（客服）。**不是飞书字段**，是后端按 `WorkbenchConfig.single_status` 跨表
   * 算出来的派生值，所以放在顶层而不是 `fields` 里——`fields` 的每个键都对应一个飞书字段，
   * 凭空多一个没有字段定义的键会被当成可选列列出来。没开这个开关的工作台不带这个键。
   */
  single_status?: string
}

export interface WorkbenchPageResult {
  items: BitableRecord[]
  total: number
  /** 下一页游标；为空表示没有下一页 */
  page_token: string | null
  has_more: boolean
  pending: string | null
}

export interface PendingCount {
  field: string
  pending: number
  done: number
}

export interface WorkbenchStats {
  total: number
  pending: PendingCount[]
}

export interface WorkbenchQuery {
  /** 按单个待办项筛选（勾选框字段名） */
  pending?: string
  page_token?: string
  page_size?: number
  search?: string
}

export type MergeType = 'master_awb' | 'hawb'

/**
 * 订舱类字段：改动这些字段时可选择连带同步到同合并组的其他票。
 * 与后端 `workbench.py` 的 `MERGE_SYNC_FIELDS` 保持一致。
 *
 * **不含「计划」**：计划下发是 B+A 方案，飞书只勾当前票，同组其余票仅前端显示已计划。
 */
export const MERGE_SYNC_FIELDS: readonly string[] = [
  '订舱单',
  'ETD',
  '订舱代理',
  '航司',
  '价格',
  '代理deadline',
  '操作日',
  'tracking时效要求',
  '车队时效要求',
]

const FeishuWorkbenchAPI = {
  getConfig(role: string) {
    return request<ApiResponse<WorkbenchConfig>>({ url: `${API_PATH}/workbench/${role}/config`, method: 'get' })
  },

  getRecords(role: string, params: WorkbenchQuery) {
    return request<ApiResponse<WorkbenchPageResult>>({
      url: `${API_PATH}/workbench/${role}/records`,
      method: 'get',
      params,
    })
  },

  getStats(role: string) {
    return request<ApiResponse<WorkbenchStats>>({ url: `${API_PATH}/workbench/${role}/stats`, method: 'get' })
  },

  getRecord(role: string, recordId: string, invoiceNo?: string) {
    return request<ApiResponse<BitableRecord>>({
      url: `${API_PATH}/workbench/${role}/records/${recordId}`,
      method: 'get',
      params: invoiceNo ? { invoice_no: invoiceNo } : undefined,
    })
  },

  /**
   * 新建表单的字段定义（读飞书表单视图；没配表单视图时返回空数组，调用方回落到按分组渲染）。
   */
  getCreateForm(role: string) {
    return request<ApiResponse<CreateFormField[]>>({ url: `${API_PATH}/workbench/${role}/create-form`, method: 'get' })
  },

  /**
   * 关联字段的候选记录（取目标表主字段作为显示文本）。
   *
   * `tableId` 是 `field` **长在哪张表**上（父表），留空即本工作台的表。二级关联要传：
   * 订单表的「供应商」长在订单表上，服务端只扫工作台表会找不到这个字段。
   *
   * `filter` 收窄候选本身的范围（不是搜关键字）：订单表的「供应商」指向整张客商表，
   * 但只有角色含「订舱代理」的才是供应商——不筛的话客户、报关行、车队全混在下拉里。
   */
  getLinkOptions(role: string, field: string, search?: string, tableId = '', filter?: LinkOptionFilter) {
    return request<ApiResponse<LinkOption[]>>({
      url: `${API_PATH}/workbench/${role}/link-options`,
      method: 'get',
      params: {
        field,
        search,
        table_id: tableId,
        filter_field: filter?.field,
        filter_values: filter?.values,
      },
    })
  },

  /** 人员字段的候选成员（飞书通讯录，取整个租户；后端缓存 10 分钟）。 */
  getUserOptions(role: string, search?: string) {
    return request<ApiResponse<UserOption[]>>({
      url: `${API_PATH}/workbench/${role}/user-options`,
      method: 'get',
      params: { search },
    })
  },

  /**
   * 关联记录的完整内容（含目标表全部字段的值与字段定义）。
   *
   * 与 `getLinkOptions` 的分工：那个只给主字段文本、用于**挑选**；这个给整条记录、
   * 用于**展示**（如商机详情里的「航线报价」分区）。
   */
  getLinkedRecords(role: string, field: string, recordIds: string[], tableId = '') {
    return request<ApiResponse<LinkedRecordsResult>>({
      url: `${API_PATH}/workbench/${role}/linked-records`,
      method: 'get',
      // 后端收的是可重复的 query 参数，axios 对数组默认就序列化成 `record_ids=a&record_ids=b`
      params: { field, record_ids: recordIds, table_id: tableId },
    })
  },

  /**
   * 一次取回该工作台**全部反查页**（`RelatedBlock`）的数据，按页 key 索引。
   *
   * 与逐页调 `getLinkedRecords` 的区别：国内操作那三页（iFreight 数据 / 复制提单编组 /
   * 投保编组）指向**同一张从表**、回指字段也同一个，逐页取数是各扫一遍全表——实测三个
   * Tab 合计 4.4 秒，其中两次纯属重复。这个接口在服务端按 `(表, 回指字段)` 分组，
   * 一组只扫一次再按页裁列，三个 Tab 变成一次一秒半。
   *
   * 每页返回的形状与 `getLinkedRecords` 完全一致，所以调用方可以无差别地用同一套逻辑
   * 往各页里塞。
   */
  getRelatedBlocks(role: string, recordIds: string[]) {
    return request<ApiResponse<RelatedBlocksResult>>({
      url: `${API_PATH}/workbench/${role}/related-blocks`,
      method: 'get',
      params: { record_ids: recordIds },
    })
  },

  /**
   * 改**关联表**里的一条记录（详情页展开的报价卡片就地改）。
   *
   * 与 `updateRecord` 的区别：那个写本工作台的表，这个写 `field` 指向的那张表——
   * 报价卡片是另一张表里的行，拿商机表的字段类型去写会一个字段都认不出。
   * 走 `silent`：逐格保存，拦截器每格弹一条「更新记录成功」会刷屏。
   *
   * `tableId` 是 `field` **长在哪张表**上（父表），留空即本工作台的表。二级关联要传：
   * 对客方案的「费用明细」长在对客方案表上，服务端只扫工作台表会找不到这个字段。
   */
  updateLinkedRecord(role: string, field: string, recordId: string, fields: Record<string, unknown>, tableId = '') {
    return silent<ApiResponse<{ record_id: string; written: number }>>({
      url: `${API_PATH}/workbench/${role}/linked-records/${recordId}`,
      method: 'put',
      params: { field, table_id: tableId },
      data: { fields },
      showSuccessMessage: false,
    })
  },

  /**
   * 在关联表里新建一条记录（详情页「新增航线报价」）。
   *
   * `fields` 里**必须自带回指当前商机的关联字段**，服务端不替调用方猜——没有回指
   * 的新记录在飞书里是条没人认领的孤儿行。`tableId` 同 `updateLinkedRecord`。
   */
  createLinkedRecord(role: string, field: string, fields: Record<string, unknown>, tableId = '') {
    return request<ApiResponse<{ record_id: string; written: number }>>({
      url: `${API_PATH}/workbench/${role}/linked-records`,
      method: 'post',
      params: { field, table_id: tableId },
      data: { fields },
    })
  },

  /**
   * 删掉关联表里的一条记录（对客方案费用明细的「删除」）。
   *
   * **不可逆**，调用方必须先二次确认；服务端只负责「说了删就删」。
   * 只删记录本身，双向关联的另一侧由飞书自己维护。
   */
  deleteLinkedRecord(role: string, field: string, recordId: string, tableId = '') {
    // 不是 `silent`：删除是一次性动作，拦截器弹的那条「删除记录成功」就是回执
    //（逐格保存那种高频写入才需要静音，见 updateLinkedRecord）
    return request<ApiResponse<{ record_id: string }>>({
      url: `${API_PATH}/workbench/${role}/linked-records/${recordId}`,
      method: 'delete',
      params: { field, table_id: tableId },
    })
  },

  /**
   * 新建一条记录。
   *
   * 只读字段后端会忽略（公式/自动编号由飞书自己算）。走普通 `request` 而非 `silent`：
   * 建记录不是逐格保存那种高频操作，拦截器那一条「新建记录成功」正好当回执。
   */
  createRecord(role: string, fields: Record<string, unknown>) {
    return request<ApiResponse<{ record_id: string; written: number }>>({
      url: `${API_PATH}/workbench/${role}/records`,
      method: 'post',
      data: { fields },
    })
  },

  updateRecord(
    role: string,
    recordId: string,
    fields: Record<string, unknown>,
    syncGroup = false,
    options: { showSuccessMessage?: boolean } = {}
  ) {
    return silent<ApiResponse<{ record_id: string; written: number; group_synced: number }>>({
      url: `${API_PATH}/workbench/${role}/records/${recordId}`,
      method: 'put',
      data: { fields, sync_group: syncGroup },
      showSuccessMessage: options.showSuccessMessage,
    })
  },

  /**
   * 在目标表新建一个文本字段——索引页「空列」的载体。
   *
   * 注意这是**改飞书生产表结构**：字段建出来所有表协作者可见，删要人工去飞书删。
   * 关掉成功提示，由调用方给一次带字段名的明确确认。
   */
  createField(role: string, name: string) {
    return request<ApiResponse<{ name: string; type: number }>>({
      url: `${API_PATH}/workbench/${role}/fields`,
      method: 'post',
      data: { name },
    })
  },

  /** 改飞书字段名（「空列」改列名）；只改名字，字段里的数据不动。 */
  renameField(role: string, fieldName: string, newName: string) {
    return request<ApiResponse<{ name: string; type: number }>>({
      url: `${API_PATH}/workbench/${role}/fields/${encodeURIComponent(fieldName)}`,
      method: 'put',
      data: { name: newName },
    })
  },

  merge(recordIds: string[], mergeType: MergeType, targetNo?: string) {
    return request<ApiResponse<{ merged_count: number; detached_count: number }>>({
      url: `${API_PATH}/records/merge`,
      method: 'post',
      data: { record_ids: recordIds, merge_type: mergeType, target_no: targetNo },
    })
  },

  unmerge(recordIds: string[], mergeType: MergeType) {
    return request<ApiResponse<{ unmerged_count: number; remaining_count: number }>>({
      url: `${API_PATH}/records/unmerge`,
      method: 'post',
      data: { record_ids: recordIds, merge_type: mergeType },
    })
  },

  /**
   * 下载飞书附件（后端代理，带 tenant_access_token 转发）。
   *
   * 必须走这个接口：附件的原始 URL 直接访问会报 `99991661 Missing access token`（§12.3），
   * 而前端也不允许显示原始 URL（§10.3）。
   *
   * `responseType: 'blob'` 时 http 拦截器直通整个 AxiosResponse（不走 JSON 解析），
   * 所以这里拿 `res.data` 就是 Blob；下载失败时后端返回的 JSON 错误体也会被
   * 拦截器解析成可读提示，不会变成一个内容是 JSON 的「坏文件」。
   */
  /**
   * 下载/预览飞书附件（后端代理）。
   *
   * `role` 与 `tableId` 必须传：附件不一定挂在本工作台的表上（商机自己没有任何附件字段，
   * 真实附件都在「对客方案」「航线报价」这类关联表里），而飞书按 **base + tableId** 判附件权限。
   * 不传 role 会落到富士康 base（飞书回 `[500] 无响应体`），传了 role 但 tableId 是主表则
   * `[403] 无响应体`——两种都实测复现过。富士康那套页面不传，行为与改动前一致。
   */
  downloadFile(fileToken: string, role?: string, tableId?: string) {
    return request<Blob>({
      url: `${API_PATH}/records/file/download`,
      method: 'get',
      params: { file_token: fileToken, role: role || undefined, table_id: tableId || undefined },
      responseType: 'blob',
    })
  },

  /**
   * 上传附件到飞书，返回可直接写进附件字段的 `{file_token, name, size, type}`。
   *
   * 注意：**上传完的文件在写回记录之前不能下载/预览**（实测：未挂到任何记录上的
   * file_token 下载返回 403，挂在记录上的正常）。所以要先保存再预览。
   */
  uploadFile(file: File, role?: string) {
    const formData = new FormData()
    formData.append('file', file)
    return request<ApiResponse<{ file_token: string; name: string; size: number; type: string }>>({
      url: `${API_PATH}/records/file/upload`,
      method: 'post',
      params: { role: role || undefined },
      data: formData,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

export default FeishuWorkbenchAPI
