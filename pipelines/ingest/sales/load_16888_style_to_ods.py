"""解析并写入已验收的 16888 车型月销量。

共用的读取、哈希与 UPSERT 流程见 ods_loader_base.OdsLoader；
本模块只保留 style 链的数据目录、月份解析与单行字段映射。

重放命令：python -m scheduler.runner --chain style --period YYYY-MM --from-csv
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from base_infra.db import engine  # noqa: F401  模块属性，供 monthly_source 与测试 monkeypatch 使用
from pipelines.ingest.sales.ods_loader_base import OdsLoader, parse_int

SOURCE_TAG = "16888_style"
SOURCE_URL = "https://xl.16888.com/style.html"

_THIS_MODULE = sys.modules[__name__]
_DATA_DIR = Path(__file__).resolve().parent / "crawl_16888_style" / "data"


def data_dir() -> Path:
    """返回 style 数据目录。"""
    return _DATA_DIR


def parse_month(month_text: str) -> datetime.date:
    """将 YYYY-MM 文本转为该月第一天。"""
    return datetime.strptime(month_text.strip(), "%Y-%m").date().replace(day=1)


def _build_row(row: dict) -> dict:
    """把一行原始 CSV 映射为 ods_sales_monthly_model 的内容字段。"""
    return {
        "stat_month": parse_month(str(row["month"])),
        "rank_no": parse_int(row.get("rank")),
        "model_name_raw": str(row["model_name"]).strip(),
        "manufacturer_name_raw": str(row["manufacturer"]).strip(),
        "price_range_raw": str(row.get("price_range") or "").strip() or None,
        "sales_volume": parse_int(row.get("sales_volume")),
    }


_loader = OdsLoader(
    _THIS_MODULE,
    table="ods_sales_monthly_model",
    source_tag=SOURCE_TAG,
    source_url=SOURCE_URL,
    key_column="manufacturer",
    build_row=_build_row,
)


def read_rows(csv_file: Path, *, raw_csv: bytes | None = None):
    """读取单个 CSV，转换为入库参数。"""
    return _loader.read_rows(csv_file, raw_csv=raw_csv)


def upsert_rows(rows: list[dict], *, connection=None) -> int:
    """批量 UPSERT 到 ods_sales_monthly_model。"""
    return _loader.upsert_rows(rows, connection=connection)


if __name__ == "__main__":
    from scheduler.jobs.load_ods_job import replay_cli

    raise SystemExit(replay_cli("style"))
