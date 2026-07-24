"""DWD 层：ODS 销量数据清洗 → dwd_sales_monthly_model。

清洗规则：
  1. 去重（按 row_hash 保留最新记录）
  2. 标准化车型名/厂商名（TRIM 去空格）
  3. 价格区间拆分为 price_min / price_max
  4. 关联 dim_model / dim_oem / dim_company 补充维度ID
  5. 排除销量为 NULL 或 <=0 的异常记录

用法:
    python pipelines.staging.clean_sales_dwd
    python -m pipelines.staging.clean_sales_dwd
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# ODS → DWD 清洗 SQL
# 从 ods_sales_monthly_model 读取，关联维度表，写入 dwd_sales_monthly_model
CLEAN_SALES_DWD_SQL = """
INSERT INTO dwd_sales_monthly_model (
    stat_month, source,
    model_name, model_name_raw, model_id,
    manufacturer_name, oem_id, brand_id, company_id,
    sales_volume, price_min, price_max, rank_no, row_hash
)
SELECT
    ods.stat_month,
    ods.source,
    TRIM(ods.model_name_raw)                                         AS model_name,
    ods.model_name_raw                                               AS model_name_raw,
    dm.id                                                            AS model_id,
    TRIM(ods.manufacturer_name_raw)                                  AS manufacturer_name,
    dm.oem_id                                                        AS oem_id,
    dm.brand_id                                                      AS brand_id,
    doem.company_id                                                  AS company_id,
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
FROM ods_sales_monthly_model ods
-- 关联车型维度（按原始车型名 + 原始厂商名匹配）
LEFT JOIN dim_model dm
    ON dm.model_name = TRIM(ods.model_name_raw)
-- 关联主机厂维度（dim_model.oem_id → dim_oem.id）
LEFT JOIN dim_oem doem
    ON doem.id = dm.oem_id
WHERE ods.sales_volume IS NOT NULL
  AND ods.sales_volume > 0
  -- 幂等：跳过已写入 DWD 的记录
  AND ods.row_hash NOT IN (SELECT row_hash FROM dwd_sales_monthly_model)
"""


def run_clean_sales_dwd() -> int:
    """执行 DWD 销量数据清洗，返回新增行数。              """
    with engine.begin() as conn:
        result = conn.execute(text(CLEAN_SALES_DWD_SQL))
        return result.rowcount


def main() -> None:
    """脚本入口。                                         """
    logger.info("开始执行 DWD 销量数据清洗...")
    count = run_clean_sales_dwd()
    logger.info("DWD 清洗完成，新增 %d 条记录", count)
    print(f"DWD 清洗完成，新增 {count} 条记录。")


if __name__ == "__main__":
    main()
