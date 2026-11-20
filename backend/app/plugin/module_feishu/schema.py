"""飞书多维表格请求/响应模型。

字段值在飞书侧是异构结构（文本是 ``[{"text":..}]``、人员是 ``[{id,name}..]``、
日期是毫秒时间戳等），这里定义的是**归一化后的对外结构**：字段值为简单类型
（str / int / float / bool / list[str]），前端据此渲染。
"""

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Query
from pydantic import BaseModel, Field

# 合并类型：主单合并 / 分单合并。
MergeType = Literal["master_awb", "hawb"]


class FieldMetaSchema(BaseModel):
    """字段元数据（供前端动态渲染表格列与表单控件）。"""

    name: str = Field(description="字段名（飞书侧唯一键，如「业务类型」）")
    type: int = Field(description="飞书字段类型枚举值")
    ui_type: str = Field(description="飞书 UI 类型（Text/Number/SingleSelect/...）")
    read_only: bool = Field(default=False, description="是否只读（公式/查找/自动编号/按钮等）")
    editable: bool = Field(default=False, description="是否可编辑（本系统表单是否渲染控件）")
    options: list[str] = Field(default_factory=list, description="单选/多选的可选项")
    multiple: bool = Field(
        default=True, description="该字段能否多选（目前只有人员字段会给出 false，取自飞书字段属性）"
    )
    description: str = Field(
        default="", description="飞书里给这个字段写的说明；详情页显示在字段名下方（如「商机信息」的构成公式）"
    )
    is_primary: bool = Field(default=False, description="是否该表的**主字段**（第一列，飞书里那条记录的「名字」）")
    link_table_id: str = Field(
        default="",
        description=(
            "关联字段（单向/双向）指向的**表 id**；非关联字段为空串。"
            "飞书记录接口只回关联的 record_id、不回对方的名字，要显示名字就得拿着这个去查目标表。"
        ),
    )


class WorkbenchGroupSchema(BaseModel):
    """详情页分组（前端渲染为一个 Tab）。"""

    label: str = Field(description="分组名")
    fields: list[FieldMetaSchema] = Field(default_factory=list, description="分组内字段元数据")


class RelatedBlockSchema(BaseModel):
    """详情抽屉里**另一张表**的一页（如国内操作订单详情里的「iFreight 数据」）。

    与 `WorkbenchGroupSchema`（本表字段的一个划分）不同：这一页的字段长在**别的表**上，
    行也不来自本表——按反查字段捞的是「哪些从表记录指着我」。
    """

    key: str = Field(description="这一页的标识；也是 `linked-records` 接口上的 field_name")
    label: str = Field(description="详情 Tab 上显示的名字（如「iFreight 数据」）")
    fields: list[FieldMetaSchema] = Field(default_factory=list, description="这一页要铺的字段（从表里的）")


class WorkbenchSortSchema(BaseModel):
    """列表排序的一项。"""

    field: str = Field(description="排序字段名（飞书 field_name）")
    desc: bool = Field(default=False, description="是否倒序；False 为升序")


class WorkbenchConfigSchema(BaseModel):
    """工作台配置（列表列 + 待办判定 + 详情分组），字段元数据已解析合并。"""

    key: str = Field(description="工作台标识")
    name: str = Field(description="工作台名称")
    description: str = Field(description="工作台说明（副标题）")
    columns: list[str] = Field(description="列表列顺序；其中「货物数据」为前端聚合列")
    cargo_fields: list[str] = Field(description="货物数据聚合列用到的原始字段")
    todo_checkboxes: list[str] = Field(description="待办判定用勾选框（任一未勾选即待办）")
    search_fields: list[str] = Field(description="关键字搜索命中的字段")
    merge_master_field: str = Field(description="主单合并关联字段名")
    merge_hawb_field: str = Field(description="分单合并关联字段名")
    sort: list[WorkbenchSortSchema] = Field(
        default_factory=list, description="列表排序规则（服务端排序，按顺序依次比较）"
    )
    base_fields: list[FieldMetaSchema] = Field(
        default_factory=list, description="详情页顶部常驻的基础信息字段（非 Tab）"
    )
    groups: list[WorkbenchGroupSchema] = Field(default_factory=list, description="详情分组（Tab）")
    create_label: str = Field(default="新建记录", description="索引页「新建」按钮上的字（如「新建商机」）")
    allow_group_sync: bool = Field(
        default=True,
        description="详情保存时是否允许「同步到同组合并组」；为假时前端不显示该勾选框（操作台单票单同步）",
    )
    can_create: bool = Field(
        default=False,
        description=(
            "是否有可用的新建表单（工作台配了飞书**表单视图**才有）。"
            "为假时前端**不显示**「新建」按钮——新建抽屉照表单视图的字段渲染，"
            "没有视图就是个空抽屉。"
        ),
    )
    fields: list[FieldMetaSchema] = Field(
        default_factory=list,
        description="本表全部字段的元数据（含不在任何分组里的）——供前端「自定义列」挑字段用",
    )
    detail_fields: list[FieldMetaSchema] = Field(
        default_factory=list,
        description=(
            "详情页要展示的全部字段，**按飞书表里的字段顺序**、**含按钮**。"
            "与 fields 的区别：fields 是「自定义列」的可选池（按钮没有值，被剔掉），"
            "detail_fields 是照飞书记录详情面板原样铺开的那一份。"
        ),
    )
    detail_groups: list[WorkbenchGroupSchema] = Field(
        default_factory=list,
        description=(
            "详情抽屉的 **Tab 划分**（字段是 detail_fields 的一个划分，合起来就是 detail_fields）。"
            "为空 = 不切 Tab，把 detail_fields 铺在一页上。"
        ),
    )
    related_blocks: list[RelatedBlockSchema] = Field(
        default_factory=list,
        description=(
            "详情抽屉里**另一张表**的页（见 `RelatedBlockSchema`，如「iFreight 数据」）。"
            "与 detail_groups 并列、互不重叠：这里每一页都要按 `key` 单独去取（前端会把它当"
            "一个关联分区，用记录自己的 id 去反查）。为空 = 本工作台只有一张表。"
        ),
    )


class WorkbenchPageSchema(BaseModel):
    """工作台列表分页结果（飞书为游标分页，无 offset）。"""

    items: list[dict[str, Any]] = Field(default_factory=list, description="记录列表")
    total: int = Field(default=0, description="当前筛选条件下的总记录数")
    page_token: str | None = Field(default=None, description="下一页游标；为空表示没有下一页")
    has_more: bool = Field(default=False, description="是否还有下一页")
    pending: str | None = Field(default=None, description="当前筛选的待办项（勾选框字段名）")


class RecordUpdateSchema(BaseModel):
    """更新记录：字段名 → 归一化值。"""

    fields: dict[str, Any] = Field(description="字段名到值的映射（值为归一化后的简单类型）")
    sync_group: bool = Field(
        default=False,
        description="是否把订舱/计划类字段连带同步到同合并组的其他票（组内联动）",
    )


class RecordCreateSchema(BaseModel):
    """新建记录：字段名 → 归一化值。

    没有 ``sync_group``：合并组联动是「改一条带动同组其他票」的行为，新建的记录
    还不属于任何组，无从联动。
    """

    fields: dict[str, Any] = Field(description="字段名到值的映射（值为归一化后的简单类型；只读字段会被忽略）")


class FieldNameSchema(BaseModel):
    """字段名请求体：新建字段与改字段名共用（两者都只要一个名字）。

    对应索引页「空列」——列名可改、内容是落库的真值。
    """

    name: str = Field(min_length=1, description="字段名（不能为空、不能与表中现有字段重名）")


class MergeRequest(BaseModel):
    """合并请求：把若干票并入同一合并组。"""

    record_ids: list[str] = Field(min_length=2, description="参与合并的记录ID列表（至少两条）")
    merge_type: MergeType = Field(description="合并类型：master_awb 主单合并 / hawb 分单合并")
    target_no: str | None = Field(
        default=None,
        description="合并单号（写入「合并主单」/「合并分单」单选字段）；为空则沿用组内已有值",
    )


class UnmergeRequest(BaseModel):
    """拆出合并组请求。"""

    record_ids: list[str] = Field(min_length=1, description="要拆出的记录ID列表")
    merge_type: MergeType = Field(description="合并类型：master_awb / hawb")


@dataclass
class WorkbenchQueryParam:
    """工作台列表查询参数（游标分页 + 待办筛选 + 关键字）。"""

    pending: str | None = Query(
        None,
        description="按单个待办项筛选（勾选框字段名，如「订舱单」），只返回它未勾选的记录",
    )
    page_token: str | None = Query(None, description="分页游标（上一页返回的 page_token）")
    page_size: int = Query(50, ge=1, le=500, description="每页数量")
    search: str | None = Query(None, description="关键字（发票号/分单号/头程主单号/品名）")
