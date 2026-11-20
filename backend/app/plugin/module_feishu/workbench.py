"""工作台配置常量。

飞书表格是唯一数据源，字段名以飞书 API 实际 ``field_name`` 为准（本表共 178 个字段）。
本文件只描述「**哪些字段属于哪个角色的哪个分组**」，不含任何数据结构推导——字段类型
仍由 ``/fields`` 接口动态读取，避免表结构微调后配置与飞书脱节。

角色划分参照业务字段对照清单（归口角色列）与上一版系统（威达货代）的页面结构。

字段名全部逐字取自飞书 API 返回，含空格与特殊符号（如 ``ASN NO``、``主单号-M``、
``文本 36``），**不要**顺手做规范化处理，否则写入时会被飞书静默忽略。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.exceptions import CustomException


@dataclass(frozen=True)
class WorkbenchGroup:
    """详情页的一个分组（前端渲染为一个 Tab）。

    ``fields`` 留空 = **「其余字段」**：收走所有没被别的分组点名的字段。用它当「主」分组
    就不必把四十来个字段名抄一遍——抄一遍等于维护第二份字段清单，飞书那边一加字段就漂移。
    """

    label: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class RelatedTable:
    """详情抽屉里一页**反查**回来的从表（如订单详情里的「iFreight 数据」）。

    两张表之间可能只有**一条反向的边**（iFreight 数据表的 ``所属订单`` → 订单表），订单表上
    没有对等的关联字段，所以本表记录的 ``link_ids`` 里根本没有这页的钥匙。反查只剩一条路：
    拿**父记录的 record_id** 去从表里找哪些行的 ``back_ref_field`` 指着它。

    **这一页只在详情抽屉里**：铺成一张表，一行一条从记录、末端一个箭头展开看整条，
    条数不定——一张订单挂两条 iFreight 是常态（实测 16 张订单里 2 张如此），所以按「取第一条」
    摊平会静默丢数据。索引页上不放从表：那里一行是本表的一条记录，从表那几行既不是它的字段、
    一格也放不下 N 条。

    ``fields`` 是**从表里的字段名**，顺序即版面顺序；名字对不上就当没写（字段被改名只会少
    一格，不会让整页打不开）。
    """

    key: str
    """这一页的标识；也是 ``linked-records`` 接口上的 ``field_name``（如 ``iFreight``）。"""

    label: str
    """详情抽屉里这一页的名字（如 ``iFreight 数据``）。"""

    table_id: str
    """从表的 table_id。"""

    back_ref_field: str
    """从表里**指回本表**的关联字段名（如 ``所属订单``）。"""

    fields: tuple[str, ...] = ()
    """这一页要铺的字段（从表里的字段名）。"""


@dataclass(frozen=True)
class WorkbenchConfig:
    """一个工作台（角色）的完整配置。"""

    key: str
    name: str
    description: str
    # 列表展示列（顺序即列顺序）。其中「货物数据」是聚合列，非真实字段。
    columns: tuple[str, ...]
    # 列表接口向飞书投影的字段（比 columns 多出聚合列用到的原始字段）。
    list_fields: tuple[str, ...]
    # 待办判定用勾选框：任一**未勾选**即计入待办。
    todo_checkboxes: tuple[str, ...]
    # 列表排序：(字段名, 是否倒序)，按元组顺序依次比较。
    sort: tuple[tuple[str, bool], ...] = field(default_factory=tuple)
    # 详情页顶部固定显示的基础信息（不是 Tab，常驻在页头）。
    base_fields: tuple[str, ...] = field(default_factory=tuple)
    # 详情页分组（每组渲染为一个 Tab）。
    groups: tuple[WorkbenchGroup, ...] = field(default_factory=tuple)
    # 关键字搜索命中的字段（OR 关系）。必须逐表配置：跨 base 时字段名完全不同，
    # 拿甲表的字段名去查乙表会被飞书整单拒掉。
    search_fields: tuple[str, ...] = field(default_factory=tuple)
    # 数据源：本工作台读写的飞书 base 与表。留空 = 回落到全局 settings（富士康那套）。
    app_token: str = ""
    table_id: str = ""
    # 列表接口是否**投影全字段**。用户可自定义索引页列（能选表里任意字段），
    # 只投影预设分组的话，自选的字段值根本不会返回。仅对允许自定义的工作台开启。
    project_all: bool = False
    # 索引页「新建」按钮上的字。各表在飞书里的叫法不同（商机 / 订单 / 报价单），
    # 写死「新建记录」会让人不确定建出来的是什么，故按表配。
    create_label: str = "新建记录"
    # 飞书**表单视图**的 view_id。配了它，新建抽屉就照该表单的字段与顺序渲染
    # （含必填星号、自定义标题与说明），而不是照 config 的分组——业务在飞书里怎么
    # 设计录入表单，这里就长什么样，改表单不用改代码。留空则退回按分组渲染。
    create_form_view_id: str = ""
    # 人员字段的**默认值**：字段名 → 姓名元组，新建表单打开时预选好。
    # 写姓名而不是 open_id：配置是给人维护的，一串 `ou_...` 没人知道是谁。
    # 下发前会拿通讯录换成 open_id，换不到的（改名/离职/被 hidden_users 挡掉）跳过并记日志。
    user_defaults: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # 通讯录里**不列出**的人（按姓名精确匹配）。供人员字段选择用——离职、外包、
    # 机器人账号之类不该出现在下拉里的人。取的是通讯录，飞书那边又删不得。
    hidden_users: tuple[str, ...] = ()
    # 详情抽屉的 **Tab 划分**。留空 = 不切 Tab，把 detail_fields 一次性铺在一页上。
    #
    # 只有字段多到一屏放不完的表才需要它（商机主表 49 个：光货物相关的就有十来个，
    # 混在商机字段里既找不到重点、一页也拉得太长）。分组**只决定谁归哪一组**，
    # 组内顺序照飞书表里的顺序——不在这里维护第二份字段清单。
    detail_sections: tuple[WorkbenchGroup, ...] = field(default_factory=tuple)
    # 列表接口是否给每条记录算一个**跨表推导**的「单票状态」（顶层 `single_status` 键）。
    #
    # 与上面那几个勾选框反推的徽章不同：那几个只看本表字段，前端自己算就行；这一个要读
    # **关联表**（商机上的「订单表」）里的字段，而列表投影里没有关联表的字段值，只有
    # record_id。所以只能在后端多打一次关联表、算好再下发（前端拿不到那份数据）。
    single_status: bool = False
    # 详情抽屉里**反查回来的从表**页（见 `RelatedTable`）。本工作台的「数据源」不止一张表时
    # 就配它：**索引页仍是本表的行**，只是在详情里多出一页，铺这张从表里反查到的记录。
    # 留空 = 详情就是本表自己的字段。不会影响列表（从表的列不进列表）。
    related: tuple[RelatedTable, ...] = ()
    # **行级数据权限**：填一个人员字段名（如「商机负责人」）后，**非超管**登录只能看到
    # 该字段等于自己的记录。留空 = 不限制（默认，所有既有工作台都不受影响）。
    #
    # 认人靠**姓名**：拿 `sys_user.name` 去飞书通讯录里精确匹配同名成员的 open_id。
    # 所以客服账号的昵称必须与飞书里那个人的姓名**一字不差**，否则匹配不到会直接报错
    # （有意做成 fail-closed：匹配不到时宁可报错，也不能悄悄放开成全量）。
    owner_field: str = ""
    # 详情保存时是否允许「同步到同组合并组」。
    #
    # 价格/客服工作台改订舱信息是整组一起改（勾选后连带写同组其他票）；操作工作台按
    # 业务要求是**单票单同步**——操作只对当前这票负责，不应把本票的报关/计划改动
    # 带到同组别的票上。为 False 时前端不显示同步勾选框，后端也忽略该参数。
    allow_group_sync: bool = True


# 货物数据聚合列用到的原始字段（件数 / 毛重 / 体积 / 尺码）。
CARGO_FIELDS: tuple[str, ...] = ("件数", "毛重", "体积", "尺码")

# 富士康流程TSET 两个工作台的搜索字段（同一张表，字段名一致）。
_FOXCONN_SEARCH_FIELDS: tuple[str, ...] = ("发票号", "分单号", "头程主单号", "ASN NO", "中文品名")

# 状态派生所依赖的勾选框（前端据「勾选框 + 状态字段」反推徽章，飞书侧不存状态枚举）。
BOOKING_CHECKBOX = "订舱单"
PLAN_CHECKBOX = "计划"
CUSTOMS_PLAN_CHECKBOX = "报关计划"
FORECAST_CHECKBOX = "预报"
INSURANCE_CHECKBOX = "投保"

# 合并组关联字段（SingleLink，写入格式为 record_id 字符串数组）。
MERGE_MASTER_FIELD = "合出主单"
MERGE_HAWB_FIELD = "合出分单"

# 合并组需要连带同步的字段：组内任一票改动，整组一起写。
#
# **不含「计划」**：计划下发走 B+A 方案（《富士康流程TSET-系统开发要求》§4.10/§5.5）——
# 前端同组都显示为已计划，但飞书原表只勾选当前票。所以同步写组时绝不能带上它。
MERGE_SYNC_FIELDS: tuple[str, ...] = (
    "订舱单",
    "ETD",
    "订舱代理",
    "航司",
    "价格",
    "代理deadline",
    "操作日",
    "tracking时效要求",
    "车队时效要求",
)

# 客服列表列（发票号列额外渲染合并徽标；货物数据为聚合列）。
_CS_COLUMNS: tuple[str, ...] = (
    "发票号",
    "部门",
    "三方进口",
    "中文品名",
    "货物数据",
    "预计提货时间",
    "订舱状态",
    "计划状态",
)

# 操作列表列。
_OP_COLUMNS: tuple[str, ...] = (
    "发票号",
    "部门",
    "三方进口",
    "中文品名",
    "货物数据",
    "预计提货时间",
    "分单号",
    "订舱代理",
)

# 客服详情页顶部的「基础信息」卡（常驻页头，不占 Tab）。
#
# 对应旧系统 CsBaseInfoCard：固定展示一票的静态属性，看的时候不必先切 Tab。
# 「实际离厂时间（车队回填）」是提货状态的判定来源（有值 = 已提货）。
_CS_BASE_FIELDS: tuple[str, ...] = (
    "发票号",
    "部门",
    "三方进口",
    "服务",
    "下单人",
    "国外客户",
    "中文品名",
    "英文品名",
    "件数",
    "毛重",
    "体积",
    "尺码",
    "体积重",
    "计费重",
    "重泡货",
    "比例",
    "分单号",
    "ASN NO",
    "提货厂区",
    "头程主单号",
    "品名数量",
    "投保品名",
    "英文品名投保",
    "送货地址",
    "条款",
    "料号",
    "单价",
    "总价",
    "报关金额",
    "ORDER NO",
    "QTY",
    "TO",
    "备注",
    "预计提货时间",
    "实际离厂时间（车队回填）",
)

# 客服详情页 Tab。
#
# 旧系统里「基础信息」是页头卡片、「合并信息」不是独立 Tab——它分别嵌在
# 头程订舱（「合单与订舱单」卡里的合出主单/合出分单选择器）与
# 计划下发（「合并组」卡里的成员表）中，所以这里把合并字段前置到这两个 Tab。
_CS_GROUPS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="头程订舱",
        fields=(
            "合出主单",
            "合出分单",
            "合并主单",
            "合并分单",
            "订舱单",
            "ETD",
            "订舱代理",
            "启运港",
            "目的港",
            "航司",
            "操作日",
            "价格",
            "代理deadline",
            "主单号-M",
            "头程主单号",
            "航班号",
            "临时启用",
            "复制提单号",
            "复制提单",
            "更新提单",
            "生成提单",
            "生成主单",
            "导出分单",
            "导出舱单",
            "随机分舱",
            "随机分舱文件",
            "订舱单文件",
        ),
    ),
    WorkbenchGroup(
        label="计划下发",
        fields=(
            "合出主单",
            "合出分单",
            "合并主单",
            "合并分单",
            "计划",
            "预计提货时间",
            "航班号",
            "tracking时效要求",
            "车队时效要求",
            "预报",
            "代理计划",
            "报关计划",
            "空运booking",
            "代理入货通知",
        ),
    ),
)

# 操作详情页公共头（文档 §6.2）：单价替代单托金额；小件数即箱数；
# 件数/箱数/QTY 同属货物信息，前端按顺序渲染。
_OP_BASE_FIELDS: tuple[str, ...] = (
    "发票号",
    "分单号",
    "部门",
    "服务",
    "三方进口",
    "操作",
    "操作日",
    "中文品名",
    "料号",
    "件数",
    "小件数",
    "QTY",
    "单价",
    "总价",
    "预计提货时间",
    "实际离厂时间（车队回填）",
    "提货厂区",
    "送货地址",
)

_OP_GROUPS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="做单分单",
        fields=(
            "发票号",
            "分单号",
            "头程主单号",
            "主单号-M",
            "ASN NO",
            "主单",
            "分单随机",
            "舱单随机",
            "导出分单",
            "导出舱单",
            "合票报关",
            "多选报关",
            "品名数量",
            "TENA",
            "TENA-备注说明",
        ),
    ),
    WorkbenchGroup(
        label="对接车队",
        fields=(
            "订舱代理",
            "代理入货通知",
            "实际离厂时间（车队回填）",
            "预计到达时间",
            "启运港",
            "车队时效要求",
            "送货司机信息",
            "货照",
        ),
    ),
    # §6.5：到货只保留「提货表同步 + 交接单」，鉴定/磁检/旧交接单/其他记录备注都不在这页
    WorkbenchGroup(
        label="到货操作",
        fields=(
            "货照",
            "送货司机信息",
            "预计到达时间",
            "交接单",
        ),
    ),
    # §6.6：报关三态用同一组字段承载（状态由 报关计划/报关单 有无反推）
    WorkbenchGroup(
        label="报关",
        fields=(
            "多选报关",
            "报关计划",
            "报关行",
            "预计报关时间",
            "启运港",
            "报关单",
            "报关金额",
            "HS",
        ),
    ),
    WorkbenchGroup(
        label="投保",
        fields=(
            "投保",
            "投保品名",
            "投保备注临时",
            "投保地址",
            "英文品名投保",
        ),
    ),
    WorkbenchGroup(
        label="鉴定",
        fields=(
            "中文品名",
            "鉴定",
            "鉴定备注",
            "磁检",
            "磁检品名",
            "铝电解",
            "片式多层",
            "片式钽质",
        ),
    ),
    WorkbenchGroup(
        label="二程订舱",
        fields=(
            "二程主单号",
            "二程航司",
            "二程价格",
            "二程ETD",
            "二程ETA",
            "二程代理",
            "二程代理账单",
        ),
    ),
    WorkbenchGroup(
        label="国外跟踪",
        fields=(
            "派送代理",
            "派送费用",
            "国外账单",
            "国外账单 (1)",
            "海运提单号",
            "预计到达时间",
            "送货司机信息",
        ),
    ),
    WorkbenchGroup(
        label="额外费用",
        fields=(
            "额外费用记录",
            "应付提货费",
            "应付保险费",
            "应付包装费",
            "应付报关费",
            "应付查验费",
            "应付额外费用",
            "应收分票提单",
            "应收包装费",
            "应收查验费",
            "应收保险费",
            "应收鉴定费",
            "应收报关费",
            "EPD1应收录入",
        ),
    ),
    WorkbenchGroup(
        label="分单明细",
        fields=(
            "其他记录备注",
            "异常记录",
            "已处理",
            "币制",
            "做账",
            "单托重量",
            "单托金额",
        ),
    ),
)

# 客服列表排序：预计提货时间由早到晚，同一时间再按发票号由大到小。
#
# 必须交给飞书服务端排序——游标分页没有 offset，本地排序只能排当前一页，
# 排序规则落不到全部 1990 条上。
_CS_SORT: tuple[tuple[str, bool], ...] = (
    ("预计提货时间", False),
    ("发票号", True),
)

# 客服列表投影字段 = 列（去掉聚合列）+ 聚合列所需的原始字段。
_CS_LIST_FIELDS: tuple[str, ...] = (
    "发票号",
    "部门",
    "三方进口",
    "中文品名",
    "英文品名",
    *CARGO_FIELDS,
    "预计提货时间",
    BOOKING_CHECKBOX,
    PLAN_CHECKBOX,
    MERGE_MASTER_FIELD,
    MERGE_HAWB_FIELD,
)

_OP_LIST_FIELDS: tuple[str, ...] = (
    "发票号",
    "部门",
    "三方进口",
    "中文品名",
    "英文品名",
    *CARGO_FIELDS,
    "预计提货时间",
    "分单号",
    "订舱代理",
    MERGE_MASTER_FIELD,
    MERGE_HAWB_FIELD,
)

# ==================== 威达业务管理系统（第二个 base） ====================
#
# 与富士康那套**共用**同一套飞书读写能力，但数据源是自己的 base/表（写进配置，
# 不再回落到全局 settings）。字段名同样逐字取自飞书 API。
WD_APP_TOKEN = "Ane6bNFAYacsUPsi3gscwy7hnPb"
WD_CUSTOMER_SERVICE_TABLE = "tblXmBgagFcTGWJr"
# 订单表。商机主表上的「确认订单」按钮（3001，飞书自动化）点一次就在这张表建一条记录，
# 「关联商机」（21）双向回指商机——所以商机详情里能顺着这个关联展开这张表的记录。
WD_ORDER_TABLE = "tblqNRxcBz4HRbY6"
# 航线报价表（航线价格部门的主表）。在**商机**那一侧它是「航线报价」那个关联分区，
# 一票商机挂 1~4 条供应商报价（实测 41 票 / 86 条）；本表把它当**独立索引页**来用。
WD_PRICING_TABLE = "tbltztWS5DLZlly1"
# iFreight 数据表。订单交接给国内操作之后的**下游产物**：订单表上的「创建iFreight 数据表」
# 按钮（3001，飞书自动化）点一次生成一条，再由「同步到iFreight」推给 iFreight 系统。
#
# 与订单表之间**只有一条反向的边**：本表的 ``所属订单``(18, 单值) 指回订单表；订单表那侧
# 实测**没有**指向本表的字段（三个关联字段全指向商机/客商档案），所以「从订单看 iFreight」
# 只能反查，见 `RelatedTable`。实测 20 条里 18 条挂着订单、覆盖 16 张订单（其中 2 张各挂 2 条）。
WD_IFREIGHT_TABLE = "tbl99ei11SxswR8v"

# 威达的人员字段候选**统一**排除这几位（按姓名精确匹配）：通讯录里有账号但不参与
# 这些表的业务分配，留在下拉里只是干扰。**每个威达工作台都要带上它**——漏传不会报错，
# 只会让这些人静悄悄冒回候选列表里。飞书那边删不得（他们还有别的用途）。
WD_HIDDEN_USERS: tuple[str, ...] = ("Anna Qiao", "Jeff Nie", "孙智勇")

# 国内客服列表列。
#
# 列的选择**按实测非空率**定（2026-10-09 全量 40 条）：``商机信息``(主字段·公式)、
# ``货物数据``(公式)、``客户``(双向关联) 三列当前 **0/40 全空**——公式还没算出来、
# 关联还没建立。拿它们当列表列会让整屏空白，故列表只放实测有值的列；
# 空的三列仍留在详情分组里，等飞书侧填了数据自然会显示。
_WD_CS_COLUMNS: tuple[str, ...] = (
    "商机编号",
    "客户 REF NO.",
    "业务类型",
    "货物品名",
    "目的 港",
    "负责航线",
    "货好时间",
    "最晚报价截止时间",
    "是否拿到货",
)

_WD_CS_LIST_FIELDS: tuple[str, ...] = (
    *_WD_CS_COLUMNS,
    "运输条款",
    "商机负责人",
)

# 列表排序：**新单在最上面**，往下按创建时间由新到旧。
#
# 没有用「创建时间」这个字段——飞书**没有**给它（商机表 48 个字段里没有时间戳列，
# `records/search` 与 `records` 两个接口的返回里也都不带记录的 `created_time`，
# 实测确认过）。能用的替代是 `商机编号`：它是自动编号，创建时按顺序递增，
# 形态固定为 `OP-` + 7 位数字（YYMM + 三位流水，实测 54 条长度全为 10、形状全为
# `OP-9999999`、无重号），所以**字典序倒排 = 数字倒排 = 创建时间倒排**，飞书服务端
# 直接能排。它本来就是这个排序的第二关键字，一直好用。
#
# 这里**只留编号一个关键字**：编号唯一，加第二个关键字没有意义；而且原来排在最前的
# 「最晚报价截止时间」会把新单压到下面去，正是要改掉的。
_WD_CS_SORT: tuple[tuple[str, bool], ...] = (("商机编号", True),)

# 只放**文本**字段：飞书的 ``contains`` 对自动编号(商机编号)/公式/关联字段不生效，
# 放进去会让整个搜索条件被拒。
_WD_CS_SEARCH_FIELDS: tuple[str, ...] = (
    "客户 REF NO.",
    "货物品名",
    "客户核心关注点",
    "备注",
    "货源地",
    "派送地址",
)

_WD_CS_BASE_FIELDS: tuple[str, ...] = (
    "商机编号",
    "客户 REF NO.",
    "业务类型",
    "运输条款",
    "商机负责人",
)

_WD_CS_GROUPS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="客户需求",
        fields=(
            "客户核心关注点",
            "最晚报价截止时间",
            "货好时间",
            "要求到港时间",
            "运输条款",
            "路线要求",
            "货源地",
            "派送地址",
            "备注",
        ),
    ),
    WorkbenchGroup(
        label="货物信息",
        fields=(
            "货物数据",
            "件数",
            "毛重（kg）",
            "体积（m³）",
            "尺寸（长*宽*高 * 数量）",
            "包装形式",
            "货物品名",
            "货物特殊属性",
            "计费重",
            "货物品名(货物特殊属性)",
        ),
    ),
    WorkbenchGroup(
        label="航线与报价",
        fields=(
            "负责航线",
            "目的 港",
            "启运港",
            "目的港",
            "最早航班时间",
            "供应商编码",
            "报价单",
            "报价单生成时间",
        ),
    ),
    WorkbenchGroup(
        label="关联单据",
        fields=(
            "客户",
            "客户编码",
            "航线报价",
            "对客方案",
            "最终对客方案",
            "订单表",
            "飞书审批编码",
        ),
    ),
    WorkbenchGroup(
        label="跟进",
        fields=(
            "商机负责人",
            "商机编辑者",
            "是否拿到货",
            "未拿到货备注",
        ),
    ),
)

# 商机详情抽屉的 Tab 划分。
#
# 商机自身 49 个字段里有十来个是「货」的属性（件数/毛重/体积/尺寸/包装/品名/特殊属性/
# 计费重…），跟「客户要求什么、航线怎么走」不是一类事，混在一页里既不聚焦、一屏也放不完。
# 拆出去单独一屏后，商机详情这屏回到二十几个字段，一屏看全。
#
# 第一组**留空** = 其余全部字段（不抄字段名，飞书加字段自动跟上，见 WorkbenchGroup）。
# 组内顺序照飞书表顺序：货物数据 → 件数 → 毛重 → 体积 → 尺寸 → 包装形式 → 货物品名
# → 货物特殊属性 → 计费重 → 货物品名(货物特殊属性)。
_WD_CS_DETAIL_SECTIONS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(label="商机详情", fields=()),
    WorkbenchGroup(
        label="货物详情",
        fields=(
            "货物数据",
            "件数",
            "毛重（kg）",
            "体积（m³）",
            "尺寸（长*宽*高 * 数量）",
            "包装形式",
            "货物品名",
            "货物特殊属性",
            "计费重",
            "货物品名(货物特殊属性)",
        ),
    ),
    # 这一组的名字**同时是一个关联分区**（前端 `DETAIL_LINK_BLOCKS` 里的「对客方案」），
    # 不是巧合：抽屉认这个约定——分组名与某个关联字段同名时，这一组不单独成 Tab，而是铺在
    # 那张关联表格**下方**。飞书自己的记录详情面板就是这么排的（对客方案的方案列表下面
    # 接着「最终对客方案 / 确认订单 / 是否拿到货 / 未拿到货备注」这四个跑单结果的字段）。
    # 所以 label 不能顺手改成「对客方案结果」之类，改了这四个字段会变成第 5 个 Tab。
    #
    # 点名这四个的副作用是它们**从「商机详情」那一屏消失**（那个空组只收没被点名的字段）：
    # 它们是「方案敲定之后」的字段，本来就不该混在客户需求里。
    # 组内顺序照飞书表顺序（实测 #6 最终对客方案 → #7 确认订单 → #8 是否拿到货 →
    # #9 未拿到货备注），配置里的次序不作数——见 `_detail_groups`。
    WorkbenchGroup(
        label="对客方案",
        fields=("最终对客方案", "确认订单", "是否拿到货", "未拿到货备注"),
    ),
)

# 威达 · 航线价格：列表列。
#
# 与客服那张表同样**按实测非空率**定列（2026-10-10 全表 86 条），不照字段清单抄：
#   `报价状态` 0/86、`原始报价附件` 0/86、`报价负责人` 1/86、`航线方案建议` 1/86、`报价有效期`
#   18/86 —— 前四个摆上去就是整列空白。空的那几个仍留在详情分组里，飞书侧填了自然会出现。
#
# `供应商报价编号`（自动编号 SP-…）排第一：86/86 全有值，一条报价一个号，肉眼认单靠它。
# 主字段 `供应商报价信息`（公式，"供应商/客户REF/件数/价格"）虽然也是 86/86，但它是**公式**，
# 摆在第一列会把编号挤到后面，而客服那边的第一列正是编号，两边保持同一个扫视习惯。
_WD_PR_COLUMNS: tuple[str, ...] = (
    "供应商报价编号",
    "商机编号",
    "供应商",
    "路线/中转点",
    "成本金额",
    "成本币种",
    "航空公司",
    "等级、比例要求",
    "最早航班时间",
    "最晚确认时间",
    "生成对客方案初稿",
)

_WD_PR_LIST_FIELDS: tuple[str, ...] = (
    *_WD_PR_COLUMNS,
    "供应商报价信息",
    "关联商机",
    "被方案采用",
    "风险提醒",
)

# 列表排序：与客服工作台同一个口径——**新报价在最上面**。
#
# 这里用的是 `报价版本`（DateTime，86/86 有值），不是自动编号 `供应商报价编号`：编号的
# 流水号宽度不固定（实测 `SP-2026091122` 是 8 位日期 + 2 位流水，`SP-20261010115` 是
# 8 位日期 + 3 位流水），字典序在流水号跨 2 位→3 位的那一天会排错。时间戳没有这个毛病，
# 而且飞书服务端能直接按它排（实测倒排出来的顺序就是建单时间倒序）。同一秒内建的多条
# 报价会并列，退化成飞书自己的记录顺序——只影响同一批导入的先后，无所谓。
_WD_PR_SORT: tuple[tuple[str, bool], ...] = (("报价版本", True),)

# 同客服：只放**文本**字段。实测 `contains` 在公式列 `供应商报价信息`（本表主字段，
# "供应商/客户REF/件数/价格"）上是**能用**的——搜「南通超达」出 7 条。所以「公式列不能搜」
# 这句话不成立，别照抄；在同一张表上逐个字段试过才算数。
#
# 反过来，单选（航空公司/启运港）与查找（商机编号）上 `contains` 会被拒（`InvalidFilter`），
# 混进来会让**整个**搜索条件失效，所以这几个一律不进来。
#
# 代价：主字段是拼出来的，搜「SP-2026」搜不到——自动编号不在里面，那是另一列。
_WD_PR_SEARCH_FIELDS: tuple[str, ...] = (
    "供应商报价信息",
    "等级、比例要求",
    "风险提醒",
)

_WD_PR_BASE_FIELDS: tuple[str, ...] = (
    "供应商报价编号",
    "商机编号",
    "供应商",
    "报价版本",
)

_WD_PR_GROUPS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="报价",
        fields=(
            "供应商报价编号",
            "供应商报价信息",
            "关联商机",
            "商机编号",
            "供应商",
            "供应商编码",
            "报价版本",
            "报价负责人",
            "报价状态",
            "生成对客方案初稿",
            "被方案采用",
        ),
    ),
    WorkbenchGroup(
        label="航线与成本",
        fields=(
            "路线/中转点",
            "航空公司",
            "启运港",
            "目的港",
            "中转点",
            "成本金额",
            "成本币种",
            "等级、比例要求",
            "预计运输时效（天）",
            "舱位状态",
        ),
    ),
    WorkbenchGroup(
        label="时间",
        fields=(
            "最早航班时间",
            "最晚到港时间",
            "最晚确认时间",
            "报价有效期",
        ),
    ),
    WorkbenchGroup(
        label="其它",
        fields=(
            "风险提醒",
            "航线方案建议",
            "原始报价附件",
        ),
    ),
)

# 国内操作：**行来自订单表**（索引页就是订单表的行，从表的列不上列表），iFreight 数据另开一个
# 详情页。
#
# 为什么行源是订单表而不是 iFreight：这一岗的「一票活」就是一张订单——订单表上有 `国内操作`
# 这一列指派人（实测 30/32 有值，全是这几位操作），iFreight 只是交接之后生成的下游单据
# （20 条，只覆盖 16 张订单，还有 2 条没挂订单）。以 iFreight 当行源的话，没生成的单会
# 整个从列表里消失，而这恰恰是操作最该盯的那批。
#
# 这一页只看详情：一张订单可以挂**两条** iFreight（实测 16 张里 2 张如此，值还不一样），
# 所以页内是一行一条的表格、末端箭头展开看整条——「挑一条显示」才是会静默丢数据的做法。
#
# **三条而不是一条**：iFreight 那张表在飞书详情面板里分了编组（`复制提单编组` / `投保编组`），
# 业务要按编组各占一个子 Tab 分开看。三条反查页指向同一张表、回指字段也同一个，区别只在
# `fields` 铺哪几列——所以「一行一条」这条取数逻辑（`_related_records`）三条各跑一遍，代价
# 是同一批行被拉三次（iFreight 才 20 行，一次一秒出头）。**key 必须互不相同**：
# `_related_page` 拿 key 认页，撞名会让写回认错页（见 service.py 的 `update_linked_record`）。
_WD_OP_RELATED: tuple[RelatedTable, ...] = (
    RelatedTable(
        key="iFreight",
        label="iFreight 数据",
        table_id=WD_IFREIGHT_TABLE,
        back_ref_field="所属订单",
        # 铺哪些字段：**按实测非空率**（20 条全量）挑出真在用的那些，再按「一看就懂」的顺序排
        # ——先单据与同步状态，再航线、货物、当事人、付款方式、业务属性。非空率低的（`委托书`
        # /`分单`/`舱单`/`主单` 0/20，`报关箱单` 3/20，`备注` 3/20）当前是空格子，铺出来整页
        # 都是「—」。`所属订单` 也去掉：这一页正长在那张订单的详情里，再显示一遍是回声。
        fields=(
            "操作编号",
            "同步状态",
            "同步时间",
            "失败原因",
            "主单号",
            "分单号",
            "运输方式",
            "运输条款",
            "预计起运时间",
            "起运地城市CityName",
            "目的地城市CityName",
            "件数",
            "件数单位",
            "毛重",
            "体积",
            "尺寸",
            "货品描述",
            "发货人名称",
            "委托人名称",
            "订舱代理名称",
            "国外代理名称",
            "运费付款方式",
            "杂费付款方式",
            "业务类型",
            "揽货方式",
            "单据类型",
        ),
    ),
    # 飞书 iFreight 详情面板里的两个**字段编组**（截图给的）：各占一个子 Tab。
    # 字段名逐字照飞书 `field_name`，抄错不报错——只是这一页少一格（`_related_blocks` 跳过）。
    # 两张都以 `分单号` 打头：业务要求它在**每个子 Tab** 里都看得见（截图里也摆在最前），
    # 它同时也是「这一行是 iFreight 的哪一条」的标识——一条订单挂两条时，两行长得几乎一样，
    # 没有分单号就认不出改的是哪条。
    RelatedTable(
        key="复制提单编组",
        label="复制提单编组",
        table_id=WD_IFREIGHT_TABLE,
        back_ref_field="所属订单",
        fields=("分单号", "复制提单", "复制提单号"),
    ),
    RelatedTable(
        key="投保编组",
        label="投保编组",
        table_id=WD_IFREIGHT_TABLE,
        back_ref_field="所属订单",
        fields=("分单号", "投保", "投保条款", "币制", "报关金额", "启运地"),
    ),
)

_WD_OP_COLUMNS: tuple[str, ...] = (
    "订单编号",
    "商机编号",
    "客户 REF NO.",
    "货物品名（货物特殊属性）",
    "起运地/目的地",
    "航班",
    # 订单表上的「分单号」是**查找引用**（type 19，只读）：飞书把挂在这张订单上的 iFreight
    # 各自的分单号 `LISTCOMBINE` 成逗号串。真正的可写值是 iFreight 里每条记录的那个，
    # 所以这一列在界面上由前端另接一个「逐条改」的小面板（见 `WeidaWorkbench.vue`），
    # 不是普通的只读格。
    "分单号",
    "国内操作",
    "交接给国内操作",
)

_WD_OP_LIST_FIELDS: tuple[str, ...] = (
    "订单编号",
    "商机编号",
    "客户 REF NO.",
    "货物品名（货物特殊属性）",
    "起运地/目的地",
    "航班",
    "分单号",
    "国内操作",
    "交接给国内操作",
    "关联商机",
    "货物数据",
    "特殊操作要求",
)

# 订单编号是 `PO-` + 定宽零填充的 YYMM+3 位流水（实测 `PO-2609018` … `PO-2610059`），
# 字典序等于时间序，倒序即最新在前（已实测飞书接受对自动编号列排序，返回的正是最新那几张）。
# 翻页时**必须**有个定序字段，否则游标分页会漏行/重行。
_WD_OP_SORT: tuple[tuple[str, bool], ...] = (("订单编号", True),)

_WD_OP_SEARCH_FIELDS: tuple[str, ...] = (
    # 只能放**文本**列：`订单编号`(自动编号) 与 `商机编号`(查找引用) 对 `contains` 会被飞书
    # 拒为 `1254018 InvalidFilter`，而一个坏条件会让**整次搜索**失败（实测：带上订单编号后
    # 搜什么都 400）。`_build_filter` 现在按类型剔除这类字段，这里就不必再列它们。
    # 订单编号在索引页用「筛选」按本地值筛（全表 32 条本来就在一页里）。
    "客户 REF NO.",
    "货物品名（货物特殊属性）",
    "主单号",
)

_WD_OP_BASE_FIELDS: tuple[str, ...] = (
    "订单编号",
    "商机编号",
    "国内操作",
    "客户 REF NO.",
)

# 分组只服务详情页的兜底渲染与「其余字段」的归属，**不**决定索引页列。
_WD_OP_GROUPS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="订单",
        fields=(
            "订单编号",
            "商机编号",
            "关联商机",
            "国内操作",
            "操作负责人",
            "客户 REF NO.",
            "业务员",
            "商机负责人",
        ),
    ),
    WorkbenchGroup(
        label="货物与航线",
        fields=(
            "货物品名（货物特殊属性）",
            "货物数据",
            "货物特殊属性",
            "起运地/目的地",
            "起运地",
            "目的地",
            "航班",
            "航司",
            "主单号",
            "分单号",
            "负责航线",
            "供应商",
            "国外代理",
            "成本金额",
            "成本币种",
            "等级、比例要求",
            "出口口岸",
        ),
    ),
    WorkbenchGroup(
        label="时间",
        fields=(
            "订舱单生成时间",
            "入仓操作时间",
            "货好时间",
            "代理deadline",
            "最晚到港时间",
            "要求到门时间",
            "派送到门时间",
        ),
    ),
    WorkbenchGroup(
        label="操作",
        fields=(
            "生成订舱单",
            "订舱单",
            "发提货邮件",
            "提货车队",
            "提货信息",
            "提货费",
            "入货通知",
            "入青岛仓号",
            "报关",
            "报关方式",
            "下发操作计划",
            "发送预报",
            "操作要求",
            "特别注意",
            "特殊操作要求",
            "客户核心关注点",
            "代理操作要求",
            "派送地址",
            "司机信息",
            "委托书附件",
            "相关资料",
            "POD",
            "备注",
        ),
    ),
    WorkbenchGroup(label="其余字段", fields=()),
)

# 国内操作：订单详情抽屉的 Tab 划分。
#
# 这一份**不是**从接口推出来的：订单表上那三个 Stage 字段（`国内客服`/`操作`/`航线`，type 24）
# 的节点结构飞书 API 读不出来（`property` 恒 null，也没有单字段 GET），节点里挂了哪些字段
# **只能由业务方给**。用户按飞书「操作」面板的截图逐节点给的字段清单，本表就是照抄——
# 所以这里的字段归属**以截图为准**，别拿「看起来更像哪一类」去改。
#
# 三个 Stage 字段本身不进任何分组（它们是飞书插件面板的阶段条，节点得去飞书里点，
# 在这儿就是个永远空白的只读格）——`_detail_groups` 会跳过 type 24，见 service.py。
#
# **飞书里同一个字段会同时挂在多个节点下**（截图实测：`分单号` 挂在「提货计划」与
# 「国外预报」、`货物品名（货物特殊属性）` 挂在「提货计划」与「代理到货计划」、
# `入仓操作时间` 挂在「提货计划」与「代理到货计划」），而详情页的 Tab 是**一份划分**，
# 一个字段只能出现在一组里。重复的那些一律归**它第一次出现的那个节点**（即「提货计划」），
# 后出现的节点就不再列它——否则会被忽略、静默少一格。
#
# 「完成节点」暂时没有分组：截图里这个节点的字段读不出来（OCR 没结果），飞书侧也问不到，
# 不拿别的节点的字段凑一个似是而非的清单出来。业务方给了口径再补。
_WD_OP_DETAIL_SECTIONS: tuple[WorkbenchGroup, ...] = (
    WorkbenchGroup(
        label="提货计划",
        fields=(
            "分单号",
            "货物数据",
            "货物品名（货物特殊属性）",
            "提货车队",
            "提货费",
            "提货信息",
            "货好时间",
            "入仓操作时间",
            "发提货邮件",
            "入青岛仓号",
            "入货通知",
        ),
    ),
    WorkbenchGroup(
        label="报关",
        fields=(
            "报关",
            "起运地",
            "出口口岸",
        ),
    ),
    WorkbenchGroup(
        label="代理到货计划",
        fields=(
            "供应商",
            "报关方式",
            "代理操作要求",
            "下发操作计划",
        ),
    ),
    WorkbenchGroup(
        label="国外预报",
        fields=(
            "主单号",
            "派送地址",
            "发送预报",
        ),
    ),
    # 空组 = 「其余字段」兜底：吃光所有没被上面点名的字段。**必须留一个**，否则订单表
    # 剩下那三十来个字段（订舱单/成本/时间/备注…）在详情里就没地方去了。
    WorkbenchGroup(label="其余字段", fields=()),
)


# ---------------------------------------------------------------------------
# 系统2 · 出货明细（新 Base）
# ---------------------------------------------------------------------------
SYS2_APP_TOKEN = "ZFzyb2s7Vaq2RKsBicXcR9A7n2d"
SYS2_SHIPMENT_TABLE = "tblLN0tP01h1mFIc"

_SYS2_PRICE_GROUPS = (
    WorkbenchGroup(
        label="基本信息",
        fields=(
            "预计提货时间",
            "发票号",
            "分单号",
            "部门",
            "服务",
            "三方进口",
            "下单人",
            "国外客户",
            "操作",
            "操作日",
            "提货厂区",
            "送货地址",
            "条款",
            "实际离厂时间（车队回填）",
        ),
    ),
    WorkbenchGroup(
        label="订舱信息",
        fields=(
            "头程主单号",
            "ETD",
            "订舱代理",
            "启运港",
            "目的港",
            "航司",
            "代理deadline",
            "订舱单",
            "订舱单文件",
            "计划",
        ),
    ),
    WorkbenchGroup(
        label="货物数据",
        fields=(
            "件数",
            "毛重",
            "尺码",
            "体积",
            "体积重",
            "重泡货",
            "比例",
            "QTY",
            "ORDER NO",
            "中文品名",
            "料号",
            "小件数",
            "箱单发票",
        ),
    ),
    WorkbenchGroup(
        label="时效要求",
        fields=(
            "tracking时效要求",
            "车队时效要求",
        ),
    ),
    WorkbenchGroup(
        label="价格费用",
        fields=(
            "单价",
            "总价",
            "价格",
            "报关金额",
            "单托重量",
            "单托金额",
            "币制",
        ),
    ),
    WorkbenchGroup(
        label="合单/派送",
        fields=(
            "合出分单",
            "合并分单",
            "合出主单",
            "合并主单",
            "派送代理",
            "二程代理",
        ),
    ),
)


_SYS2_FLEET_GROUPS = (
    WorkbenchGroup(
        label="基本信息",
        fields=(
            "发票号",
            "分单号",
            "部门",
            "服务",
            "下单人",
            "国外客户",
            "操作",
            "操作日",
            "提货厂区",
            "预计提货时间",
            "实际离厂时间（车队回填）",
            "预计到达时间",
        ),
    ),
    WorkbenchGroup(
        label="货物数据",
        fields=(
            "件数",
            "毛重",
            "尺码",
            "体积",
            "体积重",
            "重泡货",
            "小件数",
            "料号",
            "ORDER NO",
            "QTY",
            "箱单发票",
        ),
    ),
    WorkbenchGroup(
        label="运输信息",
        fields=(
            "车型",
            "车费",
            "兆桦车费",
            "送货司机信息",
            "车队时效要求",
            "代理入货通知",
            "启运港",
            "送货地址",
            "货照",
        ),
    ),
    WorkbenchGroup(
        label="费用",
        fields=(
            "单托重量",
            "单托金额",
            "币制",
        ),
    ),
)


WORKBENCHES: dict[str, WorkbenchConfig] = {
    # 系统2 · 价格工作台：主表「出货明细」（新 Base）。字段归属已由业务 2026-11-10 确认，
    # 只投影 52 个字段；其余字段在价格台不可见（不是靠前端隐藏，是不进 detail_fields）。
    "sys2_price": WorkbenchConfig(
        key="sys2_price",
        name="价格工作台",
        description="价格核算 · 费用录入",
        columns=(
            "发票号",
            "部门",
            "三方进口",
            "中文品名",
            "货物数据",
            "预计提货时间",
            "分单号",
            "订舱代理",
            "单价",
            "总价",
            "价格",
            "币制",
            "ETD",
            "合出主单",
            "合出分单",
        ),
        list_fields=(
            "预计提货时间",
            "发票号",
            "分单号",
            "部门",
            "服务",
            "三方进口",
            "下单人",
            "国外客户",
            "操作",
            "操作日",
            "提货厂区",
            "送货地址",
            "条款",
            "实际离厂时间（车队回填）",
            "头程主单号",
            "ETD",
            "订舱代理",
            "启运港",
            "目的港",
            "航司",
            "代理deadline",
            "订舱单",
            "订舱单文件",
            "计划",
            "件数",
            "毛重",
            "尺码",
            "体积",
            "体积重",
            "重泡货",
            "比例",
            "QTY",
            "ORDER NO",
            "中文品名",
            "料号",
            "小件数",
            "箱单发票",
            "tracking时效要求",
            "车队时效要求",
            "单价",
            "总价",
            "价格",
            "报关金额",
            "单托重量",
            "单托金额",
            "币制",
            "合出分单",
            "合并分单",
            "合出主单",
            "合并主单",
            "派送代理",
            "二程代理",
        ),
        todo_checkboxes=(),
        sort=(("操作日", True), ("预计提货时间", True)),
        base_fields=(
            "发票号",
            "分单号",
            "部门",
            "三方进口",
            "服务",
            "下单人",
            "中文品名",
            "英文品名",
            "件数",
            "毛重",
            "体积",
            "尺码",
            "单价",
            "总价",
            "预计提货时间",
            "实际离厂时间（车队回填）",
            "送货地址",
            "操作",
        ),
        groups=_SYS2_PRICE_GROUPS,
        detail_sections=_SYS2_PRICE_GROUPS,
        search_fields=(
            "发票号",
            "分单号",
            "头程主单号",
            "国外客户",
            "下单人",
            "中文品名",
            "料号",
        ),
        app_token=SYS2_APP_TOKEN,
        table_id=SYS2_SHIPMENT_TABLE,
        project_all=True,
        create_label="新建出货记录",
    ),
    # 系统2 · 车队工作台：主表仍是「出货明细」，实际离厂/到达/司机/兆桦车费走车队回填的
    # Lookup；写入落在主表字段（车费），车队表原值保留，后续再决定是否回写。
    "sys2_fleet": WorkbenchConfig(
        key="sys2_fleet",
        name="车队工作台",
        description="提货安排 · 司机与到达",
        columns=(
            "预计提货时间",
            "发票号",
            "分单号",
            "部门",
            "下单人",
            "操作",
            "件数",
            "毛重",
            "体积",
            "提货厂区",
            "实际离厂时间（车队回填）",
            "预计到达时间",
            "送货司机信息",
            "车型",
            "车费",
            "兆桦车费",
            "车队时效要求",
            "操作日",
        ),
        list_fields=(
            "发票号",
            "分单号",
            "部门",
            "服务",
            "下单人",
            "国外客户",
            "操作",
            "操作日",
            "提货厂区",
            "预计提货时间",
            "实际离厂时间（车队回填）",
            "预计到达时间",
            "件数",
            "毛重",
            "尺码",
            "体积",
            "体积重",
            "重泡货",
            "小件数",
            "料号",
            "ORDER NO",
            "QTY",
            "箱单发票",
            "车型",
            "车费",
            "兆桦车费",
            "送货司机信息",
            "车队时效要求",
            "代理入货通知",
            "启运港",
            "单托重量",
            "单托金额",
            "币制",
        ),
        todo_checkboxes=(),
        sort=(("预计提货时间", True),),
        base_fields=(
            "发票号",
            "分单号",
            "预计提货时间",
            "实际离厂时间（车队回填）",
            "送货司机信息",
            "车费",
        ),
        groups=_SYS2_FLEET_GROUPS,
        detail_sections=_SYS2_FLEET_GROUPS,
        search_fields=(
            "发票号",
            "分单号",
            "国外客户",
            "下单人",
        ),
        app_token=SYS2_APP_TOKEN,
        table_id=SYS2_SHIPMENT_TABLE,
        project_all=True,
        create_label="新建出货记录",
    ),
    "customer_service": WorkbenchConfig(
        key="customer_service",
        name="客服工作台",
        description="头程订舱 · 计划下发",
        columns=_CS_COLUMNS,
        list_fields=_CS_LIST_FIELDS,
        todo_checkboxes=(BOOKING_CHECKBOX, PLAN_CHECKBOX),
        sort=_CS_SORT,
        base_fields=_CS_BASE_FIELDS,
        groups=_CS_GROUPS,
        search_fields=_FOXCONN_SEARCH_FIELDS,
    ),
    "operator": WorkbenchConfig(
        key="operator",
        name="操作工作台",
        description="到货报关 · 跟踪异常",
        columns=_OP_COLUMNS,
        list_fields=_OP_LIST_FIELDS,
        todo_checkboxes=(CUSTOMS_PLAN_CHECKBOX, FORECAST_CHECKBOX, INSURANCE_CHECKBOX),
        base_fields=_OP_BASE_FIELDS,
        groups=_OP_GROUPS,
        search_fields=_FOXCONN_SEARCH_FIELDS,
        # 操作工作台单票单同步（见《富士康流程TSET-系统开发要求》§6.9）
        allow_group_sync=False,
    ),
    # 威达 · 国内客服：第一版只读（能看 / 能查 / 能筛），无待办勾选框、无合并组。
    "wd_customer_service": WorkbenchConfig(
        key="wd_customer_service",
        name="国内客服工作台",
        description="商机跟进 · 报价准备",
        columns=_WD_CS_COLUMNS,
        list_fields=_WD_CS_LIST_FIELDS,
        todo_checkboxes=(),
        sort=_WD_CS_SORT,
        base_fields=_WD_CS_BASE_FIELDS,
        groups=_WD_CS_GROUPS,
        search_fields=_WD_CS_SEARCH_FIELDS,
        app_token=WD_APP_TOKEN,
        table_id=WD_CUSTOMER_SERVICE_TABLE,
        project_all=True,
        create_label="新建商机",
        # 商机主表上的「新建商机」表单视图（飞书里维护，改表单不用改代码）
        create_form_view_id="veworaFSZ0",
        # 新建商机时「负责航线」默认就是这两位——绝大部分商机都由他们跟，
        # 每次开表单都要手点一遍纯属浪费
        user_defaults={"负责航线": ("Fairy Li", "Kevin Qin")},
        hidden_users=WD_HIDDEN_USERS,
        detail_sections=_WD_CS_DETAIL_SECTIONS,
        # 「单票状态」要看商机上的「订单表」里那两个字段，列表投影里没有，得后端另取
        single_status=True,
        # 只有「商机负责人」是自己的商机才给客服看（超管/管理员看全部）。见 `owner_field`。
        owner_field="商机负责人",
    ),
    # 威达 · 航线价格：以「航线报价」表为**主表**的独立索引页（客服那边它是商机的一个
    # 关联分区，这边是一张自己的表）。索引页能力与国内客服完全同源——同一个
    # `WeidaWorkbench` 组件、同一套自定义列/筛选/就地编辑/填色，只有数据源与列不同。
    #
    # `detail_sections` 有意留空：详情页要摆哪些字段、怎么分组，业务方还没给口径。
    # 留空 = 抽屉把本表 28 个字段铺成一页（前端 `WeidaRecordDrawer` 的兜底），
    # 等口径来了再拆 Tab——`groups` 那份划分只是新建表单的后备，不决定详情页长相。
    "wd_pricing": WorkbenchConfig(
        key="wd_pricing",
        name="航线价格工作台",
        description="航线报价 · 比价",
        columns=_WD_PR_COLUMNS,
        list_fields=_WD_PR_LIST_FIELDS,
        todo_checkboxes=(),
        sort=_WD_PR_SORT,
        base_fields=_WD_PR_BASE_FIELDS,
        groups=_WD_PR_GROUPS,
        search_fields=_WD_PR_SEARCH_FIELDS,
        app_token=WD_APP_TOKEN,
        table_id=WD_PRICING_TABLE,
        project_all=True,
        create_label="新建报价",
        # 航线报价表上的「新增航线报价」表单视图（飞书里维护，改表单不用改代码）。
        # 另有一个「批量增加」表单，是业务自己做批量录入用的，不拿来当新建入口。
        create_form_view_id="vewLCC73tL",
        hidden_users=WD_HIDDEN_USERS,
    ),
    # 威达 · 国内操作：**两张表**喂一个工作台——索引页就是订单表的行（与国内客服同一个
    # `WeidaWorkbench` 组件、同一套自定义列/筛选视图/就地编辑/填色/行高）；iFreight 数据表
    # 在**详情**里单独占一页（`related`，见 `RelatedTable`）。订单表上那个「创建iFreight 数据表」
    # 按钮建出来的行、以及飞书自动化同步进 iFreight 的信息，都在那一页看。
    #
    # 详情抽屉按飞书「操作」Stage 的节点拆 Tab（提货计划 / 报关 / 代理到货计划 / 国外预报 /
    # 其余字段 + 另一页 iFreight 数据），见 `_WD_OP_DETAIL_SECTIONS`。
    "wd_domestic_ops": WorkbenchConfig(
        key="wd_domestic_ops",
        name="国内操作工作台",
        description="交接操作 · iFreight",
        columns=_WD_OP_COLUMNS,
        list_fields=_WD_OP_LIST_FIELDS,
        # 订单表上属于**国内操作自己**的那几步勾选框：任一未勾选即算这一岗还没做完。
        # 索引页据此给出「报关未做」「订舱单没生成」这类现成的筛选片，不用自己搭视图。
        todo_checkboxes=("生成订舱单", "发提货邮件", "报关", "下发操作计划", "发送预报"),
        sort=_WD_OP_SORT,
        base_fields=_WD_OP_BASE_FIELDS,
        groups=_WD_OP_GROUPS,
        detail_sections=_WD_OP_DETAIL_SECTIONS,
        search_fields=_WD_OP_SEARCH_FIELDS,
        app_token=WD_APP_TOKEN,
        table_id=WD_ORDER_TABLE,
        project_all=True,
        # 订单表上**没有**表单视图（2026-10-12 复查：该表只剩 1 个视图 `vewYPd8FhE` 总表，
        # 早先注释里写的「总表/国内客服/国内操作 3 个」已被删掉），
        # 新建订单本来就是商机上「确认订单」那个按钮干的活。留空 → `/config` 的
        # `can_create` 为假 → 索引页不显示「新建」按钮（照空表单渲染出一个空抽屉更糟）。
        hidden_users=WD_HIDDEN_USERS,
        related=_WD_OP_RELATED,
    ),
}


# 「单票状态」的判定来源（见 `service._single_status`）。字段名逐字照飞书 `field_name`：
#   商机表：`航线报价`(双向关联，有内容即已报价) / `是否拿到货`(单选 是/否) / `未拿到货备注`
#   订单表：`订单表`(商机上的双向关联，指向订单表) / `负责航线已核价` / `交接给国内操作`
SINGLE_STATUS_QUOTE_FIELD = "航线报价"
SINGLE_STATUS_GOT_FIELD = "是否拿到货"
SINGLE_STATUS_NOTE_FIELD = "未拿到货备注"
SINGLE_STATUS_ORDER_FIELD = "订单表"
SINGLE_STATUS_PRICED_FIELD = "负责航线已核价"
SINGLE_STATUS_PRICED_VALUE = "已核价"
SINGLE_STATUS_HANDOVER_FIELD = "交接给国内操作"


def detail_field_names(role: str) -> tuple[str, ...]:
    """工作台列表/详情需要投影的全部字段（列表字段 ∪ 分组字段 ∪ 合并字段）。

    列表接口直接按这个集合投影：实测 50 行 × 97 字段仅比 ×14 字段慢约 0.3 秒，
    换来的是详情抽屉**零额外请求**秒开——而按 record_id 单独取一条要 3~5 秒
    （飞书侧要计算 178 个字段里的公式与查找列），所以宁可列表宽一点。
    """
    cfg = get_workbench(role)
    return tuple(
        dict.fromkeys(
            [
                *cfg.list_fields,
                *cfg.base_fields,
                *(f for g in cfg.groups for f in g.fields),
                *(f for s in cfg.detail_sections for f in s.fields),
                MERGE_MASTER_FIELD,
                MERGE_HAWB_FIELD,
            ]
        )
    )


def get_workbench(role: str) -> WorkbenchConfig:
    """按角色取工作台配置。

    参数:
    - role (str): 工作台标识（``customer_service`` / ``operator``）。

    返回:
    - WorkbenchConfig: 工作台配置。

    异常:
    - CustomException: 角色不存在时抛出。
    """
    cfg = WORKBENCHES.get(role)
    if cfg is None:
        raise CustomException(
            msg=f"未知工作台: {role}（可选：{'、'.join(WORKBENCHES)}）",
        )
    return cfg
