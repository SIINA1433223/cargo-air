"""客服「单票状态」的派生规则回归测试。

状态由商机（`航线报价` / `是否拿到货` / `未拿到货备注`）与它那张订单（`负责航线已核价` /
`交接给国内操作`）共同推导，飞书侧不存这个枚举。规则是业务方逐条口述的，**判定顺序**才是
规则本身：先问「走完了没」，再问下一步该谁干。写反了不会报错，只会让某一档永远显示不出来
（例如把「已核价」排在「没拿到货」前面，那条终止路径就再也到不了「已完成」）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from app.plugin.module_feishu.service import (
    SINGLE_STATUS_VALUES,
    FeishuBitableService,
    _metas_from_raw,
    _single_status,
)
from app.plugin.module_feishu.workbench import (
    SINGLE_STATUS_HANDOVER_FIELD,
    SINGLE_STATUS_PRICED_FIELD,
    SINGLE_STATUS_PRICED_VALUE,
    WD_ORDER_TABLE,
)

ROLE = "wd_domestic_ops"


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def test_all_five_states_are_reachable() -> None:
    """五档都要能出现——某一档永远到不了时，界面上看不出错，但业务上是一整类单子消失了。"""
    produced = {
        _single_status(False, "", "", False, False),  # 待航线报价
        _single_status(True, "", "", False, False),  # 待确认拿到货
        _single_status(True, "是", "", False, False),  # 待核价
        _single_status(True, "是", "", True, False),  # 待交接操作
        _single_status(True, "是", "", True, True),  # 已完成（交接）
    }
    assert produced == set(SINGLE_STATUS_VALUES)


@pytest.mark.parametrize(
    ("quote_has", "got", "note", "priced", "handover", "expected"),
    [
        # 1. 交接给国内操作勾了 = 最后一步，压过其余一切
        (True, "是", "客户要求等一下", True, True, "已完成"),
        # 2. 没拿到货且备注填了 = 终止（用户确认「已完成优先」）
        (True, "否", "客户取消", True, False, "已完成"),
        # 3. 已核价 → 等交接
        (True, "是", "", True, False, "待交接操作"),
        # 4. 拿到货 → 等核价
        (True, "是", "", False, False, "待核价"),
        # 5. 有报价 → 等确认拿到货
        (True, "", "", False, False, "待确认拿到货"),
        # 6. 兜底
        (False, "", "", False, False, "待航线报价"),

        # 「否」但备注空：**不**当已完成，退回 5/6 —— 没拿到货又没说清原因的单子
        # 应该停在「待确认拿到货」提醒人补备注，而不是当它结束了
        (True, "否", "", False, False, "待确认拿到货"),
        (True, "否", "   ", False, False, "待确认拿到货"),
        (False, "否", "", False, False, "待航线报价"),
        # 备注只有空白也算空（业务填错格的常见形态）
        (True, "否", "\n\t ", False, False, "待确认拿到货"),

        # 没订单/订单没读到 → 两个订单侧标志都是 False，照商机自身字段给
        (True, "是", "", False, False, "待核价"),
        (False, "", "", False, False, "待航线报价"),
    ],
)
def test_priority(
    quote_has: bool, got: str, note: str, priced: bool, handover: bool, expected: str
) -> None:
    assert _single_status(quote_has, got, note, priced, handover) == expected


def test_note_is_trimmed_before_deciding() -> None:
    """只有空白字符的备注等同于没填——飞书里空格是很容易被顺手带进去的。"""
    assert _single_status(True, "否", "  \n ", False, False) == "待确认拿到货"
    assert _single_status(True, "否", " 客户取消 ", False, False) == "已完成"


# ============================ 订单状态索引的缓存（`_order_status_map`）============================
#
# 列表每次都要这份索引（`_fill_single_status`），而它是**整表扫订单表**——实测占列表总时长的
# 一半（国内客服 2.73s 里 1.33s）。所以加了 60 秒缓存。缓存这类东西有两种坏法，都不会报错：
# ① 该失效时没失效 → 用户在本系统点了「交接给国内操作」，回到列表看到的还是旧标签，会被当成
#    「没写进去」；② 该带 TTL 时忘了带 → 一次写入永久驻留，飞书里改的东西再也不出现。


class _FakeRedis:
    """订单状态缓存只用到 get/set/delete 三条命令，`RedisCURD` 就调这三个。"""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expires: dict[str, int | None] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, name: str, value: bytes, ex: int | None = None) -> None:
        self.store[name] = value
        self.expires[name] = ex

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)


class FakeOrderStatus(FeishuBitableService):
    """数「订单表被扫了几遍」的桩：第二次起该命中缓存。"""

    def __init__(self, redis: _FakeRedis, fields: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        super().__init__(redis, ROLE)  # type: ignore[arg-type]
        self.fields = fields
        self.rows = rows
        self.scans = 0

    async def _table_fields(self, table_id: str = "") -> list[Any]:
        return _metas_from_raw(self.fields)

    async def _search_page(  # type: ignore[override]
        self,
        filter_obj: Any,
        page_token: Any,
        page_size: int,
        field_names: Any = None,
        sort: Any = None,
        table_id: str = "",
    ) -> dict[str, Any]:
        self.scans += 1
        return {"items": list(self.rows), "has_more": False}


def _f(field_id: str, name: str, ftype: int) -> dict[str, Any]:
    return {"field_id": field_id, "field_name": name, "type": ftype, "ui_type": ""}


def _status_fields() -> list[dict[str, Any]]:
    return [
        _f("a", SINGLE_STATUS_PRICED_FIELD, 3),  # 单选
        _f("b", SINGLE_STATUS_HANDOVER_FIELD, 7),  # 复选框
    ]


def _status_rows() -> list[dict[str, Any]]:
    return [
        {"record_id": "o1", "fields": {SINGLE_STATUS_PRICED_FIELD: SINGLE_STATUS_PRICED_VALUE}},
        {"record_id": "o2", "fields": {SINGLE_STATUS_PRICED_FIELD: SINGLE_STATUS_PRICED_VALUE, SINGLE_STATUS_HANDOVER_FIELD: True}},
    ]


def test_第二次取状态不再扫订单表() -> None:
    redis = _FakeRedis()
    svc = FakeOrderStatus(redis, _status_fields(), _status_rows())

    first = run(svc._order_status_map(WD_ORDER_TABLE))
    second = run(svc._order_status_map(WD_ORDER_TABLE))

    assert first == {"o1": (True, False), "o2": (True, True)}
    assert second == first, "命中缓存也必须给出同一份映射，否则列表的标签会随机变"
    assert svc.scans == 1, f"第二次应当命中缓存，实际扫了 {svc.scans} 遍"


def test_缓存必须带过期时间() -> None:
    """漏掉 expire 就成了永久缓存：飞书里改的东西在本系统里再也不出现，而且重启也不恢复
    （Redis 不随进程重启清空）。"""
    redis = _FakeRedis()
    run(FakeOrderStatus(redis, _status_fields(), _status_rows())._order_status_map(WD_ORDER_TABLE))

    assert list(redis.expires.values()) == [60], f"过期时间不对：{redis.expires}"


def test_写完订单表后立即失效而不是等_60_秒() -> None:
    """本系统自己的写入（核价/交接）是列表标签的主要来源，滞后 60 秒会被当成写失败。"""
    redis = _FakeRedis()
    svc = FakeOrderStatus(redis, _status_fields(), _status_rows())
    run(svc._order_status_map(WD_ORDER_TABLE))

    run(svc._invalidate_order_status(WD_ORDER_TABLE))
    run(svc._order_status_map(WD_ORDER_TABLE))

    assert svc.scans == 2, "写入之后应当重新扫订单表"


def test_清别的表不会误伤本表() -> None:
    """键里带 table_id 就是为了这个：两个 base 的表 id 可能撞名，清一张不该清掉另一张。"""
    redis = _FakeRedis()
    svc = FakeOrderStatus(redis, _status_fields(), _status_rows())
    run(svc._order_status_map(WD_ORDER_TABLE))

    run(svc._invalidate_order_status("tblOther"))
    run(svc._order_status_map(WD_ORDER_TABLE))

    assert svc.scans == 1


def test_缓存内容坏掉时重新拉而不是让列表打不开() -> None:
    redis = _FakeRedis()
    svc = FakeOrderStatus(redis, _status_fields(), _status_rows())
    run(svc._order_status_map(WD_ORDER_TABLE))
    redis.store[svc._order_status_cache_key(WD_ORDER_TABLE)] = b"{ not json"

    assert run(svc._order_status_map(WD_ORDER_TABLE)) == {"o1": (True, False), "o2": (True, True)}
    assert svc.scans == 2


def test_状态字段被改名时不缓存以免警告只响一次() -> None:
    """这个分支是配置漂移的信号（日志里的 WARNING）。缓存住 60 秒才响一次，就没人追了。"""
    redis = _FakeRedis()
    svc = FakeOrderStatus(redis, [_f("a", "别的字段", 3)], [])

    run(svc._order_status_map(WD_ORDER_TABLE))
    run(svc._order_status_map(WD_ORDER_TABLE))

    assert redis.store == {}, f"这一分支不该进缓存：{redis.store}"
