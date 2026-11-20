"""查订单表里几个「人」字段的真实类型：`国内操作` 能不能当行级权限字段用。**只读**。

行级权限（高级权限/应用模式记录规则）「以下字段包含成员本人的记录」只支持**人员字段**（type 11），
创建人/修改人（1003/1004）都不行。所以要么 `国内操作` 是人员字段，要么这一岗的行级口径得换别的写法。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/probe_ops_owner_field.py
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import FeishuBitableService
from app.plugin.module_feishu.workbench import WORKBENCHES

TYPE_NAMES = {
    1: "多行文本", 2: "数字", 3: "单选", 4: "多选", 5: "日期", 7: "复选框",
    11: "人员", 13: "电话", 15: "超链接", 17: "附件", 18: "单向关联", 19: "查找引用",
    20: "公式", 21: "双向关联", 1001: "创建时间", 1002: "更新时间", 1003: "创建人",
    1004: "修改人", 1005: "自动编号", 3001: "按钮",
}
# 这几个是候选，看谁是人员字段
WANTED = ["国内操作", "操作负责人", "业务员", "商机负责人", "负责航线"]


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        for role in ("wd_domestic_ops", "wd_customer_service"):
            cfg = WORKBENCHES[role]
            svc = FeishuBitableService(redis, role)
            table_id = cfg.table_id or svc._table_id
            print(f"\n===== {role}（表 {table_id}）=====")
            meta = {f["field_name"]: f for f in await svc._raw_field_items(table_id)}
            for name in WANTED:
                f = meta.get(name)
                if not f:
                    print(f"  {name:<12} 表里没有")
                    continue
                t = TYPE_NAMES.get(f["type"], f["type"])
                ok = "✅ 可作行级权限字段" if f["type"] == 11 else "❌ 不能作行级权限字段"
                print(f"  {name:<12} {t:<10} {ok}")
                prop = f.get("property") or {}
                if f["type"] == 11 and prop:
                    print(f"       property: {prop}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
