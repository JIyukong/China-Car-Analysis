"""把已校验的 CSV 装进连接私有的暂存表，再增量更新正式维度。"""

from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "manual"
Dimension = Literal["dim_area", "dim_energy_vehicle", "dim_model_ads_manual"]


def upsert_dimension(conn: Connection, dimension: Dimension, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("CSV 中没有可导入的记录")

    sql = (SQL_DIR / f"refresh_{dimension}.sql").read_text(encoding="utf-8")
    statements = [
        part.strip()
        for part in "\n".join(line for line in sql.splitlines() if not line.startswith("--")).split(";")
        if part.strip()
    ]
    table = f"tmp_manual_{dimension}"
    columns = ", ".join(rows[0])
    values = ", ".join(f":{column}" for column in rows[0])

    conn.execute(text(f"CREATE TEMPORARY TABLE {table} LIKE stg_{dimension}"))
    try:
        conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), rows)
        for statement in statements:
            conn.execute(text(statement))
    finally:
        # 临时表属于连接，归还池前显式释放。TEMPORARY 不会删除同名正式表。
        conn.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {table}"))
