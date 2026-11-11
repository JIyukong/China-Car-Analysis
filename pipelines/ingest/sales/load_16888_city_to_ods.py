"""将 16888 车型城市月销量 CSV 批量写入 ods_sales_monthly_model_city。

用法:
    python pipelines/ingest/sales/load_16888_city_to_ods.py
    python pipelines/ingest/sales/load_16888_city_to_ods.py --file pipelines/ingest/sales/crawl_16888_city/data/city_2026-05.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

SOURCE_TAG = "16888_city"
SOURCE_URL = "https://xl.16888.com/city.html"


def data_dir() -> Path:
    """返回 city 数据目录。"""
    return Path(__file__).resolve().parents[0] / "crawl_16888_city" / "data"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="16888 车型城市月销量 CSV -> ods_sales_monthly_model_city")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="指定单个 CSV 文件路径；不指定则批量处理 data 目录下全部 city_*.csv",
    )
    return parser.parse_args()


def discover_files(file_path: Path | None) -> list[Path]:
    """发现待处理 CSV 文件列表。"""
    if file_path is not None:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return [file_path]

    files = sorted(data_dir().glob("city_*.csv"))
    if not files:
        raise FileNotFoundError(f"未找到任何 city_*.csv 文件: {data_dir()}")
    return files


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


def build_row_hash(month: str, model_name: str, city_name: str) -> str:
    """构造 ODS 幂等哈希。"""
    payload = f"{SOURCE_TAG}|{month}|{model_name}|{city_name}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def read_rows(csv_file: Path) -> Iterable[dict[str, object]]:
    """读取单个 CSV，转换为入库参数。"""
    fetch_time = datetime.fromtimestamp(csv_file.stat().st_mtime)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            month = str(row["month"]).strip()
            model_name = str(row["model_name"]).strip()
            city_name = str(row["city"]).strip()
            yield {
                "source": SOURCE_TAG,
                "stat_month": parse_month(month),
                "rank_no": parse_int(row.get("rank")),
                "model_name_raw": model_name,
                "city_name_raw": city_name,
                "level_raw": str(row.get("level") or "").strip() or None,
                "price_range_raw": str(row.get("price_range") or "").strip() or None,
                "sales_volume": parse_int(row.get("sales_volume")),
                "source_file": str(csv_file),
                "source_url": SOURCE_URL,
                "row_hash": build_row_hash(month, model_name, city_name),
                "fetch_time": fetch_time,
            }


def upsert_rows(rows: list[dict[str, object]]) -> int:
    """批量 UPSERT 到 ods_sales_monthly_model_city。"""
    if not rows:
        return 0

    sql = text("""
        INSERT INTO ods_sales_monthly_model_city
            (source, stat_month, rank_no, model_name_raw, city_name_raw,
             level_raw, price_range_raw, sales_volume, source_file,
             source_url, row_hash, fetch_time)
        VALUES
            (:source, :stat_month, :rank_no, :model_name_raw, :city_name_raw,
             :level_raw, :price_range_raw, :sales_volume, :source_file,
             :source_url, :row_hash, :fetch_time)
        ON DUPLICATE KEY UPDATE
            rank_no               = VALUES(rank_no),
            level_raw             = VALUES(level_raw),
            price_range_raw       = VALUES(price_range_raw),
            sales_volume          = VALUES(sales_volume),
            source_file           = VALUES(source_file),
            source_url            = VALUES(source_url),
            row_hash              = VALUES(row_hash),
            fetch_time            = VALUES(fetch_time)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def main(file_path: Path | None = None) -> None:
    """脚本入口。

    :param file_path: 指定单个 CSV；None 时批量处理 data 目录全部 city_*.csv。
        调度器按月调用时传目标月 CSV，避免每月重放全部历史文件。
    """
    files = discover_files(file_path)
    logger.info("发现 %d 个 city CSV 文件待入库", len(files))

    total_rows = 0
    for csv_file in files:
        rows = list(read_rows(csv_file))
        inserted = upsert_rows(rows)
        total_rows += inserted
        logger.info("已处理 %s，共 %d 行", csv_file.name, inserted)

    print(f"city CSV 入库完成，共处理 {len(files)} 个文件，写入/更新 {total_rows} 行。")


if __name__ == "__main__":
    main(parse_args().file)
