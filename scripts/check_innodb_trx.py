"""查询当前活跃 InnoDB 事务。 """

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine


def main() -> None:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT trx_id, trx_state, trx_started, "
            "SUBSTRING(trx_query, 1, 80) FROM information_schema.INNODB_TRX"
        )).fetchall()
        print(f"活跃事务数: {len(rows)}")
        for r in rows:
            print(r)


if __name__ == "__main__":
    main()
