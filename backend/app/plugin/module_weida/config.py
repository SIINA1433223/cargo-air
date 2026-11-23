"""威达工作台的静态配置。

这里只放**表、列、可编辑字段、搜索字段、默认排序**这类低频变化的东西；
字段中文名、类型、选项仍然从数据库（`sys_field_label` / information_schema）读。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LinkBlockDef:
    """详情里的关联分区（客服的航线报价 / 对客方案）。"""

    key: str
    label: str
    table: str
    columns: tuple[str, ...]
    footer_fields: tuple[str, ...] = ()
    expand_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelatedBlockDef:
    """反查从表分区（订单 → iFreight 数据）。"""

    key: str
    label: str
    table: str
    link_field: str
    columns: tuple[str, ...]
    when_field: str | None = None
    when_contains: str | None = None
    # 任一列命中 when_contains 就显示（妙搭的「车队」页：提货车队 或 二程车队 含云港先达）
    when_fields: tuple[str, ...] = ()
    column_labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WorkbenchDef:
    role: str
    name: str
    description: str
    table: str
    columns: tuple[str, ...]
    search_fields: tuple[str, ...]
    editable: tuple[str, ...]
    default_sort: tuple[str, str] = ("update_time", "desc")
    owner_field: str | None = None
    todo_checkboxes: tuple[str, ...] = ()
    # 领域命令：(key, label, 所属详情 Tab)
    commands: tuple[tuple[str, str, str], ...] = ()
    # 详情里的关联分区（表格 + 页脚字段）
    link_blocks: tuple[LinkBlockDef, ...] = ()
    # 反查从表分区（记录 → 从表，一行一条）
    related_blocks: tuple[RelatedBlockDef, ...] = ()
    # 详情里按顺序展示的字段；空 = 全部物理列
    detail_fields: tuple[str, ...] = ()
    # 详情 Tab：(标题, 字段)；字段为空 = 收走前面没点名的其余字段
    detail_sections: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # 全量摊开不翻页（国内操作）
    no_paging: bool = False
    # 指定详情 Tab 内字段的显示名：(Tab, 字段, 显示名)
    section_labels: tuple[tuple[str, str, str], ...] = ()
    # 不允许出现在列表/接口里的内部列
    hidden: tuple[str, ...] = field(
        default=(
            "id",
            "feishu_record_id",
            "create_by",
            "update_by",
            "is_deleted",
        )
    )


WORKBENCHES: dict[str, WorkbenchDef] = {
    "customer_service": WorkbenchDef(
        role="customer_service",
        name="国内客服工作台",
        description="商机跟进 · 报价准备",
        table="biz_opportunity",
        columns=(
            "opportunity_no",
            "master_no",
            "client_ref_no",
            "goods_name",
            "goods_data",
            "goods_special_attr",
            "latest_quote_deadline",
        ),
        search_fields=(
            "opportunity_no",
            "client_ref_no",
            "goods_name",
            "business_type",
            "customer_core_concern",
            "remark",
            "delivery_address",
        ),
        editable=("*",),
        owner_field="opportunity_person_in_charge",
        detail_sections=(
            # 与源码一致：「对客方案」这组字段是关联分区的页脚，不单独成 Tab。
            ("对客方案", ("final_plan_id", "is_goods_received", "goods_not_received_remark")),
            # 妙搭源码里「商机详情」是 fields: [] 兜底组 —— 商机表上没被别处点名的字段
            # 全部按物理列顺序铺进来（业务类型/负责航线/商机负责人/鉴定机构/备注/启运港…），
            # 只有「对客方案」页脚字段与「确认订单」按钮被排除。
            ("商机详情", ()),
        ),
        link_blocks=(
            LinkBlockDef(
                key="pricing",
                label="航线报价",
                table="biz_pricing",
                expand_fields=(
                    "supplier_quote_no",
                    "flight_schedule",
                    "destination_port_demurrage_fee",
                    "cargo_inspection_status",
                    "origin_port_special_fee",
                    "quote_version",
                    "airline",
                    "departure_port",
                    "destination_port",
                    "transit_point",
                    "estimated_transit_days",
                    "latest_arrival_time",
                    "latest_confirmation_time",
                    "cabin_status",
                    "risk_reminder",
                    "route_plan_suggestion",
                    "create_time",
                    "quotation_validity_period",
                ),
                columns=(
                    "customer_plan_draft",
                    "supplier",
                    "route_transit_point",
                    "cost_amount",
                    "level_ratio_requirement",
                    "cost_currency",
                    "earliest_flight_time",
                ),
            ),
            LinkBlockDef(
                key="plan",
                label="对客方案",
                table="biz_plan",
                columns=(
                    "scheme_no",
                    "customer_quote",
                    "quote_currency",
                    "quote_valid_until",
                    "app_scheme_name",
                    "generate_quote",
                    "departure_port",
                    "destination_port",
                    "earliest_flight_time",
                ),
                footer_fields=("final_plan_id", "is_goods_received", "goods_not_received_remark"),
            ),
            LinkBlockDef(
                key="order",
                label="订仓单",
                table="biz_order",
                columns=(
                    "client_ref_no",
                    "opportunity_no",
                    "plan_no",
                    "cost_amount",
                    "level_ratio_req",
                    "cost_currency",
                    "airline",
                    "warehouse_in_operation_time",
                    "operation_requirement",
                    "special_note",
                    "domestic_operation",
                    "supplier",
                    "departure_location",
                    "destination",
                    "flight",
                    "goods_data",
                    "goods_name_special_attr",
                ),
            ),
            LinkBlockDef(
                key="handover",
                label="交接操作",
                table="biz_order",
                columns=(
                    "client_ref_no",
                    "domestic_operation",
                    "supplier",
                    "foreign_agent_name",
                    "sales_person",
                    "customer_core_concern",
                    "need_insurance",
                    "need_pickup",
                    "need_appraisal",
                    "need_declaration",
                    "is_direct",
                    "app_remark",
                    "pickup_fleet",
                    "pickup_info",
                    "pickup_fee",
                    "agent_deadline",
                    "latest_port_arrival_time",
                    "required_door_time",
                    "master_no",
                ),
            ),
        ),
    ),
    "pricing": WorkbenchDef(
        role="pricing",
        name="航线价格工作台",
        description="按商机报价 · 待报价队列",
        # 妙搭源码 ownerField: '' —— 航线价格看全量商机，不按登录人收窄
        owner_field=None,
        table="biz_opportunity",
        columns=(
            "opportunity_no",
            "client_ref_no",
            "goods_name",
            "goods_data",
            "goods_special_attr",
            "latest_quote_deadline",
            "final_plan",
        ),
        search_fields=(
            "client_ref_no",
            "goods_name",
            "goods_special_attr",
            "customer_core_concern",
        ),
        editable=("*",),
        default_sort=("latest_quote_deadline", "desc"),
        link_blocks=(
            LinkBlockDef(
                key="pricing",
                label="航线报价",
                table="biz_pricing",
                columns=(
                    "supplier",
                    "airline",
                    "cost_amount",
                    "cost_currency",
                    "level_ratio_requirement",
                    "departure_port",
                    "destination_port",
                    "transit_point",
                    "earliest_flight_time",
                    "risk_reminder",
                ),
                expand_fields=(
                    "supplier_quote_info",
                    "opportunity_no",
                    "supplier_quote_no",
                    "related_opportunity_no",
                    "flight_schedule",
                    "cargo_inspection_status",
                    "origin_port_special_fee",
                    "quote_version",
                    "route_transit_point",
                    "estimated_transit_days",
                    "latest_arrival_time",
                    "latest_confirmation_time",
                    "cabin_status",
                    "quotation_person_in_charge",
                    "adopted_plan_count",
                    "quotation_validity_period",
                    "route_plan_suggestion",
                    "create_time",
                    "latest_quote_deadline",
                    "deal_status",
                    "quote_create_week",
                ),
            ),
            LinkBlockDef(
                key="pricing_review",
                label="航线核价",
                table="biz_pricing",
                columns=(
                    "supplier",
                    "airline",
                    "cost_amount",
                    "cost_currency",
                    "level_ratio_requirement",
                    "departure_port",
                    "destination_port",
                    "transit_point",
                    "earliest_flight_time",
                    "risk_reminder",
                ),
            ),
        ),
        detail_sections=(
            (
                "货物详情",
                (
                    "business_info",
                    "business_type",
                    "goods_name",
                    "goods_special_attr",
                    "goods_data",
                    "source_location",
                    "destination_port",
                    "goods_ready_time",
                    "request_arrival_time",
                    "customer_core_concern",
                    "opportunity_person_in_charge",
                    "delivery_address",
                    "packaging_form",
                    "transport_clause",
                    "route_requirement",
                    "latest_quote_deadline",
                    "remark",
                ),
            ),
        ),
    ),
    "domestic_ops": WorkbenchDef(
        role="domestic_ops",
        name="国内操作工作台",
        description="交接操作 · iFreight",
        owner_field="domestic_operation",
        table="biz_order",
        columns=(
            "opportunity_no",
            "customer_name",
            "supplier",
            "foreign_agent_name",
            "client_ref_no",
            "bill_status",
            "warehouse_in_operation_time",
            "fee_record",
        ),
        search_fields=(
            "client_ref_no",
            "master_no",
            "driver_info",
            "domestic_operation",
            "fee_record",
        ),
        editable=("*",),
        default_sort=("warehouse_in_operation_time", "asc"),
        todo_checkboxes=(
            "create_ifreight_data",
            "send_pickup_email",
            "second_pickup_email",
            "customs_declaration",
            "issue_operation_plan",
            "send_forecast",
            "invoice_issuance",
        ),
        no_paging=True,
        section_labels=(
            ("提货计划", "pickup_fleet", "头程车队"),
            ("提货计划", "pickup_fee", "头程提货费"),
            ("提货计划", "pickup_info", "头程提货信息"),
            ("提货计划", "goods_ready_time", "头程进仓时间"),
            ("提货计划", "warehouse_in_operation_time", "头程送到时间"),
            ("提货计划", "driver_info", "头程司机信息"),
            ("代理到货计划", "warehouse_in_operation_time", "预计到货时间"),
            ("做账明细", "customer_name", "上客户"),
            ("提货计划", "second_fleet", "二程车队"),
            ("提货计划", "second_pickup_fee", "二程提货费"),
            ("提货计划", "second_remark", "二程备注信息"),
            ("提货计划", "second_warehouse_time", "二程进仓时间"),
            ("提货计划", "second_delivery_time", "二程送达时间"),
            ("提货计划", "second_driver_info", "二程司机信息"),
            ("提货计划", "qingdao_warehouse_no", "入青岛仓号"),
            ("提货计划", "agent_warehouse_no", "代理入仓号"),
            ("报关", "house_bl_no", "分单号"),
            ("报关", "master_no", "主单号"),
            ("报关", "departure_location", "起运地"),
            ("报关", "export_port", "出口口岸"),
            ("代理到货计划", "house_bl_no", "分单号"),
            ("代理到货计划", "customs_declaration_goods_name", "报关品名"),
            ("代理到货计划", "goods_description", "英文品名"),
            ("代理到货计划", "warehouse_in_operation_time", "预计到货时间"),
        ),
        related_blocks=(
            RelatedBlockDef(
                key="ifreight",
                label="iFreight 数据",
                table="biz_ifreight",
                link_field="所属订单",
                columns=(
            "house_bl_no",
            "customer_ref_no",
            "master_no",
            "transport_clause",
            "quantity",
            "piece_unit",
            "gross_weight",
            "dimension",
            "volume",
                ),
            ),
            RelatedBlockDef(
                key="insurance",
                label="投保",
                table="biz_ifreight",
                link_field="所属订单",
                columns=(
                    "house_bl_no",
                    "customer_ref_no",
                    "app_insurance",
                    "insurance_clause",
                    "client_name",
                    "quantity",
                    "piece_unit",
                    "customs_declaration_goods_name",
                    "goods_description",
                    "consignee_address",
                    "declaration_amount",
                    "currency",
                    "port_of_departure",
                    "transport_mode",
                ),
            ),
            RelatedBlockDef(
                key="fleet",
                label="车队",
                table="biz_ifreight",
                link_field="所属订单",
                columns=(
                    "house_bl_no",
                    "quantity",
                    "gross_weight",
                    "dimension",
                    "fleet_volume",
                    "fleet_ratio",
                ),
                when_fields=("pickup_fleet", "second_fleet"),
                when_contains="云港先达",
                column_labels=(
                    ("quantity", "车队件数"),
                    ("gross_weight", "车队毛重"),
                    ("dimension", "车队尺寸"),
                    ("fleet_volume", "车队体积"),
                    ("fleet_ratio", "车队比例"),
                ),
            ),
            RelatedBlockDef(
                key="arrival_report",
                label="到货预报表",
                table="biz_ifreight",
                link_field="所属订单",
                columns=(
                    "house_bl_no",
                    "quantity",
                    "gross_weight",
                    "volume",
                    "dimension",
                    "customs_declaration_goods_name",
                    "goods_description",
                    "shipper_name",
                    "shipper_address",
                    "consignee_name",
                    "consignee_address",
                    "notify_party_name",
                    "notify_party_address",
                    "transport_mode",
                ),
                when_field="supplier",
                when_contains="寰宇通达",
            ),
        ),
        detail_sections=(
            (
                "货物详情",
                (
                    "customer_name",
                    "client_ref_no",
                    "supplier",
                    "foreign_agent_name",
                    "goods_name_special_attr",
                    "flight",
                    "app_remark",
                    "fee_record",
                    "latest_port_arrival_time",
                    "arrival_time",
                    "required_door_time",
                    "goods_data",
                    "level_ratio_req",
                ),
            ),
            (
                "提货计划",
                (
                    "house_bl_no",
                    "goods_data",
                    "goods_name_special_attr",
                    "departure_location",
                    "pickup_fleet",
                    "pickup_fee",
                    "pickup_info",
                    "goods_ready_time",
                    "warehouse_in_operation_time",
                    "driver_info",
                    "qingdao_warehouse_no",
                    "agent_warehouse_no",
                    "second_fleet",
                    "second_pickup_fee",
                    "second_remark",
                    "second_warehouse_time",
                    "second_delivery_time",
                    "second_driver_info",
                ),
            ),
            (
                "报关",
                (
                    "house_bl_no",
                    "departure_location",
                    "export_port",
                ),
            ),
            (
                "代理到货计划",
                (
                    "master_no",
                    "customs_declaration_method",
                    "house_bl_no",
                    "warehouse_in_operation_time",
                    "customs_declaration_goods_name",
                    "goods_description",
                    "agent_operation_requirement",
                    "supplier",
                ),
            ),
            (
                "国外预报",
                (
                    "master_no",
                    "delivery_address",
                    "send_forecast",
                    "forecast_sent_time",
                ),
            ),
            (
                "做账明细",
                (
                    "customer_name",
                    "foreign_agent_name",
                    "supplier",
                    "cost_amount",
                    "cost_currency",
                    "level_ratio_req",
                ),
            ),
            (
                "开票",
                (
                    "bill_status",
                    "invoice_note",
                    "driver_info",
                    "customs_declaration_goods_name",
                ),
            ),
        ),
    ),

    "route_quote": WorkbenchDef(
        role="route_quote",
        name="航线报价",
        description="供应商报价 · 生成对客方案",
        table="biz_pricing",
        columns=(
            "destination_port",
            "departure_port",
            "airline",
            "cost_amount",
            "cost_currency",
            "quotation_validity_period",
            "cabin_status",
            "customer_plan_draft",
        ),
        search_fields=(
            "destination_port",
            "departure_port",
            "airline",
            "flight_schedule",
            "level_ratio_requirement",
        ),
        editable=("*",),
        default_sort=("update_time", "desc"),
        detail_sections=(
            (
                "航线信息",
                (
                    "destination_port",
                    "departure_port",
                    "airline",
                    "flight_schedule",
                    "transit_point",
                    "estimated_transit_days",
                    "earliest_flight_time",
                    "latest_arrival_time",
                    "cabin_status",
                ),
            ),
            (
                "成本与有效期",
                (
                    "cost_amount",
                    "cost_currency",
                    "level_ratio_requirement",
                    "quotation_validity_period",
                    "quotation_status",
                ),
            ),
            (
                "方案",
                (
                    "customer_plan_draft",
                    "route_plan_suggestion",
                    "risk_reminder",
                ),
            ),
            ("其余字段", ()),
        ),
    ),
    "plan": WorkbenchDef(
        role="plan",
        name="对客方案",
        description="对客报价 · 审批与有效期",
        table="biz_plan",
        columns=(
            "app_scheme_name",
            "customer_quote",
            "quote_currency",
            "scheme_status",
            "quote_valid_until",
            "quote_generate_time",
            "pricing_id",
        ),
        search_fields=("app_scheme_name", "supplier_quote_no"),
        editable=("*",),
        default_sort=("update_time", "desc"),
        detail_sections=(
            (
                "方案",
                (
                    "app_scheme_name",
                    "customer_quote",
                    "quote_currency",
                    "quote_valid_until",
                    "scheme_status",
                    "approval_result",
                    "customer_feedback",
                ),
            ),
            (
                "来源",
                (
                    "pricing_id",
                    "supplier_quote_no",
                    "quote_generate_time",
                    "generate_quote",
                ),
            ),
            ("其余字段", ()),
        ),
    ),

}


# 物理类型 → 前端控件类型
TYPE_MAP = {
    "varchar": "text",
    "char": "text",
    "text": "textarea",
    "tinytext": "textarea",
    "mediumtext": "textarea",
    "longtext": "textarea",
    "tinyint": "checkbox",
    "smallint": "number",
    "int": "number",
    "bigint": "number",
    "decimal": "number",
    "float": "number",
    "double": "number",
    "date": "date",
    "datetime": "datetime",
    "timestamp": "datetime",
    "json": "json",
}


# 标签兜底：库里没有 sys_field_label 时用
LABEL_FALLBACK = {
    "client_ref_no": "客户 REF NO.",
    "business_type": "业务类型",
    "goods_name": "货物品名",
    "piece_count": "件数",
    "gross_weight_kg": "毛重（kg）",
    "goods_ready_time": "货好时间",
    "latest_quote_deadline": "最晚报价截止时间",
    "is_goods_received": "是否拿到货",
    "goods_special_attr": "货物特殊属性",
    "responsible_route": "负责航线",
    "customer_core_concern": "客户核心关注点",
    "domestic_operation": "国内操作",
    "warehouse_in_operation_time": "入仓操作时间",
    "flight": "航班",
    "operation_single_status": "操作单票状态",
    "pickup_fleet": "提货车队",
    "master_no": "主单号",
    "fee_record": "费用记录",
    "update_time": "更新时间",
}


def get_workbench(role: str) -> WorkbenchDef | None:
    return WORKBENCHES.get(role)


# 详情页动作按钮：字段 -> (未执行文案, 已执行文案, 所属 Tab)
DETAIL_ACTIONS: dict[str, tuple[str, str, str]] = {
    "create_booking": ("创建订舱", "已创建订舱", "订单"),
    "send_pickup_email": ("头程提货邮件", "已发头程提货邮件", "提货计划"),
    "customs_declaration": ("报关", "已报关", "报关"),
    "issue_operation_plan": ("下发操作计划", "已下发操作计划", "代理到货计划"),
    "send_forecast": ("发送预报", "已发送预报", "国外预报"),
    "create_ifreight_data": ("新增分单", "已新增分单", "货物详情"),
    "invoice_issuance": ("开票", "已开票", "开票"),
    "second_pickup_email": ("二程提货邮件", "已发二程提货邮件", "提货计划"),
}
