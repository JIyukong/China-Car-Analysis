"""将 16888 车型月销量 CSV 批量写入 ods_sales_monthly_model。

用法:
    python pipelines/ingest/sales/load_16888_style_to_ods.py
    python pipelines/ingest/sales/load_16888_style_to_ods.py --file pipelines/ingest/sales/crawl_16888_style/data/style_2026-03.csv
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

SOURCE_TAG = "16888_style"
SOURCE_URL = "https://xl.16888.com/style.html"


def data_dir() -> Path:
    """返回 style 数据目录。"""
    return Path(__file__).resolve().parents[0] / "crawl_16888_style" / "data"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="16888 车型月销量 CSV -> ods_sales_monthly_model")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="指定单个 CSV 文件路径；不指定则批量处理 data 目录下全部 style_*.csv",
    )
    return parser.parse_args()


def discover_files(file_path: Path | None) -> list[Path]:
    """发现待处理 CSV 文件列表。"""
    if file_path is not None:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        return [file_path]

    files = sorted(data_dir().glob("style_*.csv"))
    if not files:
        raise FileNotFoundError(f"未找到任何 style_*.csv 文件: {data_dir()}")
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


def build_row_hash(month: str, model_name: str, manufacturer_name: str) -> str:
    """构造 ODS 幂等哈希。"""
    payload = f"{SOURCE_TAG}|{month}|{model_name}|{manufacturer_name}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def read_rows(csv_file: Path) -> Iterable[dict[str, object]]:
    """读取单个 CSV，转换为入库参数。"""
    fetch_time = datetime.fromtimestamp(csv_file.stat().st_mtime)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
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


def upsert_rows(rows: list[dict[str, object]]) -> int:
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
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def main(file_path: Path | None = None, *, expected_month: str | None = None) -> None:
    """脚本入口。

    :param file_path: 指定单个 CSV；None 时批量处理 data 目录全部 style_*.csv。
    :param expected_month: 指定目标月时，写入前检查 CSV 非空且每行属于该月。
    """
    month_start = parse_month(expected_month) if expected_month is not None else None
    files = discover_files(file_path)
    logger.info("发现 %d 个 style CSV 文件待入库", len(files))

    total_rows = 0
    for csv_file in files:
        rows = list(read_rows(csv_file))
        if month_start is not None and (not rows or any(row["stat_month"] != month_start for row in rows)):
            raise ValueError(f"{csv_file.name} 没有数据或包含非目标月份的行（目标月 {expected_month}）")
        inserted = upsert_rows(rows)
        total_rows += inserted
        logger.info("已处理 %s，共 %d 行", csv_file.name, inserted)

    print(f"style CSV 入库完成，共处理 {len(files)} 个文件，写入/更新 {total_rows} 行。")


if __name__ == "__main__":
    main(parse_args().file)
