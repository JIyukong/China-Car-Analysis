"""DWD 层：ODS 城市销量数据清洗 → dwd_sales_monthly_model_city。

清洗规则：
  1. 幂等去重（按 row_hash，NOT EXISTS）
  2. 支持按目标月增量（p_month），避免全表扫描
  3. 标准化车型名/城市名（TRIM）
  4. stat_month 由 DATE 转 CHAR(7) YYYY-MM
  5. 关联 dim_model 补 model_id / oem_id / brand_id / company_id
  6. 关联 dim_area（city_short_name = TRIM(city_name_raw)）补 area_id
  7. 关联 dim_vehicle_subtype（subtype_name = TRIM(level_raw)）补 vehicle_subtype_id
     并回滚到 dim_vehicle_type 得到 vehicle_type_id
  8. 价格区间拆分
  9. 排除 sales_volume 为 NULL 或 <=0 的记录
 10. 未匹配 dim_model 零容忍：预检发现问题 → 发飞书告警 → 抛异常阻断
     （不执行 INSERT，不丢数据，等待人工补维后重跑）

用法:
    python -m pipelines.staging.clean_sales_city_dwd YYYY-MM
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from scheduler import alert

logger = get_logger(__name__)

_MAX_ALERT_DETAIL_ROWS = 30


class CityDimensionMismatchError(RuntimeError):
    """城市 ODS 数据在维度表中找不到匹配，阻断等待人工处理。"""

    def __init__(self, unmatched_rows: int, unmatched_groups: int, detail_text: str) -> None:
        self.unmatched_rows = unmatched_rows
        self.unmatched_groups = unmatched_groups
        self.detail_text = detail_text
        super().__init__(
            f"DWD城市清洗阻断：{unmatched_rows} 行（{unmatched_groups} 组车型）未匹配维度表，"
            f"已发飞书告警，请人工补维后重跑"
        )


def _check_dim_model_readiness(conn: Any, target_month: date | None) -> tuple[int, list[dict]]:
    """预检目标月 ODS 数据关联 dim_model 的情况。

    返回 (未匹配总行数, 未匹配明细列表)。同时检测同名车型 JOIN 扇出
    （一个 model_name 对应多个 dim_model 行），扇出存在时直接抛异常阻断。

    city ODS 无厂商字段，无法像 style SP 那样用 oem_id 双条件消歧，
    因此对扇出零容忍——出现即说明维表需要人工干预。
    """
    month_clause = ""
    params: dict = {}
    if target_month is not None:
        month_clause = "AND ods.stat_month = :p_month"
        params["p_month"] = target_month

    # 检测 dim_model 中是否存在同名车型（model_name 不唯一）。
    dup_row = conn.execute(
        text(
            """
            SELECT GROUP_CONCAT(model_name SEPARATOR ', ')
            FROM (
                SELECT model_name
                FROM dim_model
                GROUP BY model_name
                HAVING COUNT(*) > 1
            ) t
            """
        ),
    ).fetchone()
    duplicated = (dup_row[0] or "") if dup_row else ""
    if duplicated:
        raise RuntimeError(
            f"dim_model 存在同名车型（无法用 oem 消歧）: {duplicated}；"
            f"请人工合并或在 city ODS 补厂商字段后重试。"
        )

    # 统计未匹配 dim_model 的车型明细（按车型名分组）
    rows = conn.execute(
        text(
            f"""
            SELECT
                TRIM(ods.model_name_raw) AS model_name,
                COUNT(*)                  AS row_count
            FROM ods_sales_monthly_model_city ods
            LEFT JOIN dim_model dm
                ON dm.model_name = TRIM(ods.model_name_raw)
            WHERE ods.sales_volume IS NOT NULL
              AND ods.sales_volume > 0
              AND ods.model_name_raw IS NOT NULL
              AND TRIM(ods.model_name_raw) <> ''
              {month_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM dwd_sales_monthly_model_city d
                   WHERE d.row_hash = ods.row_hash
              )
              AND dm.id IS NULL
            GROUP BY TRIM(ods.model_name_raw)
            ORDER BY row_count DESC
            """
        ),
        params,
    ).fetchall()

    items = [{"model": r[0], "count": int(r[1])} for r in rows]
    total = sum(item["count"] for item in items)
    return total, items


def _format_unmatched_detail(items: list[dict]) -> str:
    """把未匹配明细格式化为多行文本（用于飞书告警）。"""
    lines = []
    for i, item in enumerate(items[:_MAX_ALERT_DETAIL_ROWS], 1):
        lines.append(f"{i}. {item['model']}（{item['count']} 行）")
    if len(items) > _MAX_ALERT_DETAIL_ROWS:
        lines.append(f"... 等共 {len(items)} 组，仅展示前 {_MAX_ALERT_DETAIL_ROWS} 组")
    return "\n".join(lines)


def _send_unmatched_alert(
    items: list[dict],
    total_rows: int,
    target_month: date | None,
) -> None:
    """发送飞书告警：包含未匹配车型明细和人工处理指引。"""
    detail = _format_unmatched_detail(items)
    month_label = target_month.strftime("%Y-%m") if target_month else "全量"
    message = (
        f"DWD 城市清洗阻断：{month_label} 存在 {total_rows} 行销量数据无法匹配 dim_model\n"
        f"\n"
        f"未匹配车型明细：\n{detail}\n"
        f"\n"
        f"处理方式：\n"
        f"1. 检查上述车型名称是否有拼写差异（空格、繁简体、别名）\n"
        f"2. 若为新车型：运行 sp_etl_build_sales_dimensions 自动派生 dim_model\n"
        f"3. city ODS 无厂商字段，若 dim_model 存在同名车型需先人工消歧\n"
        f"4. 补维完成后重跑 clean_sales_city_dwd 任务"
    )
    alert.send_alert(
        "ERROR",
        "clean_sales_city_dwd",
        f"dim_mismatch_{month_label}",
        message,
        error_type="CityDimensionMismatchError",
        business_key=month_label,
        unmatched_rows=total_rows,
        unmatched_groups=len(items),
    )


# ODS → DWD 城市销量清洗 SQL
CLEAN_SALES_CITY_DWD_SQL = """
INSERT INTO dwd_sales_monthly_model_city (
    stat_month, source,
    model_name, model_name_raw, model_id,
    oem_id, brand_id, company_id,
    city_name, city_name_raw, area_id,
    subtype_name, level_raw, vehicle_subtype_id, vehicle_type_id,
    sales_volume, price_min, price_max, rank_no, row_hash
)
SELECT
    DATE_FORMAT(ods.stat_month, '%Y-%m')                             AS stat_month,
    ods.source,
    TRIM(ods.model_name_raw)                                         AS model_name,
    ods.model_name_raw                                               AS model_name_raw,
    dm.id                                                            AS model_id,
    dm.oem_id                                                        AS oem_id,
    dm.brand_id                                                      AS brand_id,
    doem.company_id                                                  AS company_id,
    TRIM(ods.city_name_raw)                                          AS city_name,
    ods.city_name_raw                                                AS city_name_raw,
    da.id                                                            AS area_id,
    dvs.subtype_name                                                 AS subtype_name,
    ods.level_raw                                                    AS level_raw,
    dvs.id                                                           AS vehicle_subtype_id,
    dvs.vehicle_type_id                                              AS vehicle_type_id,
    ods.sales_volume,
    CAST(
        NULLIF(TRIM(
            SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', 1), '-', -1)
        ), '') AS DECIMAL(10,2)
    )                                                                AS price_min,
    CAST(
        NULLIF(TRIM(
            SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', -1), '-', 1)
        ), '') AS DECIMAL(10,2)
    )                                                                AS price_max,
    ods.rank_no,
    ods.row_hash
FROM ods_sales_monthly_model_city ods
INNER JOIN dim_model dm
    ON dm.model_name = TRIM(ods.model_name_raw)
INNER JOIN dim_oem doem
    ON doem.id = dm.oem_id
LEFT JOIN dim_area da
    ON da.city_short_name = TRIM(ods.city_name_raw)
LEFT JOIN dim_vehicle_subtype dvs
    ON dvs.subtype_name = TRIM(ods.level_raw)
WHERE ods.sales_volume IS NOT NULL
  AND ods.sales_volume > 0
  AND ods.model_name_raw IS NOT NULL
  AND TRIM(ods.model_name_raw) <> ''
  AND (:p_month IS NULL OR ods.stat_month = :p_month)
  AND NOT EXISTS (
      SELECT 1 FROM dwd_sales_monthly_model_city d
       WHERE d.row_hash = ods.row_hash
  )
"""


def run_clean_sales_city_dwd(target_month: date | None = None) -> int:
    """执行 DWD 城市销量清洗，返回新增行数。

    流程：
      1. 预检未匹配 dim_model → 有问题发飞书 + 抛异常
      2. 预检通过后执行 INSERT

    :param target_month: 目标月份（该月 1 号）；None 表示全量（仅手工补数用）
    """
    month_label = target_month.strftime("%Y-%m") if target_month else "全量"

    with engine.begin() as conn:
        # ---- 第一步：Python 层预检 ----
        unmatched_total, unmatched_items = _check_dim_model_readiness(conn, target_month)

        if unmatched_total > 0:
            logger.error(
                "DWD城市清洗预检失败：%d 行（%d 组车型）未匹配 dim_model",
                unmatched_total, len(unmatched_items),
            )
            for item in unmatched_items[:10]:
                logger.error("  未匹配车型: %s (%d 行)", item["model"], item["count"])
            # 发飞书告警
            _send_unmatched_alert(unmatched_items, unmatched_total, target_month)
            # 抛异常阻断，不执行 INSERT
            raise CityDimensionMismatchError(
                unmatched_rows=unmatched_total,
                unmatched_groups=len(unmatched_items),
                detail_text=_format_unmatched_detail(unmatched_items),
            )

        # ---- 第二步：预检通过，执行正式写入 ----
        logger.info("预检通过，执行城市 DWD 清洗: month=%s", month_label)
        result = conn.execute(
            text(CLEAN_SALES_CITY_DWD_SQL),
            {"p_month": target_month},
        )
        return result.rowcount


def main() -> int:
    """通过统一调度入口恢复已验证的城市销量批次。"""
    from scheduler.monthly_publish import resume_month_cli

    return resume_month_cli('city')


if __name__ == "__main__":
    raise SystemExit(main())
