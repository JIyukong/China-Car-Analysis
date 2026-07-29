"""快速探测 ods_stock_daily / ods_stock_finance 实际列结构。"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine


def main() -> None:
    for table in ("ods_stock_daily", "ods_stock_finance", "dwd_stock_daily", "dwd_stock_finance"):
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:t "
                "ORDER BY ORDINAL_POSITION"
            ), {"t": table}).fetchall()
        print(f"\n== {table} ==")
        for name, typ in rows:
            print(f"  {name}: {typ}")


if __name__ == "__main__":
    main()
