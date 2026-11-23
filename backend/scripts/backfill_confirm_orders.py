"""回填「是否拿到货=是 但还没有订单」的商机：按 Java 引擎的确认订单契约补建订单。

幂等：已经有订单（按 biz_link「订单表」或 opportunity_no）的商机直接跳过。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/backfill_confirm_orders.py           # 只看
    PYTHONPATH=$PWD .venv/bin/python scripts/backfill_confirm_orders.py --apply   # 真建
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.database import async_db_session
from app.plugin.module_weida.service import _ensure_order


async def main() -> None:
    apply = "--apply" in sys.argv
    async with async_db_session() as session:
        rows = (
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
        print(f"待回填商机 {len(rows)} 条")
        for row in rows:
            print(f"  #{row['id']} {row['opportunity_no']} {row['client_ref_no']}")
            if apply:
                order_id = await _ensure_order(session, row)
                print(f"    -> 订单 #{order_id}")
        if apply:
            await session.commit()
    print("完成" if apply else "只读检查，加 --apply 才会建单")


if __name__ == "__main__":
    asyncio.run(main())
