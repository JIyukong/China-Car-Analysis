"""车型大类维度种子脚本：预置 dim_vehicle_type。

预置数据：
    大类键  大类名称
    1       SUV
    2       轿车
    3       MPV
    4       其他
    5       占位

用法:
    python pipelines/dim/seed_dim_vehicle_type.py
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

VEHICLE_TYPES = [
    {"type_code": "1", "type_name": "SUV"},
    {"type_code": "2", "type_name": "轿车"},
    {"type_code": "3", "type_name": "MPV"},
    {"type_code": "4", "type_name": "其他"},
    {"type_code": "5", "type_name": "占位"},
]


def main() -> None:
    """幂等写入 dim_vehicle_type 种子数据。"""
    upsert_sql = text("""
        INSERT INTO dim_vehicle_type (type_code, type_name)
        VALUES (:type_code, :type_name)
        ON DUPLICATE KEY UPDATE
            type_name = VALUES(type_name)
    """)

    with engine.begin() as conn:
        for item in VEHICLE_TYPES:
            conn.execute(upsert_sql, {
                "type_code": item["type_code"],
                "type_name": item["type_name"],
            })

    logger.info("dim_vehicle_type 处理 %d 条", len(VEHICLE_TYPES))
    print(f"dim_vehicle_type 处理 {len(VEHICLE_TYPES)} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口需要兜底打印
        logger.exception("车型大类种子写入失败: %s", exc)
        print(f"[FATAL] 车型大类种子写入失败: {exc}")
        sys.exit(1)
