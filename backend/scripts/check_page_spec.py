"""把「应用模式迁移清单」里写下的字段口径，逐条拿去和飞书真表对一遍。**只读**。

查什么：
  1. 配置里引用到的每一个字段名，在表里**是否真的存在**、类型是什么
  2. 每个字段的**实测非空率**（全量拉一遍）——列表列放上去会不会是整列空白
  3. 搜索字段是不是**文本**（contains 对自动编号/公式/关联不生效，混进去整个条件会被拒）
  4. 排序字段的性质是否还成立（定宽自动编号才等于创建时间倒排）
  5. `create_form_view_id` 那个表单视图**是否还在**
  6. 表里**已有哪些视图**（应用模式的列表组件可以直接挂现成视图，不用新造）

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/check_page_spec.py wd_customer_service
    PYTHONPATH=$PWD .venv/bin/python scripts/check_page_spec.py all
"""

from __future__ import annotations

import asyncio
import sys

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import (
    _SEARCHABLE_TYPES,
    FeishuBitableService,
    _get_tenant_access_token,
    _http_json,
)
from app.plugin.module_feishu.workbench import WORKBENCHES

TYPE_NAMES = {
    1: "多行文本", 2: "数字", 3: "单选", 4: "多选", 5: "日期", 7: "复选框",
    11: "人员", 13: "电话", 15: "超链接", 17: "附件", 18: "单向关联", 19: "查找引用",
    20: "公式", 21: "双向关联", 22: "地理位置", 23: "群组", 24: "阶段条",
    1001: "创建时间", 1002: "更新时间", 1003: "创建人", 1004: "修改人",
    1005: "自动编号", 3001: "按钮",
}
# 用**后端自己那份**可搜索类型，别自成一套判据：服务端 `_build_filter` 会按它筛掉
# 不支持 contains 的列（且飞书对一个坏条件会整单拒掉）。这里跟它保持一致。
TEXTISH = set(_SEARCHABLE_TYPES)

# 这些是**派生/聚合列**，不是表里的真实字段，配置有意这么写（见 workbench.py 注释）：
# 货物数据=聚合列；订舱状态/计划状态=由勾选框在前端反推的徽章。
DERIVED_COLUMNS = {"货物数据", "订舱状态", "计划状态"}
VIEW_TYPES = {1: "表格", 2: "看板", 3: "画册", 4: "表单", 5: "日历", 6: "甘特", 7: "查询", 8: "仪表盘"}


def is_empty(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


async def audit(redis, role: str) -> None:
    cfg = WORKBENCHES[role]
    svc = FeishuBitableService(redis, role)
    table_id = cfg.table_id or svc._table_id
    token = await _get_tenant_access_token(redis)
    base = settings.LARK_BASE_URL.rstrip("/")

    raw_fields = await svc._raw_field_items(table_id)
    meta = {f["field_name"]: f for f in raw_fields}

    records = await svc._list_raw_records(table_id, limit=2000)
    n = len(records)

    def nonnull_rate(name: str) -> float | None:
        if name not in meta:
            return None
        hit = sum(1 for r in records if not is_empty((r.get("fields") or {}).get(name)))
        return hit / n if n else 0.0

    print(f"\n{'=' * 78}\n【{role}】{cfg.name} —— 表 {table_id}，实测 {n} 行\n{'=' * 78}")

    # 1+2+3. 配置引用的字段逐条核对
    referenced: list[tuple[str, str]] = []
    referenced += [(x, "列表列") for x in cfg.columns]
    referenced += [(x, "列表投影") for x in cfg.list_fields if x not in cfg.columns]
    referenced += [(k, "排序") for k, _ in cfg.sort]
    referenced += [(x, "搜索") for x in cfg.search_fields]
    referenced += [(x, "详情页头") for x in cfg.base_fields]
    for g in cfg.groups:
        referenced += [(x, f"新建分组·{g.label}") for x in g.fields]
    for g in cfg.detail_sections:
        referenced += [(x, f"详情Tab·{g.label}") for x in g.fields]
    if cfg.owner_field:
        referenced.append((cfg.owner_field, "行级权限"))
    # 从表页的字段属于**从表**，拿主表的字段清单去比对会误报（差点把「操作编号」报成不存在）
    related_meta: dict[str, set[str]] = {}
    for r in cfg.related:
        if r.fields:
            related_meta[r.label] = {
                f["field_name"] for f in await svc._raw_field_items(r.table_id)
            }
            referenced += [(x, f"从表页·{r.label}") for x in r.fields]

    seen: set[tuple[str, str]] = set()
    problems: list[str] = []
    print(f"{'字段名':<24}{'用途':<18}{'类型':<10}{'非空率':>8}")
    for name, where in referenced:
        if (name, where) in seen:
            continue
        seen.add((name, where))
        if where.startswith("从表页·"):
            label = where.split("·", 1)[1]
            exists = name in related_meta.get(label, set())
            t = "从表字段" if exists else "**从表里没有**"
            rate_s = "—"
            if not exists:
                problems.append(f"从表字段不存在：{label} 里的 {name}")
            print(f"{name:<24}{where:<18}{t:<10}{rate_s:>8}")
            continue
        if name in DERIVED_COLUMNS and name not in meta:
            print(f"{name:<24}{where:<18}{'派生/聚合列':<10}{'—':>8}")
            continue
        f = meta.get(name)
        t = TYPE_NAMES.get(f["type"], "?") if f else "**表里没有**"
        rate = nonnull_rate(name)
        rate_s = "—" if rate is None else f"{rate * 100:.0f}%"
        print(f"{name:<24}{where:<18}{t:<10}{rate_s:>8}")
        if f is None:
            problems.append(f"字段不存在：{name}（{where}）")
        elif where == "搜索" and f["type"] not in TEXTISH:
            problems.append(f"搜索字段不是文本：{name} 是{TYPE_NAMES.get(f['type'])}，contains 会失效")
        elif where == "列表列" and rate == 0:
            problems.append(f"列表【{name}】非空率 0 —— 摆上去是整列空白")
        elif where == "行级权限" and f["type"] != 11:
            problems.append(f"行级权限字段 {name} 不是人员字段（当前{TYPE_NAMES.get(f['type'])}）")

    # 4. 排序
    for name, desc in cfg.sort:
        f = meta.get(name)
        order = "倒序" if desc else "正序"
        if f and f["type"] == 1005:
            print(
                f"\n排序 {name} {order}（自动编号，倒序=创建时间倒序）："
                "应用模式里可直接改用「创建时间」排序"
            )
        elif f:
            print(f"\n排序 {name} {order}（{TYPE_NAMES.get(f['type'])}）")
        else:
            print(f"\n排序 {name} {order}（**表里没有这个字段**）")
            problems.append(f"排序字段不存在：{name}")

    # 5+6. 视图
    v = await _http_json(
        "GET",
        f"{base}/bitable/v1/apps/{cfg.app_token or svc._app_token}/tables/{table_id}/views",
        token,
        params={"page_size": 50},
    )
    views = (v.get("data") or {}).get("items") or []
    print(f"\n--- 表里现有 {len(views)} 个视图（应用模式的列表组件可直接挂现成的）---")
    for it in views:
        mark = "  ← 代码里的新建表单" if it.get("view_id") == cfg.create_form_view_id else ""
        print(f"  {it.get('view_name', ''):<22}{VIEW_TYPES.get(it.get('view_type'), it.get('view_type')):<8}{it.get('view_id')}{mark}")

    if cfg.create_form_view_id and not any(
        it.get("view_id") == cfg.create_form_view_id for it in views
    ):
        problems.append(f"新建表单视图 {cfg.create_form_view_id} 在表里找不到了")

    # 7. 关联分区 / 从表
    if cfg.related:
        print("\n--- 从表页 ---")
        for r in cfg.related:
            print(f"  {r.label}（{r.table_id}）：反查字段 {r.back_ref_field}，铺 {len(r.fields)} 列")

    print("\n----- 结论 -----")
    if problems:
        for p in problems:
            print(f"  ⚠️ {p}")
    else:
        print("  ✅ 配置里引用的字段全部对得上，无空白列、无非法搜索字段")


async def main() -> None:
    roles = sys.argv[1:] or ["wd_customer_service"]
    if roles == ["all"]:
        roles = list(WORKBENCHES)
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        for role in roles:
            if role not in WORKBENCHES:
                print(f"没有这个工作台：{role}")
                continue
            await audit(redis, role)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
