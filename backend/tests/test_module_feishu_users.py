"""人员字段（飞书 type 11）的读写契约。

人员字段曾经被整体当只读透传，结果是「负责航线」这类字段在本系统里没有任何入口——
而飞书网页里它同样只认自己的选人浮层，等于谁都填不了。这里钉死三件事：
读出来的值要能改（带 open_id，光有姓名改不了）、能改的判定要认人员类型、
写入要转成飞书认的 ``[{"id": open_id}]``。

纯函数与桩：不打飞书、不碰真实业务数据。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import pytest
from redis.asyncio.client import Redis

from app.plugin.module_feishu.schema import FieldMetaSchema
from app.plugin.module_feishu.service import (
    EDITABLE_TYPES,
    USER_TYPE,
    FeishuBitableService,
    _users_to_refs,
    denormalize_field_value,
)

ROLE = "wd_customer_service"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


RAW_FAIRY = {
    "id": "ou_153e1768cb950e1c7f8f919ba6aa886f",
    "name": "Fairy Li",
    "en_name": "Fairy Li",
    "avatar_url": "https://example.invalid/a.png",
}


class FakeFields(FeishuBitableService):
    """只应答 ``/fields`` 的桩。"""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.items = items

    async def _raw_field_items(self, table_id: str = "") -> list[dict[str, Any]]:
        return self.items


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            [RAW_FAIRY, {"id": "ou_kevin", "name": "Kevin Qin"}],
            [
                {"id": "ou_153e1768cb950e1c7f8f919ba6aa886f", "name": "Fairy Li"},
                {"id": "ou_kevin", "name": "Kevin Qin"},
            ],
        ),
        # 没名字的（外籍/离职后信息不全）不能丢人，退回 id 至少还能定位
        ([{"id": "ou_x", "name": ""}], [{"id": "ou_x", "name": "ou_x"}]),
        # 没有 id 的条目直接丢：改人的时候本来就认不出是谁
        ([{"name": "只有名字"}], []),
        # 非列表（单值被飞书拍平、或字段本来就空）一律空
        (None, []),
        ([], []),
        ({"id": "ou_x"}, []),
        ("ou_x", []),
    ],
)
def test_users_to_refs_shapes(value: object, expected: list[dict[str, str]]) -> None:
    assert _users_to_refs(value) == expected


def test_人员字段算可编辑() -> None:
    """不给它开这个口子，详情页与索引页就都不会给人员字段任何输入控件。"""
    assert USER_TYPE in EDITABLE_TYPES


def test_读出来的记录带_open_id_不只有姓名() -> None:
    """``fields`` 里存姓名是给人看的；改人得知道改的是哪个 id（重名时更没法猜）。"""
    svc = FakeFields([])

    record = svc._normalize_record(
        {"record_id": "rec_1", "fields": {"负责航线": [RAW_FAIRY], "件数": 3}},
        {"负责航线": USER_TYPE, "件数": 2},
        frozenset(),
    )

    assert record["fields"]["负责航线"] == ["Fairy Li"]  # 展示不变
    assert record["user_refs"] == {"负责航线": [{"id": RAW_FAIRY["id"], "name": "Fairy Li"}]}
    # 非人员字段不该冒出来
    assert "件数" not in record["user_refs"]


def test_空的人员字段不进_user_refs() -> None:
    svc = FakeFields([])

    record = svc._normalize_record({"record_id": "rec_2", "fields": {"负责航线": None}}, {"负责航线": USER_TYPE}, frozenset())

    assert record["user_refs"] == {}


def test_关联字段仍走_link_ids_不被人员分支截胡() -> None:
    """``user_refs`` 是加在 ``elif`` 上的：关联字段有 ``link_record_ids`` 没有 ``id``，
    两个分支都跑会互相污染。"""
    svc = FakeFields([])

    record = svc._normalize_record(
        {"record_id": "rec_3", "fields": {"客户": [{"link_record_ids": ["recA"]}]}},
        {"客户": 21},
        frozenset({"客户"}),
    )

    assert record["link_ids"] == {"客户": ["recA"]}
    assert record["user_refs"] == {}


def test_读回来的_open_id_能原样提交回去() -> None:
    """读取与写入必须首尾相接：拿 user_refs 里的 id 提交，要得到飞书认的形态。"""
    svc = FakeFields([])
    record = svc._normalize_record({"record_id": "rec_4", "fields": {"负责航线": [RAW_FAIRY]}}, {"负责航线": USER_TYPE}, frozenset())

    ids = [ref["id"] for ref in record["user_refs"]["负责航线"]]

    assert denormalize_field_value(USER_TYPE, ids) == [{"id": RAW_FAIRY["id"]}]


def test_字段元数据带单选多选标志() -> None:
    """「负责航线」multiple=true、「商机负责人」false；单选字段送两个人会被飞书整单拒掉。"""
    svc = FakeFields(
        [
            {"field_name": "负责航线", "type": 11, "ui_type": "User", "property": {"multiple": True}},
            {"field_name": "商机负责人", "type": 11, "ui_type": "User", "property": {"multiple": False}},
            {"field_name": "件数", "type": 2, "property": {}},
        ]
    )

    metas: list[FieldMetaSchema] = run(svc._load_fields())
    by_name = {m.name: m for m in metas}

    assert by_name["负责航线"].multiple is True
    assert by_name["商机负责人"].multiple is False
    assert by_name["负责航线"].editable is True
    assert by_name["商机负责人"].editable is True
    # 非人员类型没有 multiple 属性，一律当多值，别把它误当单选
    assert by_name["件数"].multiple is True
