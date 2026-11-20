"""详情页的三样后端支撑：字段说明、详情字段清单（含按钮）、关联记录展开。

都是**只读**能力，桩在 ``_raw_field_items`` / ``_list_raw_records`` 这一层，
不打飞书、不碰任何真实业务数据。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import pytest
from redis.asyncio.client import Redis

from app.core.exceptions import CustomException
from app.plugin.module_feishu.service import (
    FeishuBitableService,
    _detail_groups,
    _metas_from_raw,
    normalize_field_value,
)
from app.plugin.module_feishu.workbench import WorkbenchGroup

ROLE = "wd_customer_service"
QUOTE = "航线报价"  # 商机主表上的双向关联字段（一对多）


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _field(field_id: str, name: str, ftype: int, **extra: Any) -> dict[str, Any]:
    return {"field_id": field_id, "field_name": name, "type": ftype, "ui_type": "", **extra}


# ============================ 原始字段 → 元数据 ============================


def test_字段顺序照飞书返回的顺序_不重排() -> None:
    """详情页要照飞书的字段顺序铺，顺序丢了就只能回去维护一份常量（改表即漂移）。"""
    raw = [_field("a", "商机信息", 20), _field("b", "客户 REF NO.", 1), _field("c", "货物数据", 20)]

    assert [m.name for m in _metas_from_raw(raw)] == ["商机信息", "客户 REF NO.", "货物数据"]


def test_字段说明透传() -> None:
    raw = [_field("a", "商机信息", 20, description="客户简称+客户的发票号+件数。")]

    assert _metas_from_raw(raw)[0].description == "客户简称+客户的发票号+件数。"


def test_没写说明的字段给空串而不是缺键() -> None:
    """飞书对没填说明的字段**不给 description 键**；缺键会让 pydantic 报错。"""
    assert _metas_from_raw([_field("a", "备注", 1)])[0].description == ""


def test_按钮既只读又不可编辑() -> None:
    """按钮(3001)没有值，详情页只做占位，绝不能渲染成输入控件。"""
    meta = _metas_from_raw([_field("a", "确认订单", 3001)])[0]

    assert meta.read_only is True
    assert meta.editable is False


def test_只有单选多选收可选项() -> None:
    """查找字段(19)的 property 里也可能带东西，但它的可选项不是拿来给用户选的。"""
    raw = [
        _field("a", "业务类型", 3, property={"options": [{"name": "空运出口"}]}),
        _field("b", "航空公司", 19, property={"options": [{"name": "CX"}]}),
    ]
    metas = _metas_from_raw(raw)

    assert metas[0].options == ["空运出口"]
    assert metas[1].options == []


def test_人员字段的_multiple_跟着飞书属性走() -> None:
    raw = [
        _field("a", "负责航线", 11, property={"multiple": True}),
        _field("b", "商机负责人", 11, property={"multiple": False}),
    ]
    metas = _metas_from_raw(raw)

    assert metas[0].multiple is True
    assert metas[1].multiple is False


# ============================ 公式/查找字段的值 ============================


def test_公式字段的值要拆掉飞书的类型包装() -> None:
    """公式/查找字段返回的是 ``{"type":..,"value":[{"text":..}]}``，不是裸的文本片段。

    这个形状没有顶层 ``text``，旧代码一路落到最后返回 None——表现是**飞书里明明有值、
    详情页（以及索引页挑出来的公式列）整片空白**。它跟「字段真的没值」长得一模一样，
    所以必须有回归用例钉住。
    """
    raw = {"type": 1, "value": [{"text": "南通超达/9026010437-C01/1", "type": "text"}]}

    assert normalize_field_value(20, raw) == "南通超达/9026010437-C01/1"
    assert normalize_field_value(19, raw) == "南通超达/9026010437-C01/1"


def test_公式结果不是文本时也要能显示() -> None:
    """公式算出数字/选项/多选时，包装里的 ``type`` 跟着变，但拆包装这一步是一样的。"""
    assert normalize_field_value(20, {"type": 2, "value": 31}) == "31"
    assert normalize_field_value(20, {"type": 4, "value": ["危险品", "带干燥剂"]}) == "危险品 带干燥剂"


def test_公式字段为空仍然是空() -> None:
    assert normalize_field_value(20, None) is None
    assert normalize_field_value(20, {"type": 1, "value": None}) is None


def test_查找回来的日期要按日期归一化而不是留一串毫秒() -> None:
    """包装里的 ``type`` 是判断内层数字含义的**唯一**线索。

    实测对客方案的「最早航班时间」是对客方案表上的**查找列(19)**，源字段在航线报价表里
    是日期(5)；飞书回的是 ``{"type": 5, "value": [1789187580000]}``。只拆包装不带类型，
    界面上就是一串 ``1789187580000``（用户截图里那个值），而它其实是个能看的时间。
    """
    raw = {"type": 5, "value": [1789187580000]}

    # 北京时间（服务端统一用 +8 展示）
    assert normalize_field_value(19, raw) == "2026-09-12 12:33:00"
    assert normalize_field_value(20, raw) == "2026-09-12 12:33:00"


def test_查找回来的日期是空或异常值时不留半截() -> None:
    """多值查找 / 解析不了的值都退回文本拼接：宁可显示得难看，也不要抛异常让整页打不开。

    注意最后一行的语义是**回落**而不是丢弃——壳里说是日期，但内容解析不出时间戳时，
    仍然把原文显示出来（显示 ``坏了`` 总好过显示空白，让人以为这条记录没值）。
    """
    assert normalize_field_value(19, {"type": 5, "value": []}) is None
    assert normalize_field_value(19, {"type": 5, "value": [1, 2]}) == "1 2"
    assert normalize_field_value(19, {"type": 5, "value": ["坏了"]}) == "坏了"


def test_非日期的查找列仍走原来的文本抽取() -> None:
    """选项/文本的抽取本来就是对的，别顺手改成别的形态——把单选的查找列从 ``"AAK"``
    变成 ``["AAK"]``，过滤、排序、比对都要跟着改一遍。"""
    assert normalize_field_value(19, {"type": 3, "value": ["AAK"]}) == "AAK"
    assert normalize_field_value(19, {"type": 1, "value": [{"text": "WILCGO"}]}) == "WILCGO"


# ============================ 详情字段清单 ============================


class FakeConfig(FeishuBitableService):
    """字段元数据由用例直接给，不打飞书。"""

    def __init__(self, raw: list[dict[str, Any]]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.raw = raw

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        return list(self.raw)


def test_详情字段含按钮而自定义列不含() -> None:
    """两个清单的口径不同：详情页要按钮占位，自定义列的可选池里按钮是噪音（它没有值）。"""
    svc = FakeConfig([_field("a", "客户 REF NO.", 1), _field("b", "确认订单", 3001)])

    cfg = run(svc.get_workbench_config(ROLE))

    assert [m.name for m in cfg.detail_fields] == ["客户 REF NO.", "确认订单"]
    assert [m.name for m in cfg.fields] == ["客户 REF NO."]


# ============================ 详情 Tab 划分 ============================


def _names(groups: list[Any], label: str) -> list[str]:
    return [m.name for g in groups if g.label == label for m in g.fields]


def test_没配详情分组就不切_Tab() -> None:
    """只有一个「全部」页时不摆一排只有一个按钮的 Tab——那是纯噪音。"""
    metas = _metas_from_raw([_field("a", "件数", 2)])

    assert _detail_groups(metas, ()) == []


def test_留空的那一组收走其余字段() -> None:
    """主分组留空 = 其他组没点名的都归它，省得把四十来个字段名抄一遍。"""
    metas = _metas_from_raw([_field("a", "商机信息", 20), _field("b", "件数", 2), _field("c", "备注", 1)])
    sections = (WorkbenchGroup(label="商机详情", fields=()), WorkbenchGroup(label="货物详情", fields=("件数",)))

    groups = _detail_groups(metas, sections)

    assert _names(groups, "商机详情") == ["商机信息", "备注"]
    assert _names(groups, "货物详情") == ["件数"]


def test_分组顺序照飞书不照配置() -> None:
    """配置里把「备注」写在「件数」前面，出来的还是飞书表里的次序——
    维护第二份顺序常量，飞书那边调一列就得跟着改一次，迟早漂移。"""
    metas = _metas_from_raw([_field("a", "件数", 2), _field("b", "备注", 1)])
    sections = (WorkbenchGroup(label="主", fields=()), WorkbenchGroup(label="货", fields=("备注", "件数")))

    groups = _detail_groups(metas, sections)

    assert _names(groups, "货") == ["件数", "备注"]


def test_分组合起来正好是详情字段不重不漏() -> None:
    """切 Tab 只是换个摆法：漏掉一组等于字段凭空消失、重复一组等于同一格出现两次。"""
    raw = [_field(f"f{i}", n, 1) for i, n in enumerate(["A", "B", "C", "D", "E"])]
    metas = _metas_from_raw(raw)
    sections = (WorkbenchGroup(label="主", fields=()), WorkbenchGroup(label="货", fields=("B", "D")))

    groups = _detail_groups(metas, sections)

    flat = [m.name for g in groups for m in g.fields]
    assert sorted(flat) == ["A", "B", "C", "D", "E"]
    # 组内仍照飞书顺序（B 在 D 前、A 在 C 前），只是按组分了堆
    assert _names(groups, "主") == ["A", "C", "E"]
    assert _names(groups, "货") == ["B", "D"]


def test_两个空组只有第一个吃到() -> None:
    metas = _metas_from_raw([_field("a", "A", 1)])
    sections = (WorkbenchGroup(label="一", fields=()), WorkbenchGroup(label="二", fields=()))

    groups = _detail_groups(metas, sections)

    assert _names(groups, "一") == ["A"]


def test_Stage_字段不进任何分组() -> None:
    """Stage（type 24，威达订单表上的 国内客服/操作/航线）读不出值、节点也读不出定义，
    摆进详情就是一格永远空白的只读框——所以整类滤掉，配置里点名了也不给。它最容易漏进来
    的地方是那个**留空的兜底组**（「其余字段」），那里不点名也照收。"""
    raw = [_field("a", "货物数据", 1), _field("b", "操作", 24), _field("c", "备注", 1)]
    metas = _metas_from_raw(raw)
    sections = (WorkbenchGroup(label="其余字段", fields=()),)

    groups = _detail_groups(metas, sections)

    assert _names(groups, "其余字段") == ["货物数据", "备注"]

    # 点名了也不出：收进来只会是空格子
    named = _detail_groups(metas, (WorkbenchGroup(label="操作", fields=("操作",)), WorkbenchGroup(label="其余字段", fields=())))
    assert _names(named, "操作") == []
    assert _names(groups, "二") == []


def test_配置里写了表里没有的字段就丢掉() -> None:
    """改表后配置没跟上时，少一格总好过整个详情页报错。"""
    metas = _metas_from_raw([_field("a", "件数", 2)])
    sections = (WorkbenchGroup(label="主", fields=()), WorkbenchGroup(label="货", fields=("件数", "已删字段")))

    groups = _detail_groups(metas, sections)

    assert _names(groups, "货") == ["件数"]


def test_威达商机的货物字段拆出去后商机那屏不含货物() -> None:
    """真配置 + 假字段表：这一步是给「拆 Tab」兜底的——拆错组（比如货物字段两边都有）
    在界面上表现为同一格出现两次，很难一眼看出来。"""
    from app.plugin.module_feishu.workbench import _WD_CS_DETAIL_SECTIONS

    raw = [
        _field("a", "商机信息", 20),
        _field("b", "货物数据", 20),
        _field("c", "件数", 2),
        _field("d", "毛重（kg）", 2),
        _field("e", "货物品名", 1),
        _field("f", "备注", 1),
        _field("g", "负责航线", 11),
    ]
    groups = _detail_groups(_metas_from_raw(raw), _WD_CS_DETAIL_SECTIONS)

    assert [g.label for g in groups] == ["商机详情", "货物详情", "对客方案"]
    assert _names(groups, "货物详情") == ["货物数据", "件数", "毛重（kg）", "货物品名"]
    assert _names(groups, "商机详情") == ["商机信息", "备注", "负责航线"]


def test_威达对客方案的收尾字段自成一组且不再出现在商机详情里() -> None:
    """「对客方案」这一组的组名是**前端约定的锚点**：抽屉里分组名与关联字段同名时，
    这一组不单独成 Tab，而是铺在对客方案那张表格**下方**（见 WeidaRecordDrawer）。

    所以这里断言的不只是分组，还有那个名字本身——改成「对客方案结果」之类，这四个字段
    会静悄悄变成第 5 个 Tab（不报错，只是位置不对）。副产物是它们从商机详情那屏消失，
    这正是要的：它们是「方案敲定之后」的字段。"""
    from app.plugin.module_feishu.workbench import _WD_CS_DETAIL_SECTIONS

    raw = [
        _field("a", "商机信息", 20),
        _field("b", "最终对客方案", 18),
        _field("c", "确认订单", 3001),
        _field("d", "是否拿到货", 3),
        _field("e", "未拿到货备注", 1),
        _field("f", "客户备注", 1),
    ]
    groups = _detail_groups(_metas_from_raw(raw), _WD_CS_DETAIL_SECTIONS)

    assert [g.label for g in groups] == ["商机详情", "货物详情", "对客方案"]
    assert _names(groups, "对客方案") == ["最终对客方案", "确认订单", "是否拿到货", "未拿到货备注"]
    assert _names(groups, "商机详情") == ["商机信息", "客户备注"]


# ============================ 列表要抽全部关联 id ============================


class FakeList(FeishuBitableService):
    """只截住 ``records/search`` 那一次请求，返回一条记录。"""

    def __init__(self, raw: list[dict[str, Any]], record: dict[str, Any]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.raw = raw
        self.record = record

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        return list(self.raw)

    async def _field_types(self) -> dict[str, int]:
        return {str(f["field_name"]): int(f["type"]) for f in self.raw}

    async def _search_page(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"items": [self.record], "total": 1, "has_more": False, "page_token": None}

    async def _token(self) -> str:
        # 不打飞书取真 token（本用例只验 link_ids 的抽取）
        return "fake-token"


def test_非合并的关联字段也要抽_link_ids() -> None:
    """「航线报价」的关联 id 以前被写死的两份合并字段挡在外面，详情页拿不到那几条报价。"""
    svc = FakeList(
        raw=[_field("a", "商机编号", 1005), _field("b", QUOTE, 21, property={"table_id": "tbl_q"})],
        # 线上形态就是 link_record_ids（``_links_to_ids`` 认的键）
        record={"record_id": "rec_1", "fields": {QUOTE: {"link_record_ids": ["recA", "recB"]}}},
    )

    page = run(svc.list_workbench(role=ROLE))

    assert page["items"][0]["link_ids"][QUOTE] == ["recA", "recB"]


# ============================ 关联记录展开 ============================


class FakeLinked(FeishuBitableService):
    """三张表：本工作台的表（含关联字段）+ 报价表 + 供应商表。

    ``_search_page`` 也桩掉了：解析关联名字走的是投影成单列的 ``records/search``，
    按 ``table_id`` 路由到对应的假数据，并记下每次要了哪几列。
    """

    def __init__(self) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.records: dict[str, list[dict[str, Any]]] = {}
        # (表 id, 投影的列)，用来验证「只拉主字段一列」
        self.searches: list[tuple[str, tuple[str, ...]]] = []

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        return list(self.tables.get(table_id, []))

    async def _list_raw_records(
        self, table_id: str, limit: int, field_names: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return list(self.records.get(table_id, []))[:limit]

    async def _search_page(
        self,
        filter_obj: dict[str, Any] | None,
        page_token: str | None,
        page_size: int,
        field_names: list[str] | None = None,
        sort: list[dict[str, Any]] | None = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        cols = tuple(field_names or ())
        self.searches.append((table_id, cols))
        items = [
            {"record_id": r["record_id"], "fields": {c: (r.get("fields") or {}).get(c) for c in cols}}
            for r in self.records.get(table_id, [])
        ]
        return {"items": items, "has_more": False, "page_token": None}

    async def _token(self) -> str:
        # 不打飞书取真 token（这几个用例只验归一化）
        return "fake-token"


@pytest.fixture
def fake_linked() -> FakeLinked:
    svc = FakeLinked()
    svc.tables[""] = [_field("a", QUOTE, 21, property={"table_id": "tbl_q"})]
    svc.tables["tbl_q"] = [
        _field("q1", "供应商报价信息", 20, is_primary=True),
        _field("q2", "成本金额", 2),
        _field("q3", "报价负责人", 11, property={"multiple": False}),
        _field("q4", "供应商", 18, property={"table_id": "tbl_s"}),
        _field("q5", "原始报价附件", 17, description="供应商给的原始报价。"),
    ]
    svc.tables["tbl_s"] = [
        _field("s1", "供应商名称", 1, is_primary=True),
        _field("s2", "联系人", 1),
    ]
    svc.records["tbl_q"] = [
        {
            "record_id": "recA",
            "fields": {
                "供应商报价信息": "HM2610392/1/31CNY",
                "成本金额": 31,
                "报价负责人": [{"id": "ou_lily", "name": "Lily Sun"}],
                "供应商": {"link_record_ids": ["recS1"]},
            },
        },
        {"record_id": "recB", "fields": {"供应商报价信息": "HM2610392/1/34CNY", "成本金额": 34}},
        {"record_id": "recZ", "fields": {"供应商报价信息": "别的商机的报价"}},
    ]
    svc.records["tbl_s"] = [
        {"record_id": "recS1", "fields": {"供应商名称": "DHL 空运", "联系人": "王工"}},
        {"record_id": "recS2", "fields": {"供应商名称": "顺丰国际", "联系人": "李工"}},
    ]
    return svc


def test_关联记录只返回点名要的那几条(fake_linked: FakeLinked) -> None:
    got = run(fake_linked.linked_records(QUOTE, ["recB", "recA"], role=ROLE))

    assert [r["record_id"] for r in got["records"]] == ["recA", "recB"]


def test_关联表自己也带字段定义_前端不用再问一次(fake_linked: FakeLinked) -> None:
    """关联表不属于任何工作台，`/config` 取不到它——字段定义必须随记录一起给。"""
    got = run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))

    names = [f["name"] for f in got["fields"]]
    assert names == ["供应商报价信息", "成本金额", "报价负责人", "供应商", "原始报价附件"]
    by_name = {f["name"]: f for f in got["fields"]}
    assert by_name["原始报价附件"]["description"] == "供应商给的原始报价。"
    assert by_name["报价负责人"]["multiple"] is False


def test_关联记录里的人员与关联也抽了原始_id(fake_linked: FakeLinked) -> None:
    """报价记录上挂着负责人与供应商，姓名/文本之外还得有 open_id 与 record_id，
    否则详情页既显示不出「人」，后续也没依据做跳转或回写。"""
    rec = run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))["records"][0]

    assert rec["user_refs"]["报价负责人"] == [{"id": "ou_lily", "name": "Lily Sun"}]
    assert rec["link_ids"]["供应商"] == ["recS1"]
    # 人员字段不该同时出现在 link_ids 里（两个分支互相污染）
    assert "报价负责人" not in rec["link_ids"]


def test_没给_id_时不打飞书(fake_linked: FakeLinked) -> None:
    assert run(fake_linked.linked_records(QUOTE, [], role=ROLE)) == {"fields": [], "records": []}


def test_不是关联字段就报错(fake_linked: FakeLinked) -> None:
    fake_linked.tables[""] = [_field("a", "成本金额", 2)]

    with pytest.raises(CustomException, match="不是关联字段"):
        run(fake_linked.linked_records("成本金额", ["recA"], role=ROLE))


def test_关联字段要带上目标表_id() -> None:
    """飞书记录接口只回关联的 record_id、不回对方的名字，要知道去哪张表查就得有表 id。"""
    raw = [
        _field("a", "客户", 21, property={"table_id": "tbl_c"}),
        _field("b", "成本金额", 2, property={"table_id": "tbl_不该有"}),
    ]
    metas = _metas_from_raw(raw)

    assert metas[0].link_table_id == "tbl_c"
    # 非关联字段即使 property 里混进了 table_id 也不能带出来（那是查找字段的来源表）
    assert metas[1].link_table_id == ""


def test_报价记录里的供应商显示成名字而不是_id(fake_linked: FakeLinked) -> None:
    """报价卡片上的「供应商」原来只有 record_id 可显示（飞书不给名字），
    详情页要一屏看全，这一格不能空着。"""
    rec = run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))["records"][0]

    assert rec["fields"]["供应商"] == ["DHL 空运"]
    # 原始 id 仍然留着（后续要跳转/回写都靠它）
    assert rec["link_ids"]["供应商"] == ["recS1"]


def test_解析关联名字只投影主字段一列(fake_linked: FakeLinked) -> None:
    """目标表可能几百行上百列，为了一个名字把整表拉回来纯属浪费。"""
    run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))

    to_supplier = [s for s in fake_linked.searches if s[0] == "tbl_s"]
    assert to_supplier == [("tbl_s", ("供应商名称",))]


def test_关联表整表也要投影_别把用不上的列都拉回来(fake_linked: FakeLinked) -> None:
    """同一张 69 行的表，``/records`` 是 4 秒、投影成 5 列是 1.3 秒——
    差的就是飞书把用不上的公式/查找也算了一遍。按钮没有值，不该占一列。"""
    fake_linked.tables["tbl_q"] = [*fake_linked.tables["tbl_q"], _field("q6", "确认订单", 3001)]

    run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))

    to_quote = [s for s in fake_linked.searches if s[0] == "tbl_q"]
    assert to_quote == [
        ("tbl_q", ("供应商报价信息", "成本金额", "报价负责人", "供应商", "原始报价附件"))
    ]


def test_目标表查不到这个人就退回_id(fake_linked: FakeLinked) -> None:
    """查不到名字时留空和「这个字段本来就没值」长得一模一样，宁可露出一串 id。"""
    fake_linked.records["tbl_s"] = []

    rec = run(fake_linked.linked_records(QUOTE, ["recA"], role=ROLE))["records"][0]

    assert rec["fields"]["供应商"] == ["recS1"]


def test_记录没挂关联就不去打那张表(fake_linked: FakeLinked) -> None:
    """一页记录里往往只有少数几行挂着值，为没用到的那几张表各跑一趟纯属白等。"""
    run(fake_linked.linked_records(QUOTE, ["recB"], role=ROLE))  # recB 没挂供应商

    assert [s for s in fake_linked.searches if s[0] == "tbl_s"] == []


class FakeLinkedWrite(FakeLinked):
    """在 FakeLinked 上补两件「写」的事：记住每批更新、记下新建请求。

    ``_table_endpoint`` 也桩掉：真实现要读飞书凭据（取 app_id/secret），用例里没有。
    """

    def __init__(self) -> None:
        super().__init__()
        # (表 id, 这一批更新)
        self.writes: list[tuple[str, list[dict[str, Any]]]] = []
        self.posted: list[tuple[str, dict[str, Any]]] = []
        # (HTTP 方法, 地址)：删除没有请求体，`posted` 分不出来，另记一份
        self.calls: list[tuple[str, str]] = []
        self.response: dict[str, Any] = {"data": {"record": {"record_id": "rec_new"}}}

    async def _batch_update(self, updates: list[dict[str, Any]], action: str, table_id: str = "") -> int:
        self.writes.append((table_id, updates))
        return len(updates)

    async def _table_endpoint(self, table_id: str, path: str) -> str:
        return f"https://example.invalid/{table_id}{path}"

    async def call(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, url))
        self.posted.append((url, kwargs.get("json") or {}))
        return self.response


@pytest.fixture
def fake_linked_write(
    fake_linked: FakeLinked, monkeypatch: pytest.MonkeyPatch
) -> FakeLinkedWrite:
    from app.plugin.module_feishu import service as service_module

    svc = FakeLinkedWrite()
    svc.tables = fake_linked.tables
    svc.records = fake_linked.records
    svc.searches = fake_linked.searches
    # 报价表上放几个可写字段：文本 / 数字 / 回指商机的双向关联
    svc.tables["tbl_q"] = [
        *svc.tables["tbl_q"],
        _field("q6", "等级、比例要求", 1),
        _field("q7", "关联商机", 21, property={"table_id": "tbl_o"}),
    ]
    # 商机表上也得有个**非关联**的普通字段，才能区分「字段不存在」与「字段存在但不是关联」
    svc.tables[""] = [*svc.tables[""], _field("a2", "客户 REF NO.", 1)]

    # **二级关联**：对客方案表上的「费用明细」指向费用明细表。这条字段长在**对客方案**表上、
    # 不在工作台表（商机主表）上，所以调用方必须把父表 id 传下来——见 `_link_target_table`。
    svc.tables["tbl_plan"] = [
        _field("p1", "方案编号", 1005, is_primary=True),
        _field("p2", "费用明细", 21, property={"table_id": "tbl_fee"}),
    ]
    svc.tables["tbl_fee"] = [
        _field("f1", "明细编号", 1005, is_primary=True),
        _field("f2", "关联的对客方案", 21, property={"table_id": "tbl_plan"}),
        _field("f3", "费用类别", 3, property={"options": [{"name": "空运费"}, {"name": "单证费"}]}),
        _field("f4", "金额", 2),
        _field("f5", "备注", 1),
    ]
    svc.records["tbl_fee"] = [
        {"record_id": "recF1", "fields": {"费用类别": "空运费", "金额": 18}},
        {"record_id": "recF2", "fields": {"费用类别": "单证费", "金额": 235}},
    ]

    async def fake_http(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        return await svc.call(method, url, token, **kwargs)

    monkeypatch.setattr(service_module, "_http_json", fake_http)
    return svc


def test_改关联记录写在关联表而不是商机表(fake_linked_write: FakeLinkedWrite) -> None:
    """详情页展开的报价卡片是**另一张表**里的记录，照着商机表的字段类型去写会一个字段都认不出。"""
    result = run(
        fake_linked_write.update_linked_record(QUOTE, "recA", {"成本金额": 30}, role=ROLE)
    )

    assert result == {"record_id": "recA", "written": 1}
    table_id, updates = fake_linked_write.writes[0]
    assert table_id == "tbl_q"
    assert updates == [{"record_id": "recA", "fields": {"成本金额": 30}}]


def test_改关联记录只读字段照样被丢掉(fake_linked_write: FakeLinkedWrite) -> None:
    """「供应商报价信息」是公式、「供应商报价编号」是自动编号，送过去只会被整单拒。"""
    run(fake_linked_write.update_linked_record(QUOTE, "recA", {"等级、比例要求": "2C直飞 1:80"}, role=ROLE))

    _, updates = fake_linked_write.writes[0]
    assert updates[0]["fields"] == {"等级、比例要求": "2C直飞 1:80"}


def test_改关联记录全是只读字段就报错(fake_linked_write: FakeLinkedWrite) -> None:
    with pytest.raises(CustomException, match="没有可写入的字段"):
        run(fake_linked_write.update_linked_record(QUOTE, "recA", {"供应商报价信息": "X"}, role=ROLE))


def test_新建关联报价写在关联表并带上回指商机(fake_linked_write: FakeLinkedWrite) -> None:
    """新报价必须挂回当前商机，否则它在飞书里是个查不到、也没人认领的孤儿行。"""
    result = run(
        fake_linked_write.create_linked_record(
            QUOTE, {"关联商机": ["recOpp"], "成本金额": 20.5}, role=ROLE
        )
    )

    assert result == {"record_id": "rec_new", "written": 2}
    url, body = fake_linked_write.posted[0]
    assert url.endswith("/tbl_q/records")
    assert body["fields"] == {"关联商机": ["recOpp"], "成本金额": 20.5}


def test_新建关联记录一项都写不了时报错(fake_linked_write: FakeLinkedWrite) -> None:
    with pytest.raises(CustomException, match="没有可写入的字段"):
        run(fake_linked_write.create_linked_record(QUOTE, {"供应商报价信息": "X"}, role=ROLE))


def test_不是关联字段就不能当关联表写(fake_linked_write: FakeLinkedWrite) -> None:
    """拿普通字段名去当「目标表」用，只会静默写到一张不相干的表上。"""
    with pytest.raises(CustomException, match="不是关联字段"):
        run(fake_linked_write.update_linked_record("客户 REF NO.", "recA", {"成本金额": 1}, role=ROLE))


def test_字段名不存在就报错(fake_linked: FakeLinked) -> None:
    with pytest.raises(CustomException, match="不存在字段"):
        run(fake_linked.linked_records("查无此字段", ["recA"], role=ROLE))


def test_未知工作台也拦下(fake_linked: FakeLinked) -> None:
    with pytest.raises(CustomException, match="未知工作台"):
        run(fake_linked.linked_records(QUOTE, ["recA"], role="no_such_role"))


# ============================ 二级关联（对客方案费用明细） ============================
#
# 对客方案的「费用明细」长在**对客方案表**上，而工作台的表是商机主表。默认只扫工作台表的
# 话，这条字段压根找不到（`不存在字段「费用明细」`），所以这几个用例都在盯「父表 id 有没有
# 真的被用上」——漏传不会报错，只会静默去写一张不相干的表。


def test_二级关联按父表解析出子表(fake_linked_write: FakeLinkedWrite) -> None:
    got = run(
        fake_linked_write.linked_records("费用明细", ["recF1"], role=ROLE, table_id="tbl_plan")
    )

    assert [f["name"] for f in got["fields"]] == ["明细编号", "关联的对客方案", "费用类别", "金额", "备注"]
    assert [r["record_id"] for r in got["records"]] == ["recF1"]
    # 拉的是**费用明细表**，不是对客方案表也不是商机主表
    assert {s[0] for s in fake_linked_write.searches} == {"tbl_fee"}


def test_不传父表就找不到对客方案上的字段(fake_linked_write: FakeLinkedWrite) -> None:
    """这条钉的是「为什么要有 table_id 这个参数」：默认只看工作台表。

    也提醒后来人：二级关联漏传父表**不会**悄悄用错表，而是当场报「不存在字段」——
    比写坏一条数据强，但也要知道是这个原因。"""
    with pytest.raises(CustomException, match="不存在字段"):
        run(fake_linked_write.linked_records("费用明细", ["recF1"], role=ROLE))


def test_二级修改写在费用明细表上(fake_linked_write: FakeLinkedWrite) -> None:
    result = run(
        fake_linked_write.update_linked_record(
            "费用明细", "recF1", {"金额": 20}, role=ROLE, table_id="tbl_plan"
        )
    )

    assert result == {"record_id": "recF1", "written": 1}
    table_id, updates = fake_linked_write.writes[0]
    assert table_id == "tbl_fee"
    assert updates == [{"record_id": "recF1", "fields": {"金额": 20}}]


def test_二级新建把回指方案写进费用明细表(fake_linked_write: FakeLinkedWrite) -> None:
    """新明细靠「关联的对客方案」挂回当前方案；没有它这条明细在飞书里谁都不认。"""
    result = run(
        fake_linked_write.create_linked_record(
            "费用明细",
            {"关联的对客方案": ["recPlan"], "费用类别": "空运费", "金额": 18},
            role=ROLE,
            table_id="tbl_plan",
        )
    )

    assert result["record_id"] == "rec_new"
    url, body = fake_linked_write.posted[0]
    assert url.endswith("/tbl_fee/records")
    assert body["fields"] == {"关联的对客方案": ["recPlan"], "费用类别": "空运费", "金额": 18}


def test_删除关联记录打在子表上(fake_linked_write: FakeLinkedWrite) -> None:
    result = run(
        fake_linked_write.delete_linked_record("费用明细", "recF1", role=ROLE, table_id="tbl_plan")
    )

    assert result == {"record_id": "recF1"}
    assert fake_linked_write.calls[-1] == ("DELETE", "https://example.invalid/tbl_fee/records/recF1")


def test_删除也认父表_漏传就报不存在字段(fake_linked_write: FakeLinkedWrite) -> None:
    """删除是**不可逆**的，写错表代价最大，所以这里的表解析同样按父表走、同样会当场报错。"""
    with pytest.raises(CustomException, match="不存在字段"):
        run(fake_linked_write.delete_linked_record("费用明细", "recF1", role=ROLE))
