"""DWD 层：ODS 城市销量数据清洗 → dwd_sales_monthly_model_city。

清洗规则：
  1. 幂等去重（按 row_hash）
  2. 标准化车型名/城市名（TRIM）
  3. stat_month 由 DATE 转 CHAR(7) YYYY-MM
  4. 关联 dim_model 补 model_id / oem_id / brand_id / company_id / ads_id / ads_supplier_name
  5. 关联 dim_area（city_short_name = TRIM(city_name_raw)）补 area_id
  6. 关联 dim_vehicle_subtype（subtype_name = TRIM(level_raw)）补 vehicle_subtype_id
     并回滚到 dim_vehicle_type 得到 vehicle_type_id
  7. 价格区间拆分
  8. 排除 sales_volume 为 NULL 或 <=0 的记录

用法:
    python -m pipelines.staging.clean_sales_city_dwd
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# ODS → DWD 城市销量清洗 SQL
CLEAN_SALES_CITY_DWD_SQL = """
INSERT INTO dwd_sales_monthly_model_city (
    stat_month, source,
    model_name, model_name_raw, model_id,
    oem_id, brand_id, company_id,
    ads_id, ads_supplier_name,
    city_name, city_name_raw, area_id,
    subtype_name, level_raw, vehicle_subtype_id, vehicle_type_id,
    sales_volume, price_min, price_max, rank_no, row_hash
)
SELECT
    DATE_FORMAT(ods.stat_month, '%Y-%m')                             AS stat_month,       -- 统计月份 YYYY-MM
    ods.source,
    TRIM(ods.model_name_raw)                                         AS model_name,
    ods.model_name_raw                                               AS model_name_raw,
    dm.id                                                            AS model_id,
    dm.oem_id                                                        AS oem_id,
    dm.brand_id                                                      AS brand_id,
    doem.company_id                                                  AS company_id,
    dm.ads_id                                                        AS ads_id,           -- 智驾 ADS 拉链ID 冗余
    dm.ads_supplier_name                                             AS ads_supplier_name, -- 智驾供应商名称快照
    TRIM(ods.city_name_raw)                                          AS city_name,
    ods.city_name_raw                                                AS city_name_raw,
    da.id                                                            AS area_id,          -- 地区维度 ID
    dvs.subtype_name                                                 AS subtype_name,     -- 标准化细分类型
    ods.level_raw                                                    AS level_raw,
    dvs.id                                                           AS vehicle_subtype_id,
    dvs.vehicle_type_id                                              AS vehicle_type_id,  -- 由 subtype 派生大类
    ods.sales_volume,
    -- 解析价格区间下限："9.98-17.98" → 9.98
    CAST(
        NULLIF(TRIM(
            SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', 1), '-', -1)
        ), '') AS DECIMAL(10,2)
    )                                                                AS price_min,
    -- 解析价格区间上限："9.98-17.98" → 17.98
    CAST(
        NULLIF(TRIM(
            SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', -1), '-', 1)
        ), '') AS DECIMAL(10,2)
    )                                                                AS price_max,
    ods.rank_no,
    ods.row_hash
FROM ods_sales_monthly_model_city ods
-- 关联车型维度
LEFT JOIN dim_model dm
    ON dm.model_name = TRIM(ods.model_name_raw)
-- 关联主机厂维度（用于取 company_id）
LEFT JOIN dim_oem doem
    ON doem.id = dm.oem_id
-- 关联地区维度（城市简称 = TRIM 城市原文）
LEFT JOIN dim_area da
    ON da.city_short_name = TRIM(ods.city_name_raw)
-- 关联车型细分维度（subtype_name = TRIM level_raw）
LEFT JOIN dim_vehicle_subtype dvs
    ON dvs.subtype_name = TRIM(ods.level_raw)
WHERE ods.sales_volume IS NOT NULL
  AND ods.sales_volume > 0
  -- 幂等：跳过已写入 DWD 的记录
  AND ods.row_hash NOT IN (SELECT row_hash FROM dwd_sales_monthly_model_city)
"""


def run_clean_sales_city_dwd() -> int:
    """执行 DWD 城市销量清洗，返回新增行数。            """
    with engine.begin() as conn:
        result = conn.execute(text(CLEAN_SALES_CITY_DWD_SQL))
        return result.rowcount


def main() -> None:
    """脚本入口。                                       """
    logger.info("开始执行 DWD 城市销量清洗...")
    count = run_clean_sales_city_dwd()
    logger.info("DWD 城市销量清洗完成，新增 %d 条记录", count)
    print(f"DWD 城市销量清洗完成，新增 {count} 条记录。")


if __name__ == "__main__":
    main()
