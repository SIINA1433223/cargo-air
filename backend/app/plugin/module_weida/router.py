"""威达工作台的只读接口（第一版：列表）。

数据来自本地 `biz_*` 表（由飞书一次性导入），字段中文名走 `sys_field_label` 字典。
派生列（公式/查找引用）不落库，接口里按需现算。
"""

from __future__ import annotations

import json

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.common.response import SuccessResponse, ResponseSchema
from app.core.database import async_db_session

WeidaRouter = APIRouter(prefix="/weida", tags=["威达工作台"])

# 工作台 → 主表。三个台子共用同一套列表逻辑。
WORKBENCH_TABLE = {
    "customer_service": "biz_opportunity",
    "pricing": "biz_opportunity",
    "domestic_ops": "biz_order",
}


@WeidaRouter.get(
    "/{role}/list",
    summary="工作台列表",
    response_model=ResponseSchema[dict],
)
async def workbench_list(
    role: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> Any:
    """按工作台返回主表的一页记录（中文键 + 原始值）。

    先给最小可用版：分页 + 中文标签；筛选/排序/抽屉后续再加。
    """
    table = WORKBENCH_TABLE.get(role)
    if not table:
        return SuccessResponse(data={"items": [], "total": 0, "labels": {}})

    async with async_db_session() as session:
        total = (
            await session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE is_deleted = 0"))
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    f"SELECT * FROM {table} WHERE is_deleted = 0 "
                    "ORDER BY id DESC LIMIT :limit OFFSET :offset"
                ),
                {"limit": page_size, "offset": (page - 1) * page_size},
            )
        ).mappings().all()
        labels = dict(
            (
                await session.execute(
                    text("SELECT col, label FROM sys_field_label WHERE tbl = :t"),
                    {"t": table},
                )
            ).all()
        )

    def clean(value):
        """把 mirror 里那种双重编码的脏值还原。

        现象：JSON 列里存的是 `"{\"...\"}"`（被 json.dumps 了两次），或者 PG 文本数组
        `{(1859891022398619),(1859891022403835)}`。前端拿到这种值没法用。
        """
        for _ in range(2):
            if isinstance(value, str):
                t = value.strip()
                if not t:
                    return value
                if (t.startswith('{') and t.endswith('}')) or t.startswith('[') or t.startswith('"'):
                    try:
                        value = json.loads(t)
                        continue
                    except Exception:
                        if t.startswith('{') and t.endswith('}'):
                            body = t[1:-1]
                            if body and not ':' in body:
                                return [x.strip().strip('"') for x in body.split(',') if x.strip()]
                        return value
            break
        return value

    items = []
    for r in rows:
        fields = {}
        for col, value in r.items():
            if col in ("id", "is_deleted", "create_by", "update_by"):
                continue
            fields[col] = {
                "label": labels.get(col, col),
                "value": clean(value) if not hasattr(value, "isoformat") else value.isoformat(),
            }
        items.append({"id": r["id"], "fields": fields})
    return SuccessResponse(
        data={"items": items, "total": total, "page": page, "pageSize": page_size}
    )


@WeidaRouter.get(
    "/{role}/stats",
    summary="工作台仪表盘统计",
    response_model=ResponseSchema[dict],
)
async def workbench_stats(role: str) -> Any:
    """三个仪表盘要的那几个数（先给总量与关键分档，图后面再上）。"""
    if role == "customer_service":
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_goods_received = '是' THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN is_goods_received = '否' THEN 1 ELSE 0 END) AS pending
        FROM biz_opportunity WHERE is_deleted = 0
        """
        title = "商机转化"
        labels = {"total": "商机总数", "done": "已拿到货", "pending": "未拿到货"}
    elif role == "domestic_ops":
        # 单票状态是**派生**的（妙搭同一口径）：勾「发送预报」=已完成，只勾「交接」=已交接，都没勾=未交接
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN IFNULL(send_forecast,0)=1 THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN IFNULL(send_forecast,0)=0
                         AND IFNULL(handover_domestic_operation,0)=1 THEN 1 ELSE 0 END) AS handed,
               SUM(CASE WHEN IFNULL(handover_domestic_operation,0)=0
                         AND IFNULL(send_forecast,0)=0 THEN 1 ELSE 0 END) AS pending
        FROM biz_order WHERE is_deleted = 0
        """
        title = "操作票量"
        labels = {"total": "订单总数", "done": "已完成", "handed": "已交接", "pending": "未交接"}
    else:
        sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN quotation_status = '已报价' THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN quotation_status IS NULL OR quotation_status <> '已报价' THEN 1 ELSE 0 END) AS pending
        FROM biz_pricing WHERE is_deleted = 0
        """
        title = "航线报价"
        labels = {"total": "报价总数", "done": "已报价", "pending": "未报价/其他"}

    async with async_db_session() as session:
        try:
            row = (await session.execute(text(sql))).mappings().first()
            data = {k: int(row[k] or 0) for k in row.keys()} if row else {}
        except Exception:
            data = {"total": 0, "done": 0, "pending": 0}
    return SuccessResponse(data={"title": title, "labels": labels, "values": data})
