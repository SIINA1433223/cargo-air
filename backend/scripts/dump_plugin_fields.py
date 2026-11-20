"""把威达 base 里若干张表的字段清单导成「妙搭 feishu-bitable 插件实例」要求的
`fields` 数组形状（id / name / type / bizType / readable / writeable / enumValues）。
**只读**，不写飞书任何东西。

为什么要这么倒：妙搭那个官方插件实例的 `formValue.fields` 决定 searchRecords 返回
哪些列——空数组就只回 record_id。字段清单正常由妙搭 UI 选表时生成，这里离线生成
一份等价物。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/dump_plugin_fields.py <输出目录>
每张表写一个 <别名>.fields.json。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from app.config.setting import settings
from app.plugin.module_feishu.service import (
    _get_tenant_access_token,
    _http_json,
)
from app.plugin.module_feishu.workbench import (
    WD_APP_TOKEN,
    WD_CUSTOMER_SERVICE_TABLE,
    WD_IFREIGHT_TABLE,
    WD_ORDER_TABLE,
    WD_PRICING_TABLE,
)

TABLES: dict[str, tuple[str, str]] = {
    "crm": (WD_CUSTOMER_SERVICE_TABLE, "商机主表"),
    "order": (WD_ORDER_TABLE, "订单表"),
    "pricing": (WD_PRICING_TABLE, "航线报价"),
    "ifreight": (WD_IFREIGHT_TABLE, "iFreight 数据"),
}

# 飞书字段基础类型 → 插件的 bizType。数字(2)按 property.formatter 细分。
BASE_TYPE_TO_BIZ: dict[int, str] = {
    1: "Text",
    2: "Number",
    3: "SingleSelect",
    4: "MultiSelect",
    5: "DateTime",
    7: "Checkbox",
    11: "User",
    13: "Phone",
    15: "Url",
    17: "Attachment",
    18: "SingleLink",
    19: "Lookup",
    20: "Formula",
    21: "DuplexLink",
    22: "Location",
    23: "GroupChat",
    24: "Stage",
    1001: "CreatedTime",
    1002: "ModifiedTime",
    1003: "CreatedUser",
    1004: "ModifiedUser",
    1005: "AutoNumber",
    3001: "Button",
}

# 数字里带 formatter 的几个，对应插件自己的 bizType
NUMBER_FORMATTER_BIZ = {"currency": "Currency", "progress": "Progress", "rating": "Rating"}

# 只读：查找引用/公式/系统四件套/自动编号，外加按钮（压根读不到写不了）
READONLY_BASE_TYPES = {19, 20, 1001, 1002, 1003, 1004, 1005, 3001}
# 按钮连读都不给
UNREADABLE_BASE_TYPES = {3001}
# 选项值只对单选/多选有意义
OPTION_BASE_TYPES = {3, 4}


def biz_type(field: dict) -> str:
    base = int(field.get("type") or 0)
    if base == 2:
        fmt = str((field.get("property") or {}).get("formatter") or "").lower()
        if fmt in NUMBER_FORMATTER_BIZ:
            return NUMBER_FORMATTER_BIZ[fmt]
    return BASE_TYPE_TO_BIZ.get(base, "Text")


def norm_field(field: dict) -> dict:
    base = int(field.get("type") or 0)
    out: dict[str, object] = {
        "id": field.get("field_id"),
        "name": field.get("field_name"),
        "type": base,
        "bizType": biz_type(field),
        "readable": base not in UNREADABLE_BASE_TYPES,
        "writeable": base not in READONLY_BASE_TYPES,
    }
    if base in OPTION_BASE_TYPES:
        options = (field.get("property") or {}).get("options") or []
        out["enumValues"] = [o.get("name") for o in options if o.get("name")]
    return out


async def dump_table(token: str, table_id: str) -> list[dict]:
    base = settings.LARK_BASE_URL.rstrip("/")
    items: list[dict] = []
    page_token: str | None = None
    while True:
        params: dict[str, object] = {"page_size": 200}
        if page_token:
            params["page_token"] = page_token
        data = await _http_json(
            "GET",
            f"{base}/bitable/v1/apps/{WD_APP_TOKEN}/tables/{table_id}/fields",
            token,
            params=params,
        )
        payload = data.get("data") or {}
        items.extend(payload.get("items") or [])
        if not payload.get("has_more"):
            return items
        page_token = payload.get("page_token")


async def main() -> None:
    if len(sys.argv) < 2:
        print("用法: dump_plugin_fields.py <输出目录>", file=sys.stderr)
        sys.exit(2)
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)

    redis = aioredis.from_url(settings.REDIS_URI, decode_responses=True)
    try:
        token = await _get_tenant_access_token(redis)
        for alias, (table_id, label) in TABLES.items():
            try:
                raw = await dump_table(token, table_id)
            except httpx.HTTPStatusError as exc:
                print(f"✗ {alias} {label} 失败：{exc.response.text[:300]}")
                continue
            fields = [norm_field(f) for f in raw]
            path = outdir / f"{alias}.fields.json"
            path.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✓ {alias:<9}{label:<12}{len(fields):>4} 个字段 → {path}")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
