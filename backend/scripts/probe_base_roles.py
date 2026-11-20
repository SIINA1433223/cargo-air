"""把威达 base 现有的「高级权限」配置读出来：自定义角色、每个角色的成员、以及
每个角色对各张表的权限（含记录规则，如果接口回的话）。**只读**。

为什么值得看：应用模式里「谁能看到哪些行」来自高级权限，如果这里已经有能用的角色，
就该复用而不是另起一套。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/probe_base_roles.py
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import (
    _get_tenant_access_token,
    _http_json,
)
from app.plugin.module_feishu.workbench import (
    WD_APP_TOKEN,
    WD_CUSTOMER_SERVICE_TABLE,
    WD_PRICING_TABLE,
)

TABLE_NAMES = {
    WD_CUSTOMER_SERVICE_TABLE: "商机主表（国内客服）",
    WD_PRICING_TABLE: "航线报价",
    "tblqNRxcBz4HRbY6": "订单表（国内操作）",
}

TABLE_PERM = {0: "无权限", 1: "可阅读", 2: "可编辑", 3: "可管理"}
FIELD_PERM = {0: "无", 1: "可阅读", 2: "可编辑"}


async def main() -> None:
    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    token = await _get_tenant_access_token(redis)
    base = settings.LARK_BASE_URL.rstrip("/")

    import httpx

    try:
        data = await _http_json(
            "GET", f"{base}/bitable/v1/apps/{WD_APP_TOKEN}/roles", token, params={"page_size": 30}
        )
    except httpx.HTTPStatusError as exc:
        print("\n角色接口被拒，飞书原话：", exc.response.text[:600])
        await redis.aclose()
        return
    print(f"\n飞书返回 code={data.get('code')} msg={data.get('msg')}")
    roles = (data.get("data") or {}).get("items") or []
    print(f"===== 自定义角色 {len(roles)} 个 =====\n")

    for r in roles:
        print(f"● {r.get('role_name')}   (role_id={r.get('role_id')})")
        print(f"  表数量={r.get('table_roles', []) and len(r['table_roles'])}")
        for tr in r.get("table_roles") or []:
            tid = tr.get("table_id")
            print(f"  - {TABLE_NAMES.get(tid, tid)}: 表权限={TABLE_PERM.get(tr.get('table_perm'), tr.get('table_perm'))}")
            fp = tr.get("field_perm")
            if isinstance(fp, dict):
                limited = {k: FIELD_PERM.get(v, v) for k, v in fp.items() if v != 1}
                print(f"      字段权限：非默认的 {len(limited)} 个 {limited if len(limited) <= 6 else '(略)'}")
            rr = tr.get("rec_rule") or tr.get("record_rule")
            if rr:
                print(f"      记录规则：{json.dumps(rr, ensure_ascii=False)[:400]}")
            else:
                print("      记录规则：接口没回（大概率要开高级权限的版本才有）")

        m = await _http_json(
            "GET",
            f"{base}/bitable/v1/apps/{WD_APP_TOKEN}/roles/{r.get('role_id')}/members",
            token,
            params={"page_size": 30},
        )
        items = (m.get("data") or {}).get("items") or []
        names = []
        for it in items:
            names.append(it.get("name") or it.get("member_id") or "?")
        print(f"  成员 {len(items)} 人：{'、'.join(names[:12])}{' …' if len(names) > 12 else ''}")
        print()

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
