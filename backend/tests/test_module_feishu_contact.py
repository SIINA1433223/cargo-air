"""通讯录爬取：整棵组织树要爬全，但**不能一个一个排队爬**。

飞书的成员接口只返回**直属**成员，所以「全部成员」必须先把部门树爬完再逐部门取人。
这两步原来是纯串行的，实测爬一趟 **9.5s**——而 `get_create_form`（用户点「新建」）要拿
人员字段的默认值，就得等这 9.5s。现在按层/按部门并发，掉到 1s 上下。

并发这件事写坏了有两种表现，都很隐蔽：

1. **看着对、其实还是串行**——用 ``gather`` 包了个内部还在 ``await`` 循环的东西，
   耗时一点没变，代码评审看不出来。所以下面用**在途计数峰值**钉住重叠，而不是掐秒表
   （掐秒表在慢 CI 上会闪）。
2. **爬全变成爬漏**——为了并发重写了去重，某层的子部门被 ``seen`` 提前吃掉，人员下拉
   静默少人。所以树形与成员的去重结果都要逐条对。

桩在 ``_send`` 这一层（比 ``_contact_directory`` 更靠里），因此真会走到并发逻辑。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import Any, cast

from redis.asyncio.client import Redis

from app.plugin.module_feishu.service import (
    _CONTACT_CONCURRENCY,
    FeishuBitableService,
)

ROLE = "customer_service"

_CHILDREN_RE = re.compile(r"/contact/v3/departments/([^/]+)/children")


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


class FakeContact(FeishuBitableService):
    """按一张写死的组织树应答部门/成员查询，并记录**在途请求数**。"""

    def __init__(
        self,
        tree: dict[str, list[str]],
        members: dict[str, list[tuple[str, str]]],
    ) -> None:
        super().__init__(cast("Redis", None), ROLE)
        self.tree = tree
        self.members = members
        self.inflight = 0
        # 在途峰值 = 真正发生过的最大重叠数。串行爬时它恒为 1。
        self.peak = 0
        self.children_calls: list[str] = []
        self.user_calls: list[str] = []

    async def _send(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            # 让出控制权，否则「并发」和「串行」在事件循环里长得一模一样
            await asyncio.sleep(0.01)
            found = _CHILDREN_RE.search(url)
            if found:
                dept = found.group(1)
                self.children_calls.append(dept)
                items = [{"open_department_id": c} for c in self.tree.get(dept, [])]
                return {"code": 0, "data": {"items": items, "has_more": False}}
            dept = str((kwargs.get("params") or {}).get("department_id") or "")
            self.user_calls.append(dept)
            items = [
                {"open_id": oid, "name": name} for oid, name in self.members.get(dept, [])
            ]
            return {"code": 0, "data": {"items": items, "has_more": False}}
        finally:
            self.inflight -= 1


def sample() -> FakeContact:
    """一棵三层树：根 0 → (A, B, C)，A → (A1, A2)；B 与 C 是叶子。

    A/A1 里都有同一个人（人可以在多个部门里），用来验跨部门去重。
    """
    tree = {"0": ["A", "B", "C"], "A": ["A1", "A2"]}
    members = {
        "0": [("ou_boss", "总经理")],
        "A": [("ou_dup", "重复的人"), ("ou_a", "甲")],
        "A1": [("ou_dup", "重复的人"), ("ou_a1", "甲一")],
        "A2": [("ou_a2", "甲二")],
        "B": [("ou_b", "乙")],
        "C": [("ou_c", "丙")],
    }
    return FakeContact(tree, members)


def test_部门树要爬到叶子_不只看根() -> None:
    svc = sample()
    ids = run(svc._contact_department_ids())
    assert ids == ["0", "A", "B", "C", "A1", "A2"], "漏了子部门 = 人员下拉静默少人"
    # 叶子部门也会被问一次「有没有子部门」——不问就无从知道它是叶子
    assert sorted(svc.children_calls) == ["0", "A", "A1", "A2", "B", "C"]


def test_根部门只有一个人时也要看到全部人() -> None:
    """飞书只返回直属成员；只看根部门的话 18 个人里只能看到 1 个。"""
    svc = sample()
    users = run(svc._load_contact_users())
    assert len(users) == 7, f"应去重后 6 部门 8 条 → 7 人，实际 {len(users)}"
    assert [u["name"] for u in users] == sorted(u["name"] for u in users), "要按名字排序"


def test_同一个人出现在多个部门只算一次() -> None:
    svc = sample()
    users = run(svc._load_contact_users())
    assert [u["open_id"] for u in users].count("ou_dup") == 1


def test_同层部门是并发取的_不是排成一列() -> None:
    """峰值在途数 > 1 即证明重叠发生；串行实现下它恒为 1。"""
    svc = sample()
    run(svc._contact_department_ids())
    assert svc.peak >= 3, (
        f"3 个同层子部门应一起发，峰值在途只有 {svc.peak}——并发等于没生效，"
        "用户点「新建」还会等上原来的 9.5s"
    )


def test_取成员也是并发的() -> None:
    svc = sample()
    run(svc._load_contact_users())
    assert svc.peak >= 4, f"6 个部门取成员本应并发，峰值在途只有 {svc.peak}"


def test_并发不超过限流上限() -> None:
    """组织树深的租户一次性发上百个请求会被飞书限流。"""
    wide = FakeContact({"0": [f"D{i}" for i in range(30)]}, {})
    assert _CONTACT_CONCURRENCY < 30, "本用例要能真的触发限流"
    run(wide._load_contact_users())
    assert wide.peak <= _CONTACT_CONCURRENCY, f"峰值 {wide.peak} 超过上限 {_CONTACT_CONCURRENCY}"


def test_组织树成环不会无限爬() -> None:
    """真实组织树不会成环，但配置错/接口异常时不能把服务转死。"""
    svc = FakeContact({"0": ["A"], "A": ["0", "A"]}, {})
    ids = run(svc._contact_department_ids())
    assert ids == ["0", "A"]
