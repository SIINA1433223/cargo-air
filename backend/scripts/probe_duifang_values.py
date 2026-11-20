"""只读探针：对客方案里的查找列到底回什么形状。

界面上「最早航班时间」显示成一串 ``17907395400000``，而它是对客方案里的**查找列(19)**，
源字段在航线报价表里。要看的是：飞书回的原始值、我们的归一化结果、以及源字段的类型。
顺带打印审批结果/方案状态/客户反馈的可选项。
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import FeishuBitableService, _http_json

ROLE = "wd_customer_service"
DUIFANG = "tblcTmqLBPpDxbIb"
HANGXIAN = "tbltztWS5DLZlly1"

WATCH = ["最早航班时间", "启运港", "目的港", "航空公司", "供应商编码", "方案编号", "对客报价", "报价有效期至"]
OPTION_FIELDS = ["方案状态", "审批结果", "客户反馈"]


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        svc = FeishuBitableService(redis, ROLE)

        metas = {m.name: m for m in await svc._table_fields(DUIFANG)}
        print("=== 对客方案的字段定义 ===")
        for name in WATCH + OPTION_FIELDS:
            m = metas.get(name)
            if not m:
                print(f"  ✗ {name} 不存在")
                continue
            opts = f"  options={m.options}" if m.options else ""
            link = f"  → {m.link_table_id}" if m.link_table_id else ""
            print(f"  {m.type:>5} {m.name}{link}{opts}")

        hx = {m.name: m for m in await svc._table_fields(HANGXIAN)}
        src = hx.get("最早航班时间")
        print(f"\n航线报价里的源字段 最早航班时间: type={src.type if src else '不存在'}")

        # 真调一次 records/search 拿原始 JSON（绕过我们的归一化）
        token = await svc._token()
        url = await svc._table_endpoint(DUIFANG, "/records/search")
        data = await _http_json("POST", url, token, params={"page_size": 3}, json={})
        for item in (data.get("data") or {}).get("items") or []:
            raw = item.get("fields") or {}
            print(f"\n--- record {item.get('record_id')} 原始字段值 ---")
            for name in WATCH:
                if name in raw:
                    print(f"  {name}: {json.dumps(raw[name], ensure_ascii=False)}")
            # 归一化之后
            norm = svc._normalize_record(item, {n: m.type for n, m in metas.items()}, frozenset())
            print("  归一化后:")
            for name in WATCH:
                if name in norm["fields"]:
                    print(f"    {name}: {norm['fields'][name]!r}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
