"""解析并写入已验收的 16888 车型月销量。

重放命令：python -m scheduler.runner --chain style --period YYYY-MM --from-csv
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime
from io import StringIO
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from base_infra.db import engine

SOURCE_TAG = "16888_style"
SOURCE_URL = "https://xl.16888.com/style.html"


def data_dir() -> Path:
    """返回 style 数据目录。"""
    return Path(__file__).resolve().parents[0] / "crawl_16888_style" / "data"


def parse_month(month_text: str) -> datetime.date:
    """将 YYYY-MM 文本转为该月第一天。"""
    return datetime.strptime(month_text.strip(), "%Y-%m").date().replace(day=1)


def parse_int(value: str | None) -> int | None:
    """解析整数文本；空值返回 None。"""
    if value is None:
        return None
    raw = value.strip().replace(",", "")
    if not raw:
        return None
    return int(raw)


def build_row_hash(month: str, model_name: str, manufacturer_name: str) -> str:
    """构造 ODS 幂等哈希。"""
    payload = f"{SOURCE_TAG}|{month}|{model_name}|{manufacturer_name}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def read_rows(csv_file: Path, *, raw_csv: bytes | None = None) -> Iterable[dict[str, object]]:
    """读取单个 CSV，转换为入库参数。"""
    fetch_time = datetime.fromtimestamp(csv_file.stat().st_mtime)
    with (csv_file.open("r", encoding="utf-8-sig", newline="") if raw_csv is None
          else StringIO(raw_csv.decode("utf-8-sig"), newline="")) as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            month = str(row["month"]).strip()
            model_name = str(row["model_name"]).strip()
            manufacturer_name = str(row["manufacturer"]).strip()
            yield {
                "source": SOURCE_TAG,
                "stat_month": parse_month(month),
                "rank_no": parse_int(row.get("rank")),
                "model_name_raw": model_name,
                "manufacturer_name_raw": manufacturer_name,
                "price_range_raw": str(row.get("price_range") or "").strip() or None,
                "sales_volume": parse_int(row.get("sales_volume")),
                "source_file": str(csv_file),
                "source_url": SOURCE_URL,
                "row_hash": build_row_hash(month, model_name, manufacturer_name),
                "fetch_time": fetch_time,
            }


def upsert_rows(rows: list[dict[str, object]], *, connection: Connection | None = None) -> int:
    """批量 UPSERT 到 ods_sales_monthly_model。"""
    if not rows:
        return 0

    sql = text("""
        INSERT INTO ods_sales_monthly_model
            (source, stat_month, rank_no, model_name_raw, manufacturer_name_raw,
             price_range_raw, sales_volume, source_file, source_url, row_hash, fetch_time)
        VALUES
            (:source, :stat_month, :rank_no, :model_name_raw, :manufacturer_name_raw,
             :price_range_raw, :sales_volume, :source_file, :source_url, :row_hash, :fetch_time)
        ON DUPLICATE KEY UPDATE
            rank_no               = VALUES(rank_no),
            price_range_raw       = VALUES(price_range_raw),
            sales_volume          = VALUES(sales_volume),
            source_file           = VALUES(source_file),
            source_url            = VALUES(source_url),
            row_hash              = VALUES(row_hash),
            fetch_time            = VALUES(fetch_time)
    """)
    with (engine.begin() if connection is None else nullcontext(connection)) as conn:
        conn.execute(sql, rows)
    return len(rows)


if __name__ == "__main__":
    from scheduler.jobs.load_ods_job import replay_cli

    raise SystemExit(replay_cli("style"))
