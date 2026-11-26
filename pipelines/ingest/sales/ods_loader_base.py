"""16888 车型/城市销量 ODS 加载器的共用骨架。

两个 load_16888_*_to_ods 模块的读取、哈希与 UPSERT 流程完全一致，
差别仅在：数据目录、来源标签/URL、目标表、业务键列与单行字段映射。
本模块把这套相同流程参数化收口为 OdsLoader，具体 loader 只保留
data_dir、parse_month、_build_row 等差异化定义。

OdsLoader 持有 loader 模块对象并在每次调用时动态读取其 data_dir/engine，
这样离线测试对 loader.data_dir、loader.engine 的 monkeypatch 仍然生效。
"""

from __future__ import annotations

import csv
import hashlib
from contextlib import nullcontext
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


def parse_int(value: str | None) -> int | None:
    """解析整数文本；None 或空白返回 None，去掉千分位逗号。"""
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    return int(raw)


class OdsLoader:
    """绑定 loader 模块与表结构映射规则的单表 ODS 加载器。"""

    def __init__(
        self,
        loader_module,
        *,
        table: str,
        source_tag: str,
        source_url: str,
        key_column: str,
        build_row: Callable[[dict[str, str]], dict[str, Any]],
        extra_update_columns: Iterable[str] = (),
    ):
        # 不直接缓存 data_dir/engine，确保测试 monkeypatch 模块属性后能读到新值
        self._loader = loader_module
        self.table = table
        self.source_tag = source_tag
        self.source_url = source_url
        self.key_column = key_column
        self._build_row = build_row
        # 业务键列不参与 ON DUPLICATE KEY UPDATE，其余内容列都需要更新
        self._update_columns = tuple(extra_update_columns)

    @property
    def engine(self):
        return self._loader.engine

    def build_row_hash(self, month: str, model_name: str, key_value: str) -> str:
        """构造 ODS 幂等哈希（与历史 md5 口径保持一致）。"""
        payload = f"{self.source_tag}|{month}|{model_name}|{key_value}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def read_rows(self, csv_file: Path, *, raw_csv: bytes | None = None) -> Iterable[dict[str, Any]]:
        """读取单个 CSV，转换为入库参数；raw_csv 给定时直接重放已验收字节。"""
        fetch_time = datetime.fromtimestamp(csv_file.stat().st_mtime)
        with (csv_file.open("r", encoding="utf-8-sig", newline="") if raw_csv is None
              else StringIO(raw_csv.decode("utf-8-sig"), newline="")) as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                record = self._build_row(row)
                month = str(row["month"]).strip()
                model_name = str(row["model_name"]).strip()
                key_value = str(row[self.key_column]).strip()
                record.update({
                    "source": self.source_tag,
                    "source_file": str(csv_file),
                    "source_url": self.source_url,
                    "row_hash": self.build_row_hash(month, model_name, key_value),
                    "fetch_time": fetch_time,
                })
                yield record

    def upsert_rows(self, rows: list[dict[str, Any]], *, connection: Connection | None = None) -> int:
        """批量 UPSERT 到目标 ODS 表；可复用外部事务连接。"""
        if not rows:
            return 0

        # 业务键（source/stat_month/车型/键列）由唯一约束兜底，不放进 UPDATE 子句
        update_columns = [
            "rank_no", "price_range_raw", "sales_volume",
            "source_file", "source_url", "row_hash", "fetch_time",
            *self._update_columns,
        ]
        columns = list(rows[0].keys())
        sql = text(f"""
            INSERT INTO {self.table} ({', '.join(columns)})
            VALUES ({', '.join(f':{col}' for col in columns)})
            ON DUPLICATE KEY UPDATE
                {', '.join(f'{col}=VALUES({col})' for col in update_columns)}
        """)
        with (self.engine.begin() if connection is None else nullcontext(connection)) as conn:
            conn.execute(sql, rows)
        return len(rows)
