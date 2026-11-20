"""行级数据权限：非超管只看「商机负责人 = 自己」的记录。

这条权限的失效方式全是**静默**的：少 AND 一层就在搜索时漏别人的单，详情不判就能靠
record_id 越权，卡片不过滤就出现「卡片 1958 条、点进去 3 条」。界面上都不会报错，
所以每一种都得由用例钉住。另一个方向也要钉：**匹配不到姓名必须报错而不是放行**——
fail-open 的后果是客服看到全量，而那种「怎么都能看到」不会有人当成 bug 来报。

桩在 ``_contact_directory`` / ``_search_page`` 这一层，不打飞书、不碰真实数据。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from typing import Any, cast

import pytest
from redis.asyncio.client import Redis

from app.core.exceptions import CustomException
from app.plugin.module_feishu.service import (
    FeishuBitableService,
    _combine_filter,
    _metas_from_raw,
)
from app.plugin.module_feishu.workbench import WORKBENCHES, get_workbench

ROLE = "wd_customer_service"
OWNER_FIELD = "商机负责人"
# 国内客服配置的搜索字段之一（``customer_service`` 那套的字段名不通用，故取威达的）
SEARCH_FIELD = "客户 REF NO."
# 没有配 owner_field 的工作台（富士康那套），用来验「不填 = 不限制」
ROLE_PLAIN = "customer_service"

CONTACT: list[dict[str, str]] = [
    {"open_id": "ou_fairy", "name": "Fairy Li"},
    {"open_id": "ou_taolee", "name": "陶莉莉"},
]


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@dataclass
class FakeUser:
    """``auth.user``（UserModel）里与本权限相关的两个属性。"""

    name: str = ""
    is_superuser: bool = False


class FakeService(FeishuBitableService):
    """截住通讯录与 ``records/search``，记下每次搜索收到的 filter 与字段。"""

    def __init__(self, role: str = ROLE, contact: list[dict[str, str]] | None = None) -> None:
        super().__init__(cast("Redis", None), role)
        self.contact = list(CONTACT if contact is None else contact)
        # (filter, 投影列)
        self.searches: list[dict[str, Any] | None] = []
        self.search_columns: list[list[str] | None] = []
        # 每次 ``records/search`` 返回的记录（默认一条）
        self.records: list[dict[str, Any]] = [
            {"record_id": "rec_1", "fields": {OWNER_FIELD: [{"id": "ou_fairy", "name": "Fairy Li"}]}}
        ]

    async def _contact_directory(self) -> list[dict[str, str]]:
        return self.contact

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        # ``list_workbench`` 要字段类型来投影与判关联，给最小可用的一组
        return [
            {"field_id": "f1", "field_name": OWNER_FIELD, "type": 11, "ui_type": "User"},
            {"field_id": "f2", "field_name": "商机编号", "type": 1005, "ui_type": "AutoNumber"},
            # 关键字搜索要用它，否则 ``_build_filter`` 会以「没有可搜索文本字段」直接报错
            {"field_id": "f3", "field_name": SEARCH_FIELD, "type": 1, "ui_type": "Text"},
        ]

    async def get_fields(self) -> list[Any]:
        # 绕开 Redis：本用例只关心 filter，不关心字段缓存
        return _metas_from_raw(await self._raw_field_items())

    async def _endpoint(self, path: str) -> str:
        return f"https://example.invalid{path}"

    async def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        # 详情走 batch_get 时不再打飞书；返回和真实响应同形的一层
        return {"code": 0, "data": {"records": list(self.records)}}

    async def _field_types(self) -> dict[str, int]:
        return {OWNER_FIELD: 11, "商机编号": 1005, SEARCH_FIELD: 1}

    async def _fill_link_texts(self, records: list[dict[str, Any]], link_targets: dict[str, str]) -> None:
        return None

    async def _fill_single_status(self, records: list[dict[str, Any]], order_table: str) -> None:
        return None

    async def _search_page(
        self,
        filter_obj: dict[str, Any] | None,
        page_token: str | None,
        page_size: int,
        field_names: list[str] | None = None,
        sort: list[dict[str, Any]] | None = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        self.searches.append(filter_obj)
        self.search_columns.append(field_names)
        return {"items": list(self.records), "total": len(self.records), "has_more": False, "page_token": None}

    async def _token(self) -> str:
        return "fake-token"


def _as_cs(svc: FakeService, owner_field: str = OWNER_FIELD) -> FakeService:
    """把桩的配置换成「国内客服那套」。

    ``wd_customer_service`` 没有勾选框，待办计数走不到那条路，所以验待办时借一个带勾选框
    的角色（``wd_domestic_ops``），再把 owner_field 装上去——验的是代码路径，不是配置。
    """
    assert svc._cfg is not None
    svc._cfg = replace(svc._cfg, owner_field=owner_field)
    return svc


# ============================ 配置 ============================


def test_只有国内客服配了负责人字段() -> None:
    """其余工作台留空 = 不限制：这是默认行为，不填就不该有任何过滤。"""
    assert get_workbench(ROLE).owner_field == OWNER_FIELD
    for key, cfg in WORKBENCHES.items():
        if key != ROLE:
            assert cfg.owner_field == "", f"{key} 不该被行级过滤影响"


def test_负责人字段默认空是数据类默认值() -> None:
    """没写 owner_field 的配置必须仍然能构造出来，否则一加字段整套工作台全挂。"""
    assert replace(get_workbench(ROLE), owner_field="").owner_field == ""


# ============================ _owner_filter ============================


@pytest.mark.parametrize(
    ("user", "why"),
    [
        (None, "内部调用/脚本没带用户，不该被拦"),
        (FakeUser(name="Fairy Li", is_superuser=True), "超管看全部"),
        (FakeUser(name="查无此人", is_superuser=True), "超管连姓名匹配都跳过"),
    ],
)
def test_这些情况不加过滤(user: Any, why: str) -> None:
    assert run(FakeService()._owner_filter(user)) is None, why


def test_没配负责人字段的工作台不加过滤() -> None:
    assert run(FakeService(role=ROLE_PLAIN)._owner_filter(FakeUser(name="Fairy Li"))) is None


def test_姓名命中通讯录时过滤自己那一个_open_id() -> None:
    cond = run(FakeService()._owner_filter(FakeUser(name="陶莉莉")))

    assert cond == {"field_name": OWNER_FIELD, "operator": "is", "value": ["ou_taolee"]}


def test_姓名匹配是精确的且按_unicode_原样比() -> None:
    """「陶莉」不是「陶莉莉」：子串匹配会把同姓的人放进来，等于没过滤。"""
    with pytest.raises(CustomException):
        run(FakeService()._owner_filter(FakeUser(name="陶莉")))


def test_姓名前后空格不算数() -> None:
    """账号里手填的名字很容易带尾巴空格，为此把客服拦在门外没有道理。"""
    cond = run(FakeService()._owner_filter(FakeUser(name="  Fairy Li  ")))

    assert cond is not None and cond["value"] == ["ou_fairy"]


def test_通讯录里没有同名成员时_报错而不是放行() -> None:
    """fail-closed：宁可客服看不到（会来问），也不能悄悄放开成全量（没人会报）。"""
    with pytest.raises(CustomException) as ei:
        run(FakeService()._owner_filter(FakeUser(name="改名了")))

    assert "改名了" in str(ei.value.msg)


def test_账号没有昵称时也报错() -> None:
    """没有 name 就没法认人，此时放行等于把整个 base 交出去。"""
    for name in ("", "   ", None):
        with pytest.raises(CustomException):
            run(FakeService()._owner_filter(FakeUser(name=cast("str", name))))


# ============================ _combine_filter ============================


LEAF_A = {"field_name": "a", "operator": "is", "value": ["1"]}
LEAF_B = {"field_name": "b", "operator": "is", "value": ["2"]}
WRAPPED_A = {"conjunction": "and", "conditions": [LEAF_A]}
WRAPPED_B = {"conjunction": "and", "conditions": [LEAF_B]}


def test_全空返回_None() -> None:
    assert _combine_filter(None, None) is None


def test_只有一个条件也要包一层_conjunction() -> None:
    """飞书对**顶层裸条件**报 ``filter.conjunction is required``（code 99992402）。

    这条踩过：客服没有搜索词时列表只有一个 owner 条件，裸传过去整个工作台 400——
    而带搜索词时反而正常，症状是「不搜索就报错、一搜索就好」，极难对上号。
    """
    assert _combine_filter(LEAF_A) == WRAPPED_A
    assert _combine_filter(None, LEAF_B) == WRAPPED_B


def test_已经是合法形态的过滤器原样保留() -> None:
    """`_build_filter` 返回的就是包好的形态，不该被再套一层。"""
    assert _combine_filter(WRAPPED_A) == WRAPPED_A


def test_多个条件走_children_且每项都带_conjunction() -> None:
    """飞书两处硬要求：子过滤器不能进 ``conditions``，且 ``children`` 里的每一项
    **本身也得**是 ``{conjunction, conditions}``——裸条件放进去同样 400（实测过）。"""
    merged = _combine_filter(LEAF_A, LEAF_B)

    assert merged == {"conjunction": "and", "children": [WRAPPED_A, WRAPPED_B]}
    for child in merged["children"]:  # type: ignore[union-attr]
        assert "conjunction" in child


# ============================ 织进四个入口 ============================


def test_列表把负责人条件与关键字_AND_而不是二选一() -> None:
    """少 AND 一层，客服一搜索就能搜出别人的商机。"""
    svc = FakeService()

    run(svc.list_workbench(role=ROLE, search="ABC", current_user=FakeUser(name="Fairy Li")))

    sent = svc.searches[0]
    assert sent is not None and sent["conjunction"] == "and"
    # 关键字那一支还在，负责人那一支也在——少了任何一支都是越权或搜不到
    flat = str(sent["children"])
    assert "ou_fairy" in flat and "ABC" in flat


def test_超管走列表时原来的过滤原样不变() -> None:
    """超管不该因为这次改动多出任何条件（原来什么样就是什么样）。"""
    plain = FakeService()
    run(plain.list_workbench(role=ROLE, search="ABC", current_user=FakeUser(name="Fairy Li", is_superuser=True)))

    assert "ou_fairy" not in str(plain.searches[0])


def test_列表不带用户时不过滤() -> None:
    """没带 current_user（内部调用）时行为与改动前一致。"""
    svc = FakeService()

    run(svc.list_workbench(role=ROLE, search="ABC"))

    assert "ou_fairy" not in str(svc.searches[0])


def test_不搜索也不筛待办时_带上用户仍然发出合法_filter() -> None:
    """这是**默认视图**（刚打开工作台就是这一屏）：只有 owner 一个条件。

    裸条件顶层报 ``filter.conjunction is required``，所以这一屏当初会整个 400——
    而只要随手搜一下反而好了。默认视图反而是最常走的路径，必须钉住。
    """
    svc = FakeService()

    run(svc.list_workbench(role=ROLE, current_user=FakeUser(name="Fairy Li")))

    sent = svc.searches[0]
    assert sent is not None and sent.get("conjunction") == "and"
    assert "ou_fairy" in str(sent)


def test_总数卡片按负责人过滤() -> None:
    """卡片数字不过滤的话，会出现「卡片写 1958 条、点进去只有 3 条」。"""
    svc = FakeService()

    run(svc.count_total(current_user=FakeUser(name="Fairy Li")))

    assert svc.searches[0] == {"conjunction": "and", "conditions": [{"field_name": OWNER_FIELD, "operator": "is", "value": ["ou_fairy"]}]}


def test_待办卡片每一项都按负责人过滤() -> None:
    """待办分项卡片与总数卡片同理：少过滤一项就是数字与明细对不上。"""
    svc = _as_cs(FakeService(role="wd_domestic_ops"))

    run(svc.count_todo_breakdown("wd_domestic_ops", current_user=FakeUser(name="Fairy Li")))

    assert svc.searches, "至少该发一次计数请求"
    for sent in svc.searches:
        assert "ou_fairy" in str(sent), "待办计数漏了行级过滤"


def test_待办卡片_AND_了勾选条件而不是被覆盖() -> None:
    """负责人条件与勾选框条件必须同时生效：被覆盖掉就是「待办数 = 我的全部记录」。"""
    svc = _as_cs(FakeService(role="wd_domestic_ops"))

    run(svc.count_todo_breakdown("wd_domestic_ops", current_user=FakeUser(name="Fairy Li")))

    sent = svc.searches[0]
    assert sent is not None and sent["conjunction"] == "and"
    assert len(sent["children"]) == 2


def test_待办卡片不带用户时不过滤() -> None:
    svc = _as_cs(FakeService(role="wd_domestic_ops"))

    run(svc.count_todo_breakdown("wd_domestic_ops"))

    for sent in svc.searches:
        assert "ou_fairy" not in str(sent)


# ============================ 详情越权兜底 ============================


def _detail_service(owner_ids: list[str]) -> FakeService:
    svc = _as_cs(FakeService(role="wd_domestic_ops"))
    svc.records = [
        {
            "record_id": "rec_1",
            "fields": {OWNER_FIELD: [{"id": oid, "name": "x"} for oid in owner_ids]},
        }
    ]
    return svc


def test_详情是自己的人就放行() -> None:
    svc = _detail_service(["ou_fairy"])

    record = run(svc.get_workbench_record("rec_1", role="wd_domestic_ops", current_user=FakeUser(name="Fairy Li")))

    assert record["record_id"] == "rec_1"


def test_详情不是自己的人就_403() -> None:
    """列表被滤掉不代表详情进不来：record_id 能从分享链接/旧页面拿到，接口这层必须自己判。"""
    svc = _detail_service(["ou_taolee"])

    with pytest.raises(CustomException) as ei:
        run(svc.get_workbench_record("rec_1", role="wd_domestic_ops", current_user=FakeUser(name="Fairy Li")))

    assert ei.value.status_code == 403


def test_详情里负责人为空时也_403() -> None:
    """没填负责人的记录不属于任何人，非超管不该看到。"""
    svc = _detail_service([])

    with pytest.raises(CustomException) as ei:
        run(svc.get_workbench_record("rec_1", role="wd_domestic_ops", current_user=FakeUser(name="Fairy Li")))

    assert ei.value.status_code == 403


def test_超管看任何详情都不拦() -> None:
    svc = _detail_service(["ou_taolee"])

    record = run(
        svc.get_workbench_record("rec_1", role="wd_domestic_ops", current_user=FakeUser(name="Fairy Li", is_superuser=True))
    )

    assert record["record_id"] == "rec_1"
