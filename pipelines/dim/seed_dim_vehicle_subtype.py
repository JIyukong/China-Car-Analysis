"""车型细分类型维度种子脚本：预置 dim_vehicle_subtype。

预置数据（10 条，大类已归一化到 dim_vehicle_type.type_name）：
    细分键  细分名称      大类
    1       MPV          MPV
    2       SUV          SUV
    3       不限          其他（源数据为 other）
    4       中型车        轿车
    5       中大型车      轿车
    6       大型车        轿车
    7       小型车        轿车
    8       微型车        轿车
    9       紧凑型车      轿车
    10      跑车          轿车

前提依赖：
    执行本脚本前，dim_vehicle_type 必须已写入 5 条大类数据
    （即先运行 seed_dim_vehicle_type.py）。

用法:
    python pipelines/dim/seed_dim_vehicle_subtype.py
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 细分类型种子数据：subtype_code/subtype_name 对应用户源数据，
# type_name 为归一化后的大类名称（用于关联 dim_vehicle_type.id）
VEHICLE_SUBTYPES = [
    {"subtype_code": "1",  "subtype_name": "MPV",      "type_name": "MPV"},
    {"subtype_code": "2",  "subtype_name": "SUV",      "type_name": "SUV"},
    {"subtype_code": "3",  "subtype_name": "不限",      "type_name": "其他"},   # other → 其他
    {"subtype_code": "4",  "subtype_name": "中型车",    "type_name": "轿车"},
    {"subtype_code": "5",  "subtype_name": "中大型车",  "type_name": "轿车"},
    {"subtype_code": "6",  "subtype_name": "大型车",    "type_name": "轿车"},
    {"subtype_code": "7",  "subtype_name": "小型车",    "type_name": "轿车"},
    {"subtype_code": "8",  "subtype_name": "微型车",    "type_name": "轿车"},
    {"subtype_code": "9",  "subtype_name": "紧凑型车",  "type_name": "轿车"},
    {"subtype_code": "10", "subtype_name": "跑车",      "type_name": "轿车"},
]


def main() -> None:
    """幂等写入 dim_vehicle_subtype 种子数据。

    通过子查询从 dim_vehicle_type 解析 vehicle_type_id，
    若大类不存在则该行不写入（INSERT ... SELECT 无匹配行时静默跳过）。
    """
    upsert_sql = text("""
        INSERT INTO dim_vehicle_subtype (subtype_code, subtype_name, vehicle_type_id)
        SELECT :subtype_code, :subtype_name, vt.id
        FROM dim_vehicle_type vt
        WHERE vt.type_name = :type_name
        ON DUPLICATE KEY UPDATE
            subtype_name    = VALUES(subtype_name),
            vehicle_type_id = VALUES(vehicle_type_id)
    """)

    with engine.begin() as conn:
        for item in VEHICLE_SUBTYPES:
            result = conn.execute(upsert_sql, {
                "subtype_code": item["subtype_code"],
                "subtype_name": item["subtype_name"],
                "type_name":    item["type_name"],
            })
            if result.rowcount == 0:                                      # 子查询无匹配大类
                logger.warning(
                    "细分类型 %s 未匹配到大类「%s」，已跳过",
                    item["subtype_name"], item["type_name"],
                )

    logger.info("dim_vehicle_subtype 处理 %d 条", len(VEHICLE_SUBTYPES))
    print(f"dim_vehicle_subtype 处理 {len(VEHICLE_SUBTYPES)} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口需要兜底打印
        logger.exception("车型细分类型种子写入失败: %s", exc)
        print(f"[FATAL] 车型细分类型种子写入失败: {exc}")
        sys.exit(1)
