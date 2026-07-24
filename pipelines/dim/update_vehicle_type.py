"""历史兼容脚本：根据 ods 销量数据更新 dim_model.vehicle_type 字段。

数据来源：ods_sales_monthly_model_city（model_name_raw + level_raw 两字段去重）
映射桥梁：dim_vehicle_subtype.subtype_name → dim_vehicle_type.type_name

核心规则：
  - 从 ods 按 (model_name_raw, level_raw) 聚合，取每个车型出现频次最高的 level_raw
  - 用 level_raw 匹配 dim_vehicle_subtype.subtype_name，再取其大类的 type_name
  - 写入 dim_model.vehicle_type（车型大类枚举：SUV/轿车/MPV/占位）
  - ods 中无 level_raw 记录的车型保持 '占位'

注意：
    当前项目已将 `dim_model.vehicle_type` 的权威维护入口迁移到
    `dim_energy_vehicle` 手工维度表。本脚本仅保留给历史数据核对使用，
    不再作为主流程或权威回填入口。

用法:
    python -m pipelines.dim.update_vehicle_type
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 取每个车型频次最高 level_raw 的 SQL
TOP_LEVEL_SQL = text("""
    SELECT model_name_raw, level_raw
    FROM (
        SELECT
            model_name_raw,
            level_raw,
            ROW_NUMBER() OVER (
                PARTITION BY model_name_raw
                ORDER BY COUNT(*) DESC, level_raw ASC
            ) AS rn
        FROM ods_sales_monthly_model_city
        WHERE level_raw IS NOT NULL AND level_raw != ''
        GROUP BY model_name_raw, level_raw
    ) ranked
    WHERE rn = 1
""")


def build_model_type_map(conn) -> dict[str, str]:
    """构建 车型名 → 大类名称 映射。

    通过 ods 频次最高的 level_raw 关联 dim_vehicle_subtype.subtype_name，
    再取其 vehicle_type_id 对应的 dim_vehicle_type.type_name。
    """
    rows = conn.execute(TOP_LEVEL_SQL).fetchall()

    # level_raw → type_name 的映射（经 dim_vehicle_subtype → dim_vehicle_type）
    level_map_sql = text("""
        SELECT s.subtype_name, t.type_name
        FROM dim_vehicle_subtype s
        JOIN dim_vehicle_type t ON s.vehicle_type_id = t.id
    """)
    level_to_type: dict[str, str] = {}
    for subtype_name, type_name in conn.execute(level_map_sql).fetchall():
        level_to_type[str(subtype_name).strip()] = str(type_name).strip()

    model_type_map: dict[str, str] = {}
    unmatched_levels: set[str] = set()
    for model_name_raw, level_raw in rows:
        level = str(level_raw).strip()
        type_name = level_to_type.get(level)
        if type_name is None:
            unmatched_levels.add(level)
            type_name = "占位"
        model_type_map[str(model_name_raw).strip()] = type_name

    if unmatched_levels:
        logger.warning(
            "以下 level_raw 未匹配到 dim_vehicle_subtype，已兜底为「占位」：%s",
            ", ".join(sorted(unmatched_levels)),
        )

    logger.info("构建车型→大类映射：%d 个车型", len(model_type_map))
    return model_type_map


def update_vehicle_type(model_type_map: dict[str, str]) -> tuple[int, int]:
    """更新 dim_model.vehicle_type，返回 (命中数, 未命中数)。"""
    hit = 0
    miss = 0

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, model_name FROM dim_model")
        ).fetchall()

        update_sql = text(
            "UPDATE dim_model SET vehicle_type = :vt WHERE id = :mid"
        )

        for mid, model_name in rows:
            name = str(model_name).strip()
            type_name = model_type_map.get(name)
            if type_name is not None:
                conn.execute(update_sql, {"vt": type_name, "mid": mid})
                hit += 1
            else:
                miss += 1

    return hit, miss


def main() -> None:
    """脚本入口：根据 ods level_raw 更新 vehicle_type。"""
    logger.warning("当前脚本仅供历史兼容使用，请勿作为手工维度权威回填入口。")
    logger.info("开始更新 dim_model.vehicle_type")

    with engine.connect() as conn:
        model_type_map = build_model_type_map(conn)

    n_hit, n_miss = update_vehicle_type(model_type_map)

    logger.info("更新完成：命中 %d 条，未命中 %d 条（保持占位）", n_hit, n_miss)
    print(f"更新完成：命中 {n_hit} 条，未命中 {n_miss} 条（保持「占位」）")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口需要兜底打印
        logger.exception("vehicle_type 更新失败: %s", exc)
        print(f"[FATAL] vehicle_type 更新失败: {exc}")
        sys.exit(1)
