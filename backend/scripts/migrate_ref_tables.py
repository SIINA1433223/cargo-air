"""建「基础资料」两张表：weida_ref_option（选项+编码）与 weida_prompt（提示词）。

幂等：表已存在就跳过。

用法（cwd=backend）：
    PYTHONPATH=$PWD .venv/bin/python scripts/migrate_ref_tables.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import async_db_session

DDL = [
    """
    CREATE TABLE IF NOT EXISTS weida_ref_option (
      id INT AUTO_INCREMENT PRIMARY KEY,
      kind VARCHAR(64) NOT NULL,
      value VARCHAR(255) NOT NULL,
      code VARCHAR(64) NULL,
      extra JSON NULL,
      sort INT NOT NULL DEFAULT 0,
      update_time DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_kind_value (kind, value)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS weida_prompt (
      id INT AUTO_INCREMENT PRIMARY KEY,
      kind VARCHAR(32) NOT NULL,
      customer VARCHAR(255) NULL,
      type_name VARCHAR(255) NOT NULL,
      prompt MEDIUMTEXT NULL,
      update_time DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_kind_type_customer (kind, type_name, customer)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


async def main() -> None:
    async with async_db_session() as session:
        for ddl in DDL:
            await session.execute(text(ddl))
        await session.commit()
    print("完成")


if __name__ == "__main__":
    asyncio.run(main())
