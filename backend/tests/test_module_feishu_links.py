"""单向关联字段（``合出主单``/``合出分单``）的解析回归测试。

合并组完全建立在这两个字段的 record_id 上。线上真实返回里，**只有一个关联值时飞书给的是
裸 dict ``{"link_record_ids": [...]}`` 而不是套数组**；早期实现只认 ``list``，导致所有合并
关联被静默丢成空列表 —— 前端合并组算成 0 个、合并组排序与「移出合并组」全部失效，
而且不报任何错。这里把线上见过的每种形态都钉死。
"""

import pytest

from app.plugin.module_feishu.service import _links_to_ids, _links_to_text

MASTER = [{"link_record_ids": ["recveEPx4nAXMY", "recveEME8poM20"]}]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # 线上真实形态：单条关联 → 裸 dict（曾经被漏掉的那个）
        ({"link_record_ids": ["recveEPx4nAXMY", "recveEME8poM20"]}, ["recveEPx4nAXMY", "recveEME8poM20"]),
        # 单条关联但关联为空 → 裸 dict 且值为 null
        ({"link_record_ids": None}, []),
        # 常见形态：数组套 dict
        (MASTER, ["recveEPx4nAXMY", "recveEME8poM20"]),
        # 其它键名兜底
        ([{"record_ids": ["a", "b"]}], ["a", "b"]),
        ([{"record_id": "a"}], ["a"]),
        ([{"id": "a"}], ["a"]),
        ([{"value": "a"}], ["a"]),
        # 嵌套 dict 不能被 str() 成 "{'record_id': 'a'}" 这种垃圾 id
        ([{"link_record_ids": [{"record_id": "a"}, {"record_id": "b"}]}], ["a", "b"]),
        # 纯字符串与字符串数组
        ("recA", ["recA"]),
        (["recA", "recB"], ["recA", "recB"]),
        # 空值一律空列表
        (None, []),
        ([], []),
        ("", []),
        ({"link_record_ids": []}, []),
        ([{"link_record_ids": []}], []),
        # 去重保序
        (["b", "a", "b"], ["b", "a"]),
        # 无关类型（数字/布尔）不炸
        (123, []),
        (True, []),
    ],
)
def test_links_to_ids_shapes(value: object, expected: list[str]) -> None:
    assert _links_to_ids(value) == expected


def test_links_to_text_accepts_bare_dict() -> None:
    """文本侧同一根因：裸 dict 也要能取出展示文本。"""
    assert _links_to_text([{"text": "4100124128"}, {"text": "4100124129"}]) == ["4100124128", "4100124129"]
    assert _links_to_text({"text": "4100124128"}) == ["4100124128"]
    # ``records/search`` 投影关联字段时不返回 text，此时退回 None（前端显示发票号）
    assert _links_to_text({"link_record_ids": ["recA"]}) is None
    assert _links_to_text(None) is None
