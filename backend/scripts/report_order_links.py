"""只读：列出「商机 ↔ 订单」关联缺口（不改任何数据）。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/report_order_links.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import async_db_session


async def main() -> None:
    async with async_db_session() as session:
        orphan_orders = (
            await session.execute(
                text(
                    "SELECT o.id, o.order_no, o.opportunity_no FROM biz_order o "
                    "WHERE o.is_deleted = 0 AND NOT EXISTS ("
                    "  SELECT 1 FROM biz_link l WHERE l.to_table = 'order' "
                    "  AND l.to_id = o.id AND l.field = '订单表') ORDER BY o.id"
                )
            )
        ).mappings().all()
        print(f"订单无关联：{len(orphan_orders)} 张")
        for row in orphan_orders:
            print(f"  订单#{row['id']} {row['order_no'] or ''} 商机编号={row['opportunity_no'] or '（空）'}")

        pending = (
            await session.execute(
                text(
                    "SELECT o.id, o.opportunity_no, o.client_ref_no FROM biz_opportunity o "
                    "WHERE o.is_deleted = 0 AND o.is_goods_received = '是' "
                    "AND NOT EXISTS (SELECT 1 FROM biz_link l WHERE l.from_table = 'opportunity' "
                    "  AND l.from_id = o.id AND l.to_table = 'order' AND l.field = '订单表') "
                    "ORDER BY o.id"
                )
            )
        ).mappings().all()
        print(f"「拿到货=是」但无订单：{len(pending)} 条商机")
        for row in pending:
            print(f"  商机#{row['id']} {row['opportunity_no'] or ''} {row['client_ref_no'] or ''}")


if __name__ == "__main__":
    asyncio.run(main())
