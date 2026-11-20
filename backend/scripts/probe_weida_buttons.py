"""只读探针：威达两张表里所有**按钮字段(3001)** 的名字与顺序。

「发起报价审批」这个按钮要摆在对客方案上方，得先确认它到底挂在**哪张表**上
（商机主表还是对客方案表）——挂主表就是商机级字段，挂对客方案表就是方案级字段，
两者的摆法完全不同。
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import FeishuBitableService

ROLE = "wd_customer_service"
SHANGJI = "tblXmBgagFcTGWJr"
DUIFANG = "tblcTmqLBPpDxbIb"
HANGXIAN = "tbltztWS5DLZlly1"

TABLES = (("商机主表", SHANGJI), ("对客方案", DUIFANG), ("航线报价", HANGXIAN))


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        svc = FeishuBitableService(redis, ROLE)
        for label, table_id in TABLES:
            metas = await svc._table_fields(table_id)
            buttons = [(i, m) for i, m in enumerate(metas) if m.type == 3001]
            print(f"=== {label} {table_id}（共 {len(metas)} 字段，按钮 {len(buttons)} 个）===")
            for index, meta in buttons:
                print(f"  #{index:>3}  {meta.name}   description={meta.description!r}")
            # 名字里带「审批」的字段（不限于按钮）
            hits = [m for m in metas if "审批" in m.name]
            if hits:
                print("  含「审批」的字段: " + "、".join(f"{m.name}({m.type})" for m in hits))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
