"""补齐 iFreight 表缺的 5 列（妙搭有、Fast 没有），并写字段中文名。

幂等：列已存在就跳过。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/migrate_ifreight_columns.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import async_db_session

COLUMNS = [
    ("estimated_departure_time", "datetime NULL", "预计起运时间"),
    ("sales_person", "varchar(255) NULL", "业务员"),
    ("opportunity_person", "varchar(255) NULL", "商机负责人"),
    ("foreign_agent", "varchar(255) NULL", "国外代理"),
    ("booking_agent", "varchar(255) NULL", "订舱代理"),
]


async def main() -> None:
    async with async_db_session() as session:
        existing = {
            row[0]
            for row in (await session.execute(text("SHOW COLUMNS FROM biz_ifreight"))).all()
        }
        for col, ddl, label in COLUMNS:
            if col in existing:
                print(f"  跳过 {col}（已存在）")
            else:
                await session.execute(
                    text(f"ALTER TABLE biz_ifreight ADD COLUMN `{col}` {ddl}")
                )
                print(f"  新增 {col}")
            await session.execute(
                text(
                    "INSERT INTO sys_field_label (tbl, col, label) "
                    "SELECT 'biz_ifreight', :c, :l FROM DUAL WHERE NOT EXISTS ("
                    " SELECT 1 FROM sys_field_label WHERE tbl='biz_ifreight' AND col=:c)"
                ),
                {"c": col, "l": label},
            )
        await session.commit()
    print("完成")


if __name__ == "__main__":
    asyncio.run(main())
