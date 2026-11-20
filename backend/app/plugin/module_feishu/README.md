# module\_feishu — 飞书多维表格业务工作台

## 模块定位

把飞书多维表格「出货明细」（空运货代业务，178 字段 / 约 2000 条记录）做成体验更好的
**客服 / 操作工作台**前端：列表、待办、详情分组表单、合并组。

边界：**读写直接透传飞书，本地不落库**——飞书是唯一数据源，双向同步天然成立，
且飞书侧已有的公式、查找引用、自动编号、按钮（审批/生成单据等）自动化全部原样保留，
本模块只读其计算结果、不重写这些逻辑。属于业务插件层（`app/plugin/`），不依赖任何内核槽位。

## 入口

| 入口类型       | 路径             | 说明                                                                 |
| -------------- | ---------------- | -------------------------------------------------------------------- |
| 插件入口       | `plugin.py`      | `class Plugin(PluginBase)`；`PLUGIN = Plugin()` 不可省；`setup()` 留空 |
| HTTP 路由      | `controller.py`  | `FeishuRouter`，容器前缀 `/feishu`（19 条，由 discover 自动挂载）     |
| WebSocket 路由 | 无               | —                                                                    |
| ORM 模型       | 无               | 不落库，故无表；`ctx.add_models` 不调用                              |
| 种子数据       | 无               | 数据来自飞书，不需要初始化数据                                        |
| 内核槽位       | 无               | 不提供也不消费槽位                                                    |
| 定时任务       | 无               | 无同步任务（透传即实时）                                              |
| 事件订阅       | 无               | —                                                                    |

端点（完整路径前缀 `/api/v1`）：

| 方法   | 路径                                          | 说明                                     |
| ------ | --------------------------------------------- | ---------------------------------------- |
| GET    | `/feishu/workbench/{role}/config`             | 工作台配置（列/待办项/详情分组 + 字段元数据） |
| GET    | `/feishu/workbench/{role}/records`            | 记录列表（`pending` 待办项 + `search` 关键字，游标分页） |
| GET    | `/feishu/workbench/{role}/stats`              | 统计（总数 + 各待办项未勾选/已勾选数）    |
| GET    | `/feishu/workbench/{role}/records/{id}`       | 单条详情（可传 `invoice_no` 走更快路径）  |
| POST   | `/feishu/workbench/{role}/records`            | 新建记录（表单视图字段）                 |
| PUT    | `/feishu/workbench/{role}/records/{id}`       | 更新记录（`sync_group` 控制整组联动）     |
| GET    | `/feishu/workbench/{role}/create-form`        | 飞书表单视图的字段定义（新建抽屉照它渲染） |
| GET    | `/feishu/workbench/{role}/link-options`       | 关联字段候选（取目标表主字段）           |
| GET    | `/feishu/workbench/{role}/user-options`       | 人员字段候选（通讯录，递归子部门）       |
| GET    | `/feishu/workbench/{role}/linked-records`     | 关联表记录 + 该表全部字段定义（`field` 也认反查页的 key） |
| GET    | `/feishu/workbench/{role}/related-blocks`     | **全部反查页一次取回**（`record_ids` 可重复传；同一张从表只扫一遍） |
| POST   | `/feishu/workbench/{role}/linked-records`     | 在关联表新建（回指字段由调用方给）       |
| PUT    | `/feishu/workbench/{role}/linked-records/{id}`| 改关联表里的记录                         |
| DELETE | `/feishu/workbench/{role}/linked-records/{id}`| 删关联表里的记录（不可逆，调用方先二次确认） |
| POST   | `/feishu/workbench/{role}/fields`             | 建文本字段（索引页「空列」，**改生产表结构**） |
| PUT    | `/feishu/workbench/{role}/fields/{name}`      | 改字段名（只改名，不动数据）             |
| GET    | `/feishu/records/file/download`               | 附件下载（代理，`role` + `table_id` 判权限） |
| POST   | `/feishu/records/file/upload`                 | 附件上传                                 |
| POST   | `/feishu/records/merge`                       | 合并（写「合出主单」/「合出分单」）       |
| POST   | `/feishu/records/unmerge`                     | 拆出合并组                               |

`role` 取值（全部定义在 `workbench.py` 的 `WORKBENCHES` 里，字段名逐字对应飞书 `field_name`）：

| role                 | 页面           | 数据源                       |
| -------------------- | -------------- | ---------------------------- |
| `customer_service`   | 富士康客服     | 富士康流程TSET（全局 settings） |
| `operator`           | 富士康操作     | 同上                         |
| `wd_customer_service`| 威达 · 国内客服 | 威达商机主表 `tblXmBgagFcTGWJr` |
| `wd_pricing`         | 威达 · 航线价格 | 威达航线报价表 `tbltztWS5DLZlly1` |
| `wd_domestic_ops`    | 威达 · 国内操作 | 威达订单表 `tblqNRxcBz4HRbY6`（索引页的行）+ iFreight 数据表 `tbl99ei11SxswR8v`（`related`，详情里的「iFreight 数据」页） |

权限沿用 `module_feishu:record:query|detail|update`。

## 依赖

- 内核槽位：无
- 其它插件：无
- 外部服务：飞书开放平台（`LARK_APP_ID` / `LARK_APP_SECRET` / `LARK_APP_TOKEN` / `LARK_TABLE_ID`）
- 基础设施：Redis（缓存 tenant_access_token 与字段元数据；Redis 不可用时每次请求都要换 token 并重拉字段）

依赖实现要点（改动前请先读，避免踩回归）：

- 飞书 `/fields` 单页上限 100，本表 178 字段**必须翻页**，否则后 78 个字段会判为「表中不存在」。
- `records/search` 的 `page_size` 必须放 URL query，放 body 会被忽略并退回默认 500。
- **读表一律走 `records/search`（POST），不要用 `GET /records`**。同一张客商表、同样 193 行，
  实测 `GET` 全列 **20.85s**（`page_size=100` 那档直接 30s 超时），`search` 全列 1.39s、
  投影后 **0.97s**——差 20 倍以上。并且 `search` 能用 `field_names` 投影而 `GET` 会拒
  （字段是公式时 `code 1254024 InvalidFieldNames`）。投影省的不只是流量：它让飞书不必现算
  那些公式/查找/双向关联列，20 倍里的大头在这里。候选列表（`list_link_options`）每次点开
  关联字段都要扫一遍目标表，所以这条直接决定「新建」抽屉的手感。
- **爬通讯录要并发**：`/contact/v3/users` 只返回**直属**成员，所以「全部成员」必须先把部门树
  爬完再逐部门取人。这两步原来都是串行的，实测一整趟 **9.5s**，而 `get_create_form` 要拿人员
  字段默认值就得等它——按层/按部门并发后 2.5s（限流见 `_CONTACT_CONCURRENCY`：组织树深的租户
  一次发上百个请求会被飞书限流，部门树与取人两条路都要过同一个信号量）。
- filter 的 `value` 必须是**数组**；勾选框未勾选是 `{"operator":"is","value":[false]}`。
- 嵌套过滤条件必须放在 `children` 里，直接塞 `conditions` 会 `field validation failed`。
- 按 record_id 单取一条要 3~5 秒（飞书要计算 178 字段里的公式/查找列），所以列表直接投影
  详情所需的全部字段，详情抽屉零额外请求打开。
- **按钮字段（type 3001）没有接口能触发**：飞书不提供「点一下按钮」的 OpenAPI，详情页只能
  把它渲染成置灰占位（例外见下）。要触发按钮背后的自动化，只能去飞书里点。
- **Stage 字段（type 24，`ui_type=Stage`，威达订单表上叫 `国内客服`/`操作`/`航线`）读不出定义**：
  `property` 恒为 `null`，且飞书没有 `GET /fields/{field_id}` 这个端点（实测 404）。它是飞书
  插件面板的阶段条，节点里挂了哪些字段**只能靠人给**，别指望从接口推出来。这三个字段不要收进
  任何详情分组——收进来就是个永远空白的只读格（`_detail_groups` 里已按 `STAGE_FIELD_TYPE`
  整类滤掉，配置里点名了也不出）。
- 国内操作的**订单详情按「操作」Stage 的节点拆了 Tab**（提货计划 / 报关 / 代理到货计划 /
  国外预报 / 其余字段），字段归属见 `_WD_OP_DETAIL_SECTIONS`——那份是**照业务方给的飞书
  截图抄的**，不是从接口推的（上一条：节点读不出来）。两条容易踩的坑：飞书里同一字段会同时
  挂在**多个节点**下（`分单号` 挂在提货计划与国外预报…），而 Tab 是一份划分、一个字段只能
  进一组，重复的一律归它第一次出现的节点；兜底组（`fields=()`）**必须留一个**，否则剩下的
  字段在详情里凭空消失。「完成节点」暂时没有分组（截图读不出它的字段，不拿别的字段凑数）。
- **`分单号` 不是普通字段，它没有「一个格子」的形态**。订单表上那一列是**查找引用**
  （type 19，只读）：飞书把挂在这张订单上的每条 iFreight 的分单号 `LISTCOMBINE` 成逗号串
  （`TAOW044871,TAOW044872`），**不带 record_id**——那份值拆不回原记录。真正可写的是
  **iFreight 表里每条记录**的那个（type 1 文本），而一张订单挂两条 iFreight 是常态。
  所以凡是要「改分单号」的地方（索引页那一列、订单每个子 Tab 的顶部）都不是一格，而是
  按 iFreight 的记录数摆出**几个**输入框、各改各的：索引页写 `WeidaWorkbench.vue` 的
  `loadFendan`/`commitFendan`，详情页写 `WeidaRecordDrawer.vue` 的 `FENDAN_BLOCK` 那一块，
  两者都走 `update_linked_record('iFreight', …)` 写 iFreight 表。订单表那列由**飞书公式**
  自己汇总，本系统只在本地做一个乐观串盖上去，不直接写它。
  `_WD_OP_DETAIL_SECTIONS` 里 `分单号` 仍留在「提货计划」，但前端会把它从普通字段网格里
  摘出去（见 `tabFields`），改用上面那个组件渲染——留在配置里只是为了底兜。
- 详情页里**唯一**能点的 3001 按钮，是前端 `LINK_BUTTON_DIALOGS` 显式登记的（目前只有威达
  对客方案的「添加费用明细」→ `WeidaCostDetailDialog`）。没登记的一律置灰。
- **业务方会把 3001 按钮换成复选框**（本系统里按钮一直是灰的，改了就真能用了）。换完前端基本
  不用动，但**字段类型有 10 分钟 Redis 缓存**（`lark_fields:*`），期间后端还按按钮处理、界面上
  什么都不渲染——改完字段类型先清那几个键再验。已经换过的两处：对客方案表的 `生成报价单`
  （原来在 `LINK_COLUMNS` 里当占位，现在表格自动画成可勾选框）、商机主表的 `发起审批`
  （原来是对客方案列表上方的 `LINK_ACTION_BUTTONS` 灰按钮，现在走 **`LINK_ACTION_FIELDS`**
  ——同一位置，但字段能就地勾选并写回主记录）。两个旧按钮还留在表里（`生成报价单 按钮` /
  `发起报价审批`），不在任何清单里，不会再出现。`LINK_ACTION_FIELDS` 点名的字段会**从字段页
  摘掉**（`consumedAbove`），否则同一个字段在「商机详情」里还会再出现一次。
- **三处 Redis 缓存，键都带 `app_token` 与 `table_id`**（两个 base 的表 id 可能撞名）：
  `lark_fields:*`（字段元数据，600s）、`lark_linktexts:*`（关联字段的文本，300s）、
  `lark_orderstatus:*`（订单表的「单票状态」索引，60s）。前两个是「改完字段/数据要等几分钟」
  的来源；第三个见下条。调试时删对应键即可，**不要**去删 tenant token 的键（那会连带换 token）。
- **订单状态索引有 60 秒缓存**（`_order_status_map` → `lark_orderstatus:{app}:{table}`）：
  列表每次都要它，而它是**整表扫订单表**——实测占列表总时长的一半（国内客服 2.73s 里 1.33s）。
  本系统自己写表时（`_batch_update` 清**被写的那张表**的键、`create_workbench_record`）**立刻
  清掉**这个键，所以「交接给国内操作」这类操作不会看到 60 秒的滞后；只有**在飞书里改的**才吃
  这 60 秒。字段被改名时**不写缓存**（那是配置漂移，日志里的 WARNING 不该 60 秒才响一次）。
- **一个工作台两张表**靠 `WorkbenchConfig.related`（见 `RelatedTable`）：**索引页的行只来自
  主表，从表不上索引页**，它在**详情抽屉**里单独占一页（`/config` 的 `related_blocks` →
  前端渲染成一个 `linked` 版式的 Tab，见下条）。取数走
  `GET /linked-records?field=<key>&record_ids=<主表记录 id>`：
  本表记录 id 传进去，从表里 `back_ref_field` 指着它的那些行就是结果——**一批**，不是一条
  （一张订单挂两条 iFreight 是常态）。选择这条路的前提是两张表之间**只有反向的边**
  （iFreight 的 `所属订单` → 订单表，订单表上没有对等的关联字段）；有双向关联时用详情页的
  关联分区块（`LINK_FIELD_SPLITS`）更自然。
- 这一页的**版式由前端 `linkColumns` 点名**：配了列清单的（如 `iFreight`）摆成一张表——一行
  一条从记录、末端箭头展开看其余字段；没配的摆成每条一张字段网格。别把从表塞进索引页的
  某一格：1:N 的从表摊到一格上必然要挑一条显示、另一条静默消失（实测那两条的业务值还往往
  不同：一条同步失败、一条成功），而「一格里显示 N 条」的弹层版式也没解决「一屏看几条」。
- **一个工作台可以有多个反查页指向同一张从表**（国内操作现在是三条：`iFreight` / `复制提单编组`
  / `投保编组`，后两条是飞书详情面板里的字段编组，业务要各占一个子 Tab）。它们表 id 与回指
  字段全一样，区别只在 `fields` 铺哪几列。**`key` 必须互不相同**：`_related_page` 拿 key 认页、
  写回的表 id 也从它取，撞名会让某一页的改动静默写到别处去。
- **同一张从表只扫一遍**（`related_blocks_records` + `GET /related-blocks`）：这几页逐页取数
  是各扫一遍全表，实测三个 Tab 合计 4.4s（其中两次纯属重复）。合并取数按
  `(表 id, 回指列)` 分组，**一组只扫一次**（投影取该组各页字段的**并集**），扫完再按页裁列
  下发 —— 三个 Tab 变成一次 1.4s 的请求，切页不再等。两条路径必须**逐字同形**
  （`tests/test_module_feishu_related.py` 里有用例逐页深比较），裁错只会让某一页少几列，
  界面上看不出来。取数比「在服务端加 TTL 缓存」好在**零陈旧**：飞书侧（比如点「同步到
  iFreight」按钮）刚写的行，下次打开抽屉就能看到。
- `_related_records`（单页路径）仍在，`linked_records?field=<反查页 key>` 走它；两条路径
  共用 `_records_pointing_back`，分组与裁列逻辑只此一份。
- `_related_records` 返回的字段元数据里**不含回指列**：筛选要用它（也在 `link_ids` 里），
  但这一页正长在它所指向的那条记录的详情里，摆一格「所属订单」是回声。
- 反查页的字段**可编辑性照从表自己**，不再一律压成只读——在这儿改的值会直接写回从表
  （`update_linked_record` 先从配置里认出这是反查页，取它的 `table_id`，问不到就走
  `_link_target_table` 会在本表上找 `iFreight` 这个不存在的字段并报错）。飞书侧本来就只读的
  类型（自动编号等）仍然只读，那是从表的字段定义说了算，不是本系统另加的一道锁。
- **行级数据权限**（`WorkbenchConfig.owner_field`）：填一个**人员字段名**（目前只有国内客服填了
  「商机负责人」），**非超管**登录就只看该字段等于自己的记录；留空 = 不限制，其余工作台不受影响。
  三个容易踩的点：
  - **认人靠姓名，不是 id**。`sys_user` 表里没有飞书 `open_id`，所以拿 `sys_user.name` 去
    `_contact_directory()` 里**精确匹配**同名成员换 `open_id`（同样用姓名的先例见 `user_defaults`）。
    客服账号的昵称必须与飞书通讯录里那个人的姓名**一字不差**。
  - **匹配不到直接报错，不放行**（fail-closed）。放行的后果是客服看到全量数据，而「怎么都能看到」
    没人会当成 bug 来报；报错至少会有人来问。前后空格会 strip 掉（账号里手填的名字常带尾巴）。
  - **四个入口都要滤**：列表（`list_workbench`）、总数卡片（`count_total`）、待办卡片
    （`count_todo_breakdown`）、详情（`get_workbench_record`）。详情那一层不能省——`record_id`
    能从分享链接/旧页面拿到，列表滤掉了不代表详情进不来，故详情单独判一次并返回 403。
    卡片那两处不同步过滤的话，界面会自相矛盾：卡片写 1958 条、点进去 3 条。
  条件与关键字/待办条件是 **AND**，合并走 `_combine_filter`。这个函数看着啰嗦，是因为飞书
  filter 有两个实测出来的形态硬约束（违反都是 `code 99992402 field validation failed`）：
  **顶层不能是裸条件**（`filter.conjunction is required`，只有一个条件也要包一层
  `{conjunction, conditions}`），**子过滤器不能进 `conditions` 而要走 `children`**，且
  `children` 里**每一项本身也得带 `conjunction`**。踩过：客服默认视图（不搜索、不筛待办）
  只有一个 owner 条件，裸传过去整个列表 400，而随手一搜反而好了。
  `current_user` 由 `controller.py` 把 `auth.user` 传下来；不传（内部调用/脚本）= 不过滤，
  行为与加这个功能之前一致。
- **token 自愈**：`tenant_access_token` 在缓存里可能提前失效（应用被改动授权、飞书侧作废），
  此时**所有**飞书接口一起失败，且长得像「参数写错了」（HTTP 400 + body 里的业务码，或
  200 + 非 0 code）。`_send` 认出 token 类报错（99991661/99991663/99991664/99991665）就
  清缓存、重换、**原样再打一次**（只重试一次）；认不出的失败一次都不重试。所有 JSON 调用与
  字节下载都走它，不要再自己拿 `_token()` 出去打飞书。

## 删除影响

删除本目录后：

- `/feishu/workbench/*` 与 `/feishu/records/merge|unmerge` 全部 404，客服/操作工作台页面无数据。
- 无表、无种子数据、无定时任务、无内核槽位需要清理，删除是干净的。
- `sys_menu` 里指向客服/操作工作台页面的菜单会变成死链，需一并删除。
- **飞书侧数据不受影响**（本模块不持有数据副本）；但本模块是这些工作台唯一的写入通道，
  删除后无法再通过本系统回写飞书。
