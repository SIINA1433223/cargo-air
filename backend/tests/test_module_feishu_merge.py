"""合并 / 拆出的组内成员重算（需求文档 §4.8 精确规则）。

合并组只存在「合出主单」/「合出分单」两个关联字段里，字段存的是**全组成员（含自身）**
的 record_id。所以「跨组重组」「拆出」的本质都是把相关组重写成正确的成员集合——写漏一处，
旧引用就会让并查集把两个组粘成一个，线上表现是「怎么拆都拆不开、越合越大」。

写入层是假的：``_link_ids_of`` 读内存里的表，``_batch_update`` 写回内存，
**不打飞书、不碰任何真实业务数据**。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

from redis.asyncio.client import Redis

from app.plugin.module_feishu.service import FeishuBitableService
from app.plugin.module_feishu.workbench import MERGE_HAWB_FIELD, MERGE_MASTER_FIELD

MASTER = "master_awb"
HAWB = "hawb"

Row = dict[str, Any]


class FakeTable(FeishuBitableService):
    """内存版的飞书表：只实现合并组用到的两个关联字段。"""

    def __init__(self, master: Row | None = None, hawb: Row | None = None) -> None:
        super().__init__(cast("Redis", None))
        self.master: Row = dict(master or {})
        self.hawb: Row = dict(hawb or {})
        # 每次 _batch_update 提交的一批，用来断言「同一条记录不在一批里出现两次」
        self.calls: list[list[Row]] = []

    async def _link_ids_of(self, record_ids: list[str], field: str) -> dict[str, list[str]]:
        store = self.master if field == MERGE_MASTER_FIELD else self.hawb
        return {rid: list(store.get(rid, [])) for rid in record_ids}

    async def _batch_update(self, updates: list[Row], action: str, table_id: str = "") -> int:
        if not updates:
            return 0
        self.calls.append(updates)
        for update in updates:
            fields: Row = update["fields"]
            for field, value in fields.items():
                store = self.master if field == MERGE_MASTER_FIELD else self.hawb
                store[update["record_id"]] = list(value)
        return len(updates)

    # ---- 断言辅助 ----

    def group_of(self, field: str, *rids: str) -> list[list[str]]:
        """取若干票在指定关联字段上的成员集合（已排序，便于直接比对）。"""
        store = self.master if field == MERGE_MASTER_FIELD else self.hawb
        return [sorted(store.get(rid, [])) for rid in rids]

    def assert_no_duplicate_in_any_call(self) -> None:
        for batch in self.calls:
            ids = [update["record_id"] for update in batch]
            assert len(ids) == len(set(ids)), f"同一批写入里出现了重复 record_id: {ids}"


def run(coro: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
    """跑一个服务层协程。仓库未装 pytest-asyncio，沿用 asyncio.run 的既有写法。"""
    return asyncio.run(coro)


def test_scene1_unmerge_one_from_three() -> None:
    """场景1：ABC 合主单，拆 A 出来 → BC 保持合主单且只含 BC，A 为空。"""
    table = FakeTable(master={"A": ["A", "B", "C"], "B": ["A", "B", "C"], "C": ["A", "B", "C"]})

    result = run(table.unmerge_records(["A"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "B", "C") == [["B", "C"], ["B", "C"]]
    assert table.group_of(MERGE_MASTER_FIELD, "A") == [[]]
    assert result["remaining_count"] == 2
    table.assert_no_duplicate_in_any_call()


def test_scene2_unmerge_one_from_two_dissolves_group() -> None:
    """场景2：AB 合主单，拆 A 出来 → 组只剩一票不成组，两票都清空。"""
    table = FakeTable(master={"A": ["A", "B"], "B": ["A", "B"]})

    result = run(table.unmerge_records(["A"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "A", "B") == [[], []]
    assert result["remaining_count"] == 0


def test_scene3_cross_group_rebuild_of_two_three_ticket_groups() -> None:
    """场景3：ABC 与 DEF 各合主单，重新点 AD 合主单 → AD 成组，BC、EF 各留原组。"""
    table = FakeTable(
        master={
            "A": ["A", "B", "C"],
            "B": ["A", "B", "C"],
            "C": ["A", "B", "C"],
            "D": ["D", "E", "F"],
            "E": ["D", "E", "F"],
            "F": ["D", "E", "F"],
        }
    )

    result = run(table.merge_records(["A", "D"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "A", "D") == [["A", "D"], ["A", "D"]]
    assert table.group_of(MERGE_MASTER_FIELD, "B", "C") == [["B", "C"], ["B", "C"]]
    assert table.group_of(MERGE_MASTER_FIELD, "E", "F") == [["E", "F"], ["E", "F"]]
    assert result["detached_count"] == 4  # BC 与 EF 各两条
    table.assert_no_duplicate_in_any_call()


def test_scene4_cross_group_rebuild_of_two_two_ticket_groups() -> None:
    """场景4：AB 与 CD 各合主单，重新点 AC → AC 成组，B、D 都清空。"""
    table = FakeTable(master={"A": ["A", "B"], "B": ["A", "B"], "C": ["C", "D"], "D": ["C", "D"]})

    run(table.merge_records(["A", "C"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "A", "C") == [["A", "C"], ["A", "C"]]
    assert table.group_of(MERGE_MASTER_FIELD, "B", "D") == [[], []]
    table.assert_no_duplicate_in_any_call()


def test_scene5_unmerge_master_also_releases_hawb() -> None:
    """场景5：AB 既合主单又合分单，拆主单 → 分单一并解除（留下那票也不能再粘着）。"""
    both = {"A": ["A", "B"], "B": ["A", "B"]}
    table = FakeTable(master=dict(both), hawb=dict(both))

    run(table.unmerge_records(["A"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "A", "B") == [[], []]
    assert table.group_of(MERGE_HAWB_FIELD, "A", "B") == [[], []]
    table.assert_no_duplicate_in_any_call()


def test_merge_of_isolated_tickets_writes_no_detach() -> None:
    """从没合过的两票直接合并：没有旧组要维护，只写新组一次。"""
    table = FakeTable()

    result = run(table.merge_records(["A", "B"], MASTER))

    assert result["detached_count"] == 0
    assert len(table.calls) == 1
    assert table.group_of(MERGE_MASTER_FIELD, "A", "B") == [["A", "B"], ["A", "B"]]


def test_merge_subset_of_a_group_keeps_remainder_alone() -> None:
    """同组内重新合并一部分（ABC 点 AB）：剩下的 C 单独一票，清空。"""
    table = FakeTable(master={"A": ["A", "B", "C"], "B": ["A", "B", "C"], "C": ["A", "B", "C"]})

    run(table.merge_records(["A", "B"], MASTER))

    assert table.group_of(MERGE_MASTER_FIELD, "A", "B") == [["A", "B"], ["A", "B"]]
    assert table.group_of(MERGE_MASTER_FIELD, "C") == [[]]


def test_merge_and_unmerge_keep_the_two_fields_independent() -> None:
    """合并分单不动主单字段，拆分单同样不动 —— 两个字段各管各的。"""
    table = FakeTable(master={"A": ["A", "B"]})

    run(table.merge_records(["A", "B"], HAWB))
    assert table.master == {"A": ["A", "B"]}
    assert table.group_of(MERGE_HAWB_FIELD, "A", "B") == [["A", "B"], ["A", "B"]]

    run(table.unmerge_records(["A"], HAWB))
    assert table.master == {"A": ["A", "B"]}
    assert table.group_of(MERGE_HAWB_FIELD, "A", "B") == [[], []]
