"""DWS 层：月度销量多粒度聚合 → dws_sales_monthly_company / dws_sales_monthly_brand。

聚合粒度：
  - 按上市公司（company_id）× 月度
  - 按品牌（brand_id）× 月度

指标：销量合计、市占率、同比、环比

用法:
    python -m pipelines.serve.agg_sales_monthly
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 按上市公司月度聚合 SQL
AGG_COMPANY_MONTHLY_SQL = """
INSERT INTO dws_sales_monthly_company (
    stat_month, company_id, company_name,
    total_sales, market_share, yoy_growth, mom_growth
)
SELECT
    curr.stat_month,
    curr.company_id,
    dc.company_name,
    curr.monthly_sales                                              AS total_sales,
    ROUND(curr.monthly_sales / total.monthly_sales * 100, 4)        AS market_share,
    CASE
        WHEN prev.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - prev.monthly_sales) / prev.monthly_sales * 100, 2)
        ELSE NULL
    END                                                             AS yoy_growth,
    CASE
        WHEN last_month.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - last_month.monthly_sales) / last_month.monthly_sales * 100, 2)
        ELSE NULL
    END                                                             AS mom_growth
FROM (
    -- 当月各上市公司销量
    SELECT stat_month, company_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE company_id IS NOT NULL
    GROUP BY stat_month, company_id
) curr
JOIN dim_company dc ON dc.id = curr.company_id
CROSS JOIN (
    -- 当月全市场总销量
    SELECT stat_month, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE company_id IS NOT NULL
    GROUP BY stat_month
) total ON total.stat_month = curr.stat_month
LEFT JOIN (
    -- 去年同期（同比）
    SELECT DATE_SUB(stat_month, INTERVAL 12 MONTH) AS stat_month,
           company_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE company_id IS NOT NULL
    GROUP BY DATE_SUB(stat_month, INTERVAL 12 MONTH), company_id
) prev ON prev.stat_month = curr.stat_month AND prev.company_id = curr.company_id
LEFT JOIN (
    -- 上月（环比）
    SELECT DATE_SUB(stat_month, INTERVAL 1 MONTH) AS stat_month,
           company_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE company_id IS NOT NULL
    GROUP BY DATE_SUB(stat_month, INTERVAL 1 MONTH), company_id
) last_month ON last_month.stat_month = curr.stat_month
             AND last_month.company_id = curr.company_id
ON DUPLICATE KEY UPDATE
    company_name  = VALUES(company_name),
    total_sales   = VALUES(total_sales),
    market_share  = VALUES(market_share),
    yoy_growth    = VALUES(yoy_growth),
    mom_growth    = VALUES(mom_growth)
"""

# 按品牌月度聚合 SQL
AGG_BRAND_MONTHLY_SQL = """
INSERT INTO dws_sales_monthly_brand (
    stat_month, brand_id, brand_name,
    total_sales, market_share, yoy_growth, mom_growth
)
SELECT
    curr.stat_month,
    curr.brand_id,
    db.brand_name,
    curr.monthly_sales                                              AS total_sales,
    ROUND(curr.monthly_sales / total.monthly_sales * 100, 4)        AS market_share,
    CASE
        WHEN prev.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - prev.monthly_sales) / prev.monthly_sales * 100, 2)
        ELSE NULL
    END                                                             AS yoy_growth,
    CASE
        WHEN last_month.monthly_sales > 0
        THEN ROUND((curr.monthly_sales - last_month.monthly_sales) / last_month.monthly_sales * 100, 2)
        ELSE NULL
    END                                                             AS mom_growth
FROM (
    SELECT stat_month, brand_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE brand_id IS NOT NULL
    GROUP BY stat_month, brand_id
) curr
JOIN dim_brand db ON db.id = curr.brand_id
CROSS JOIN (
    SELECT stat_month, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE brand_id IS NOT NULL
    GROUP BY stat_month
) total ON total.stat_month = curr.stat_month
LEFT JOIN (
    SELECT DATE_SUB(stat_month, INTERVAL 12 MONTH) AS stat_month,
           brand_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE brand_id IS NOT NULL
    GROUP BY DATE_SUB(stat_month, INTERVAL 12 MONTH), brand_id
) prev ON prev.stat_month = curr.stat_month AND prev.brand_id = curr.brand_id
LEFT JOIN (
    SELECT DATE_SUB(stat_month, INTERVAL 1 MONTH) AS stat_month,
           brand_id, SUM(sales_volume) AS monthly_sales
    FROM dwd_sales_monthly_model
    WHERE brand_id IS NOT NULL
    GROUP BY DATE_SUB(stat_month, INTERVAL 1 MONTH), brand_id
) last_month ON last_month.stat_month = curr.stat_month
            AND last_month.brand_id = curr.brand_id
ON DUPLICATE KEY UPDATE
    brand_name   = VALUES(brand_name),
    total_sales  = VALUES(total_sales),
    market_share = VALUES(market_share),
    yoy_growth   = VALUES(yoy_growth),
    mom_growth   = VALUES(mom_growth)
"""


def run_agg_company_monthly() -> int:
    """执行上市公司月度聚合，返回受影响行数。            """
    with engine.begin() as conn:
        result = conn.execute(text(AGG_COMPANY_MONTHLY_SQL))
        return result.rowcount


def run_agg_brand_monthly() -> int:
    """执行品牌月度聚合，返回受影响行数。                """
    with engine.begin() as conn:
        result = conn.execute(text(AGG_BRAND_MONTHLY_SQL))
        return result.rowcount


def main() -> None:
    """脚本入口：执行公司 + 品牌两层聚合。               """
    logger.info("开始 DWS 公司月度聚合...")
    company_count = run_agg_company_monthly()
    logger.info("DWS 公司月度聚合完成，受影响 %d 条", company_count)

    logger.info("开始 DWS 品牌月度聚合...")
    brand_count = run_agg_brand_monthly()
    logger.info("DWS 品牌月度聚合完成，受影响 %d 条", brand_count)

    print(f"DWS 聚合完成：公司 {company_count} 条，品牌 {brand_count} 条。")


if __name__ == "__main__":
    main()
