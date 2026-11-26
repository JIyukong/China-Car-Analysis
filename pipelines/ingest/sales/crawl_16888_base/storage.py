"""车型/城市爬虫共用的断点续传与月份文件落地。

历史上 crawl_style.py 与 crawl_city.py 各有一份仅文件名前缀
（style_ / city_）和数据目录不同的实现，这里参数化收口为单一类，
写入编码、表头策略、progress.json 结构与汇总格式均保持原行为。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

# progress.json 缺失或损坏时返回的初始结构
EMPTY_PROGRESS = {"completed_months": [], "months_in_progress": {}}


class CrawlStorage:
    """绑定一个数据目录、文件名前缀和 CSV 列顺序的落地器。"""

    def __init__(self, data_dir: Path, prefix: str, csv_columns: list[str]):
        self._data_dir = Path(data_dir)
        self._prefix = prefix
        self._csv_columns = csv_columns
        self._data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _progress_path(self) -> Path:
        return self._data_dir / "progress.json"

    def _month_path(self, suffix: str, month_id: str) -> Path:
        return self._data_dir / f"{self._prefix}_{month_id}.{suffix}"

    def is_month_locally_complete(self, month_id: str) -> bool:
        """CSV + JSON 双文件均存在即视为该月份本地已完成。"""
        return self._month_path("csv", month_id).exists() and self._month_path("json", month_id).exists()

    def load_progress(self) -> dict[str, Any]:
        """读取断点进度；文件不存在或损坏时返回空结构。"""
        path = self._progress_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print("[WARN] progress.json 解析失败，从头开始。")
        return {"completed_months": [], "months_in_progress": {}}

    def save_progress(self, progress: dict[str, Any]) -> None:
        """落地断点进度（覆盖写），并刷新 last_run 时间戳。"""
        progress["last_run"] = datetime.now().isoformat(timespec="seconds")
        self._progress_path().write_text(
            json.dumps(progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_month_csv(self, month_id: str, records: list[dict[str, Any]]) -> Path:
        """追加写月份 CSV；文件不存在时先写表头，续传时不重复写。"""
        csv_path = self._month_path("csv", month_id)
        write_header = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8-sig") as f:  # utf-8-sig 兼容 Excel
            writer = csv.DictWriter(f, fieldnames=self._csv_columns)
            if write_header:
                writer.writeheader()
            for rec in records:
                writer.writerow({**rec, "month": month_id})
        return csv_path

    def save_month_json(
        self,
        month_id: str,
        records: list[dict[str, Any]],
        total_count: int | None,
        page_count: int,
    ) -> Path:
        """月份完成后覆盖写 JSON 汇总。"""
        json_path = self._month_path("json", month_id)
        payload = {
            "month": month_id,
            "total_count": total_count,
            "page_count": page_count,
            "record_count": len(records),
            "crawl_time": datetime.now().isoformat(timespec="seconds"),
            "records": records,
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return json_path

    def save_summary(self, source_url: str, months_summary: list[dict[str, Any]]) -> Path:
        """保存本次运行的全月份记录数汇总。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_path = self._data_dir / f"{self._prefix}_summary_{timestamp}.json"
        payload = {
            "crawl_time": datetime.now().isoformat(timespec="seconds"),
            "source_url": source_url,
            "months": months_summary,
            "total_records": sum(m["record_count"] for m in months_summary),
        }
        summary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  → 汇总: {summary_path}")
        return summary_path
