"""详情里的「另一张表」页（`RelatedTable` / `/config` 的 `related_blocks`）。

两条规则没有编译期或运行期的护栏，写错了都只是**静默少东西**：

1. 反查的筛选（`_points_back_at`）：少认一种 id 形态或少了 id 集合的积，某些订单的 iFreight
   页就空了——界面上和「这张单还没生成」长得一模一样；
2. 页内字段的解析（`_related_blocks`）：名字对不上时跳过是有意的（字段改名不该让整页打不开），
   但因此谁也不会报错，所以「跳过」和「照配置顺序排」都得由用例钉住。

桩在 ``_raw_field_items`` / ``_table_fields`` 这一层，不打飞书。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, cast

from redis.asyncio.client import Redis

from app.plugin.module_feishu.service import (
    FeishuBitableService,
    _metas_from_raw,
    _points_back_at,
    _related_page,
)
from app.plugin.module_feishu.workbench import (
    WD_IFREIGHT_TABLE,
    WD_ORDER_TABLE,
    RelatedTable,
    get_workbench,
)

ROLE = "wd_domestic_ops"
BACK_REF = "所属订单"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _field(field_id: str, name: str, ftype: int, **extra: Any) -> dict[str, Any]:
    return {"field_id": field_id, "field_name": name, "type": ftype, "ui_type": "", **extra}


def _rec(record_id: str, **link_ids: Any) -> dict[str, Any]:
    return {"record_id": record_id, "link_ids": link_ids}


# ============================ 反查的筛选 ============================


def test_单条回指命中() -> None:
    assert _points_back_at(_rec("r1", **{BACK_REF: ["o1"]}), BACK_REF, {"o1"}) is True


def test_一张订单挂两条时两条都命中() -> None:
    """飞书里一张订单挂两条 iFreight 是常态（实测 16 张里 2 张如此）。挑「第一条」摊平会
    静默丢掉另一条——那一条的业务值还往往不同（一条同步失败、一条成功）。"""
    rows = [_rec("r1", **{BACK_REF: ["o1"]}), _rec("r2", **{BACK_REF: ["o1"]})]

    assert sum(_points_back_at(r, BACK_REF, {"o1"}) for r in rows) == 2


def test_回指别人的记录不命中() -> None:
    assert _points_back_at(_rec("r1", **{BACK_REF: ["o2"]}), BACK_REF, {"o1"}) is False


def test_本表多条记录一起查时按集合判() -> None:
    """详情一次只问一条，但接口收的是 id 列表：这里传了两条订单，指着其中任一条都算命中。"""
    assert _points_back_at(_rec("r1", **{BACK_REF: ["o7"]}), BACK_REF, {"o1", "o7"}) is True


def test_没挂任何订单的孤儿行不命中也不报错() -> None:
    """从表里有没挂订单的行（实测 20 条里 2 条），它们不属于任何详情页。"""
    assert _points_back_at(_rec("r1"), BACK_REF, {"o1"}) is False
    assert _points_back_at(_rec("r1", **{BACK_REF: []}), BACK_REF, {"o1"}) is False
    assert _points_back_at(_rec("r1", **{BACK_REF: None}), BACK_REF, {"o1"}) is False


def test_回指是别的字段时不算() -> None:
    """从表上挂着别的关联字段（商机/订舱代理），它们指的不是这张订单。"""
    assert _points_back_at(_rec("r1", 商机=["o1"]), BACK_REF, {"o1"}) is False


def test_回指的_id_不吃形态的亏() -> None:
    """两种形态都要认（数字 id、单值没裹成列表），而且单值那种**不能**按字符比：
    `wanted={"o"}` 若把 `"o1"` 认成命中，多出来的就不是少几条而是**串页**了。"""
    assert _points_back_at(_rec("r1", **{BACK_REF: [123]}), BACK_REF, {"123"}) is True
    assert _points_back_at(_rec("r1", **{BACK_REF: "o1"}), BACK_REF, {"o1"}) is True
    assert _points_back_at(_rec("r1", **{BACK_REF: "o1"}), BACK_REF, {"o"}) is False


# ============================ 页内字段的解析 ============================


class FakeRelated(FeishuBitableService):
    """两张表的字段都由用例给：本表的（`_raw_field_items`）与从表的（`_table_fields`）。"""

    def __init__(self, own: list[dict[str, Any]], other: list[dict[str, Any]]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.own = own
        self.other = other

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        return list(self.own)

    async def _table_fields(self, table_id: str) -> list[Any]:
        assert table_id == WD_IFREIGHT_TABLE, "从表只该按配置里的 table_id 去问"
        return _metas_from_raw(self.other)


def _config(own: list[dict[str, Any]], other: list[dict[str, Any]]) -> Any:
    return run(FakeRelated(own, other).get_workbench_config(ROLE))


def test_反查页按配置里的字段清单出_不漏不多() -> None:
    own = [_field("a", "订单编号", 1005), _field("b", "国内操作", 3)]
    other = [_field("x", "操作编号", 1005), _field("y", "同步状态", 3), _field("z", "备注", 1)]

    cfg = _config(own, other)

    # 三条反查页指向同一张 iFreight 表（key 互不相同），各铺各的列
    assert [b.key for b in cfg.related_blocks] == ["iFreight", "复制提单编组", "投保编组"]
    block = cfg.related_blocks[0]
    assert block.label == "iFreight 数据"
    # 配置里点名了的那几个按**配置的顺序**出，没点名的（备注）不出现
    assert [m.name for m in block.fields][:2] == ["操作编号", "同步状态"]
    assert "备注" not in [m.name for m in block.fields]


def test_反查页的字段可编辑性与从表一致() -> None:
    """反查页**就地改**要能写回从表（要求见「ifreight数据表的tag也需要是可修改的信息」）：
    所以服务端不再把这一页的字段一律压成只读。飞书侧本来就只读的类型（这里是自动编号
    1005）仍然只读——那由从表自己的字段定义说了算，不是本系统另加的一道锁。"""
    other = [_field("x", "操作编号", 1005), _field("y", "运输条款", 3)]

    block = _config([_field("a", "订单编号", 1005)], other).related_blocks[0]

    by_name = {m.name: m for m in block.fields}
    assert by_name["运输条款"].editable and not by_name["运输条款"].read_only
    assert not by_name["操作编号"].editable and by_name["操作编号"].read_only


def test_反查页的字段都不当记录的主字段() -> None:
    """从表的主字段摆在这一页里也不该被当成「记录的名字」——`linkedTitle` 之类靠 is_primary
    取标题，而这一行是别人，不是当前这条记录。"""
    other = [_field("x", "操作编号", 1005, is_primary=True), _field("y", "运输条款", 3)]

    block = _config([_field("a", "订单编号", 1005)], other).related_blocks[0]

    assert all(not m.is_primary for m in block.fields)


def test_从表页不上索引页() -> None:
    """从表只在详情里：索引页一行是本表的一条记录，从表那几行既不是它的字段、一格也放不下
    N 条。列表接口因此不带 `related`（那一列已经撤掉），从表一律点到详情 Tab 才取。"""
    cfg = _config([_field("a", "订单编号", 1005)], [_field("x", "操作编号", 1005)])

    assert not hasattr(cfg.related_blocks[0], "in_index")


def test_字段名对不上就少一格而不是整页打不开() -> None:
    """从表改过名时跳过着走：配置里 26 个名字，表里只剩 1 个也得能出这一页。"""
    cfg = _config([_field("a", "订单编号", 1005)], [_field("x", "操作编号", 1005)])

    assert [m.name for m in cfg.related_blocks[0].fields] == ["操作编号"]


def test_反查页的字段不进自定义列的池子() -> None:
    """`fields` 是索引页「自定义列」的可选池。从表那几列混进来就等于能挑成索引页的列，
    而它们是 1:N 的——一张订单挂两条时，那一格该显示哪条？"""
    own = [_field("a", "订单编号", 1005)]
    other = [_field("x", "操作编号", 1005)]

    cfg = _config(own, other)

    assert [m.name for m in cfg.fields] == ["订单编号"]
    assert "iFreight·操作编号" not in [m.name for m in cfg.fields]


def test_没配反查页的工作台不受影响() -> None:
    """只有威达国内操作配了 related：别的工作台的 config 里这一项必须是空的
    （前端凭它决定要不要多摆一页）。"""
    svc = FakeRelated([_field("a", "商机信息", 20)], [])

    cfg = run(svc.get_workbench_config("wd_customer_service"))

    assert cfg.related_blocks == []


def test_配置里的表_id_与回指字段就是那两个() -> None:
    """表 id 或回指字段名写错**不会**报错，只会查不到东西（或查到别人的），
    表现为「这一页永远空的」——和「这张单还没生成 iFreight」长得一样，所以钉住它们。

    三条反查页指向**同一张** iFreight 表、同一个回指字段，但 key 必须互不相同：
    `_related_page` 拿 key 认页（写回目标从它取表 id），key 撞名会让「复制提单编组」里的
    一次改动写到别处去——界面上只是「改完没生效」，没有任何报错。
    """
    rels: tuple[RelatedTable, ...] = get_workbench(ROLE).related

    assert [(r.key, r.table_id, r.back_ref_field) for r in rels] == [
        ("iFreight", WD_IFREIGHT_TABLE, BACK_REF),
        ("复制提单编组", WD_IFREIGHT_TABLE, BACK_REF),
        ("投保编组", WD_IFREIGHT_TABLE, BACK_REF),
    ]
    assert len({r.key for r in rels}) == len(rels), "反查页的 key 必须互不相同"
    assert get_workbench(ROLE).table_id == WD_ORDER_TABLE  # 索引页的行仍来自订单表


def test_复制提单编组与投保编组各自成页() -> None:
    """飞书 iFreight 详情面板里的「复制提单编组」「投保编组」各占一个子 Tab。

    这 7 个名字是逐字照飞书 ``field_name`` 抄的，抄错**不会报错**：只是这一页少一格，
    界面上和「这个字段本来就没有」长得一模一样——所以钉住。字段类型都是可编辑的
    （复选框/文本/单选/数字），改完经 `update_linked_record` 回填 iFreight 表。
    """
    by_key = {r.key: r for r in get_workbench(ROLE).related}

    assert by_key["复制提单编组"].fields == ("分单号", "复制提单", "复制提单号")
    assert by_key["投保编组"].fields == ("分单号", "投保", "投保条款", "币制", "报关金额", "启运地")

    # 编组字段**只**出现在各自那一页，iFreight 主表页不再重复铺一遍（否则同一格两个入口）
    groups = ("复制提单", "复制提单号", "投保", "投保条款", "币制", "报关金额", "启运地")
    assert not (set(by_key["iFreight"].fields) & set(groups))
    # 摘要列那 6 个仍要在 iFreight 主表页（表格折叠时靠它们显示）
    for name in ("操作编号", "同步状态", "主单号", "分单号", "运输方式", "发货人名称"):
        assert name in by_key["iFreight"].fields


def test_订单详情按_Stage_节点拆了_Tab() -> None:
    """订单表那三个 Stage 字段（国内客服/操作/航线）的节点定义飞书读不出来，分 Tab 的字段
    归属是照业务方给的截图抄的。这份配置最容易出两种错，都**不报错**：
    ① 忘了留兜底组 → 剩下三十来个字段在详情里凭空消失；② 把 Stage 字段本身写进某个组 →
    多一格永远空白的只读框（`_detail_groups` 虽会滤掉，配置里写上仍是错的）。"""
    sections = get_workbench(ROLE).detail_sections

    assert sections, "国内操作详情现在应当分 Tab"
    assert [s.label for s in sections][-1] == "其余字段"
    assert [s.label for s in sections].count("其余字段") == 1
    empty = [s.label for s in sections if not s.fields]
    assert empty == ["其余字段"], f"只有兜底组可以留空，实际留空的：{empty}"
    # Stage 字段不该被任何一组点名（它们的值根本读不出来）
    named = {f for s in sections for f in s.fields}
    assert not named & {"国内客服", "操作", "航线"}


# ============================ 详情里那一页的取数（`_related_records`）============================


class FakeIndex(FeishuBitableService):
    """反查取数那一步的桩：`_search_page` 只回从表的行，`_table_fields` 只回从表字段。"""

    def __init__(self, downstream: list[dict[str, Any]], other: list[dict[str, Any]]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.downstream = downstream
        self.other = other

    async def _table_fields(self, table_id: str) -> list[Any]:
        assert table_id == WD_IFREIGHT_TABLE
        return _metas_from_raw(self.other)

    async def _search_page(  # type: ignore[override]
        self,
        filter_obj: Any,
        page_token: Any,
        page_size: int,
        field_names: Any = None,
        sort: Any = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        cols = tuple(field_names or ())
        return {
            "items": [
                {"record_id": r["record_id"], "fields": {c: (r.get("fields") or {}).get(c) for c in cols}}
                for r in self.downstream
            ],
            "has_more": False,
        }


def test_只有_回指列_和这一页点名的字段被拉回来() -> None:
    """从表 93 个字段，不裁的话每行都要等飞书把用不上的公式/查找也算一遍。"""
    other = [
        _field("x", "操作编号", 1),
        _field("y", "同步状态", 3),
        _field("z", BACK_REF, 18, property={"table_id": WD_ORDER_TABLE}),
        _field("w", "不在这页里的字段", 1),
    ]
    svc = FakeIndex([], other)

    metas, records = run(svc._related_records(get_workbench(ROLE).related[0], {"o1"}))

    assert "不在这页里的字段" not in [m.name for m in metas]
    assert records == []


def test_回指字段被改名时不把整张从表当成结果() -> None:
    """认不出下游是谁时必须空手而归：把整张从表返回的话，每条订单都会显示**别人**的 iFreight
    ——这比「这一页空了」危险得多。"""
    other = [_field("x", "操作编号", 1)]  # 没有 `所属订单`
    svc = FakeIndex([{"record_id": "r1", "fields": {}}], other)

    metas, records = run(svc._related_records(get_workbench(ROLE).related[0], {"o1"}))

    assert (metas, records) == ([], [])


def _row(record_id: str, text: str, *parents: str) -> dict[str, Any]:
    """从表的**原始**记录（`fields` 里放飞书那种 ``[{"link_record_ids": [...]}]`` 的关联形态，
    `_normalize_record` 就是从这儿抽 link_ids 的）。"""
    return {
        "record_id": record_id,
        "fields": {"操作编号": text, BACK_REF: [{"link_record_ids": list(parents)}] if parents else None},
    }


def test_详情这一页一次给出指着我那几条() -> None:
    """一张订单挂两条 iFreight 是常态（实测 16 张里 2 张）。这一页是**一批**：
    只回一条就是静默丢数据，而两条的业务值还往往不同（一条同步失败、一条成功）。"""
    other = [
        _field("x", "操作编号", 1),
        _field("y", "同步状态", 3),
        _field("z", BACK_REF, 18, property={"table_id": WD_ORDER_TABLE}),
    ]
    svc = FakeIndex([_row("r1", "A", "o1"), _row("r2", "B", "o1"), _row("r3", "别人的", "o9")], other)

    metas, records = run(svc._related_records(get_workbench(ROLE).related[0], {"o1"}))

    assert [r["record_id"] for r in records] == ["r1", "r2"]
    # 回指列不当作这一页的渲染字段：筛的时候用它（`link_ids` 也照抽），但摆出来就是回声
    # ——这一页正长在它所指向的那条记录的详情里
    assert BACK_REF not in [m.name for m in metas]
    assert BACK_REF in (records[0].get("link_ids") or {}), "筛完了还是得看得出这条指着谁"


# ============================ 反查页就地改（写回从表）============================


class FakeWrite(FeishuBitableService):
    """写路径的桩：只记「准备写哪张表 / 真写到哪张表」，不打飞书。"""

    def __init__(self) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.seen: list[tuple[str, str]] = []

    async def _prepare_write(self, fields: dict[str, Any], table_id: str = "") -> dict[str, Any]:
        self.seen.append(("prepare", table_id))
        return dict(fields)

    async def _batch_update(self, updates: list[dict[str, Any]], action: str, table_id: str = "") -> int:
        self.seen.append(("write", table_id))
        return len(updates)

    async def _link_target_table(self, field_name: str, table_id: str = "") -> str:
        raise AssertionError("反查页在本表上没有关联字段，不该去问「它指向哪张表」")


def test_反查页就地改写到从表() -> None:
    """`iFreight` 不是本表的字段，写之前得先认出它是**反查页**：表 id 从配置取，
    否则 `_link_target_table` 会报「飞书表中不存在字段「iFreight」」——界面表现为
    「改不动，弹一句莫名其妙的报错」。"""
    svc = FakeWrite()

    run(svc.update_linked_record("iFreight", "rec1", {"运输条款": "预付"}, ROLE))

    assert svc.seen == [("prepare", WD_IFREIGHT_TABLE), ("write", WD_IFREIGHT_TABLE)]


def test_认得出哪些名字是反查页() -> None:
    assert _related_page(ROLE, "iFreight") is not None
    assert _related_page(ROLE, "运输条款") is None  # 从表里的字段名，不是反查页的 key


def test_三条反查页的写回都落到_iFreight_表() -> None:
    """两个编组页也和主表页一样，改一格直接写回 iFreight——它们不是只读展示页。"""
    for key, field_name in (
        ("iFreight", "运输条款"),
        ("复制提单编组", "复制提单"),
        ("投保编组", "投保条款"),
    ):
        svc = FakeWrite()

        run(svc.update_linked_record(key, "rec1", {field_name: "预付"}, ROLE))

        assert svc.seen == [("prepare", WD_IFREIGHT_TABLE), ("write", WD_IFREIGHT_TABLE)], key


# ============================ 三页合并成一次取数（`related_blocks_records`）============================
#
# 国内操作那三页指向**同一张从表**、回指字段也同一个，逐页取数是各扫一遍全表——实测三个 Tab
# 合计 4.4 秒，其中两次纯属重复。合并取数按 `(表 id, 回指列)` 分组、一组只扫一次，扫完再按页
# 裁列。这里钉两件事：**只扫一遍**（省下来的就是那两次），以及**裁列与逐页路径逐字一致**
# （裁错只会让某一页少几列/多几列，界面上看不出来）。


class FakeSweep(FakeIndex):
    """在 `FakeIndex` 上多数一个「从表被扫了几遍」与每次扫的投影列。"""

    def __init__(self, downstream: list[dict[str, Any]], other: list[dict[str, Any]]) -> None:
        super().__init__(downstream, other)
        self.scans = 0
        self.projected: list[tuple[str, ...]] = []

    async def _search_page(  # type: ignore[override]
        self,
        filter_obj: Any,
        page_token: Any,
        page_size: int,
        field_names: Any = None,
        sort: Any = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        if page_token is None:  # 只数第一页，翻页不算「又扫了一遍」
            self.scans += 1
            self.projected.append(tuple(field_names or ()))
        return await super()._search_page(filter_obj, page_token, page_size, field_names, sort, table_id)


def _sweep_fields() -> list[dict[str, Any]]:
    """从表字段定义：回指列 + 三页各自点名的列（各页取前 3 个就够区分是谁的列）。"""
    fields = [_field("z", BACK_REF, 18, property={"table_id": WD_ORDER_TABLE})]
    seen = {BACK_REF}
    for rel in get_workbench(ROLE).related:
        for name in rel.fields[:3]:
            if name not in seen:
                seen.add(name)
                fields.append(_field(f"f{len(fields)}", name, 1))
    return fields


def _sweep_row(record_id: str, *parents: str) -> dict[str, Any]:
    """从表的一行：三页点名的列都填上值（两边路径不一致时逐字比较才看得出来）+ 回指。"""
    values: dict[str, Any] = {f["field_name"]: f"{f['field_name']}@{record_id}" for f in _sweep_fields()}
    values[BACK_REF] = [{"link_record_ids": list(parents)}] if parents else None
    return {"record_id": record_id, "fields": values}


def _page_fields(key: str) -> set[str]:
    return {r.key: set(r.fields) for r in get_workbench(ROLE).related}[key]


def test_三条反查页只扫一遍从表() -> None:
    """这是这个接口存在的**全部理由**：三页共用一张表，合并前是「各扫一遍」= 三倍往返。"""
    svc = FakeSweep([_sweep_row("r1", "o1"), _sweep_row("r2", "o1"), _sweep_row("r3", "o9")], _sweep_fields())

    merged = run(svc.related_blocks_records(["o1"], ROLE))

    assert svc.scans == 1, f"三页指向同一张表，应当只扫一遍，实际 {svc.scans} 遍"
    assert set(merged) == {"iFreight", "复制提单编组", "投保编组"}
    for key, page in merged.items():
        assert page["table_id"] == WD_IFREIGHT_TABLE, key
        # 指回 o1 的两条都要有：一张订单挂两条是常态，只给一条是静默丢数据
        assert [r["record_id"] for r in page["records"]] == ["r1", "r2"], key


def test_一次扫描的投影取三页字段的并集() -> None:
    """扫描只做一次，投影就得覆盖三页的**并集**：漏掉哪页点名的列，那一页的格子会凭空空掉
    ——而它和「这个字段本来就没值」长得一模一样。"""
    svc = FakeSweep([], _sweep_fields())

    run(svc.related_blocks_records(["o1"], ROLE))

    projected = set(svc.projected[0])
    assert BACK_REF in projected, "回指列是筛选的依据，投影里不能少"
    for rel in get_workbench(ROLE).related:
        for name in rel.fields[:3]:
            if name in {f["field_name"] for f in _sweep_fields()}:
                assert name in projected, f"{rel.key} 点名的「{name}」没进投影"


def test_每页只回自己点名的列() -> None:
    """合并扫描带了并集回来，但**下发**仍按各页裁：三页是三个子 Tab，把 26 列全铺到只有 3 格
    的编组页上，前端会渲出一堆它没打算显示的格。"""
    other = _sweep_fields()
    svc = FakeSweep([_sweep_row("r1", "o1")], other)

    merged = run(svc.related_blocks_records(["o1"], ROLE))
    present = {f["field_name"] for f in other}

    for key, page in merged.items():
        names = [m["name"] for m in page["fields"]]
        assert set(names) == _page_fields(key) & present, key
        # 回指列是回声（这一页正长在它所指向的那条记录的详情里），不进字段清单……
        assert BACK_REF not in names, key
        # ……但筛选的结果要留着：前端靠它认这条指着谁
        assert BACK_REF in page["records"][0]["link_ids"], key


def test_每页的记录里留着回指列_与逐页路径同形() -> None:
    """两条路径交给前端的记录形状必须**一样**（逐页路径只把回指列从字段清单里摘掉、值还留着）。
    形状不一致时前端得写两套解析，且差异只在某一路径下现形。"""
    svc = FakeSweep([_sweep_row("r1", "o1")], _sweep_fields())

    merged = run(svc.related_blocks_records(["o1"], ROLE))

    record = merged["投保编组"]["records"][0]
    assert set(record) == {"record_id", "fields", "link_ids", "user_refs"}
    assert BACK_REF in record["fields"], "回指列的值也得留着（逐页路径就是这样的）"


def test_合并取数与逐页取数逐字一致() -> None:
    """提速不能靠少给东西。三条路径（合并 / 逐页）的 `{table_id, fields, records}` 必须逐字相同
    ——少一列或值形态变了，界面上都只是「这一格空着」."""
    other = _sweep_fields()
    rows = [_sweep_row("r1", "o1"), _sweep_row("r2", "o1"), _sweep_row("r3", "o9")]
    keys = [r.key for r in get_workbench(ROLE).related]

    per_page = {
        key: run(FakeSweep(list(rows), other).linked_records(key, ["o1"], role=ROLE)) for key in keys
    }
    merged = run(FakeSweep(list(rows), other).related_blocks_records(["o1"], ROLE))

    for key in keys:
        blob = json.dumps(per_page[key], sort_keys=True, default=str)
        assert blob == json.dumps(merged[key], sort_keys=True, default=str), f"{key} 两条路径不一致"


def test_没配反查页的工作台返回空字典() -> None:
    """调用方不必先查 `/config` 才知道要不要发这个请求。"""
    svc = FakeSweep([], [])

    assert run(svc.related_blocks_records(["o1"], "wd_customer_service")) == {}


def test_没给记录_id_时也把各页字段定义给出来() -> None:
    """真到这一步（调用方传了空 id 列表）也该画出空页而不是一片「加载中」——字段清单在
    `/config` 里其实已经有了，但前端拿到这份就能无差别地渲染。"""
    svc = FakeSweep([_sweep_row("r1", "o1")], _sweep_fields())

    merged = run(svc.related_blocks_records([], ROLE))

    assert svc.scans == 0, "没有要查的记录就不该去扫表"
    assert all(page["records"] == [] for page in merged.values())
    assert all(page["fields"] for page in merged.values())
