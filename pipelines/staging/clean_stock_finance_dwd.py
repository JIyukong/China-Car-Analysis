"""DWD 层：ODS 股票财务数据清洗 → dwd_stock_finance。

清洗规则：
  1. 幂等去重（依赖 UNIQUE (stock_id, report_date, report_type)，INSERT IGNORE）
  2. ODS 实际结构：仅有 stock_id（无 stock_code_raw / stock_name_raw / market_raw）
     从 dim_stock 反推 stock_code / stock_name / market
  3. 派生 report_year / report_quarter（年报为 4）
  4. 比率单位归一化：若源值 ∈ [0,1] 则乘以 100（roe / gross_margin / debt_ratio）
  5. 同一 (stock_id, report_date, report_type) 多来源时优先保留 akshare
  6. SET SESSION innodb_lock_wait_timeout = 300

用法:
    python -m pipelines.staging.clean_stock_finance_dwd
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# ODS → DWD 财务指标清洗 SQL
# 说明：
# 1. 使用行号窗口对 (stock_id, report_date, report_type) 优先保留 source='akshare'
# 2. 比率归一化：CASE WHEN ABS(v) <= 1 THEN v*100 ELSE v END
# 3. ODS 无 stock_code_raw / row_hash，从 dim_stock 反推 code / market
# 4. 幂等依赖 UNIQUE KEY (stock_id, report_date, report_type) + INSERT IGNORE
CLEAN_STOCK_FINANCE_DWD_SQL = """
INSERT IGNORE INTO dwd_stock_finance (
    stock_id, stock_code, stock_code_raw, stock_name, market,
    report_date, report_type, report_year, report_quarter,
    eps, net_profit,
    total_revenue, main_revenue, total_profit,
    total_assets, total_liabilities, equity,
    roe, gross_margin, debt_ratio, is_ratio_normalized,
    row_hash
)
SELECT
    ds.id                                                            AS stock_id,
    ds.stock_code                                                    AS stock_code,
    ds.stock_code                                                    AS stock_code_raw,  -- ODS 无原始码，暂用标准码
    ds.stock_name                                                    AS stock_name,
    ds.market                                                        AS market,
    src.report_date,
    src.report_type,
    YEAR(src.report_date)                                            AS report_year,
    -- 年报 → 4；季报 → 从 report_type 截取数字
    CASE src.report_type
        WHEN 'Q1' THEN 1
        WHEN 'Q2' THEN 2
        WHEN 'Q3' THEN 3
        WHEN 'Q4' THEN 4
        WHEN '年报' THEN 4
        ELSE NULL
    END                                                              AS report_quarter,
    src.eps,
    src.net_profit,
    src.total_revenue,
    src.main_revenue,
    src.total_profit,
    src.total_assets,
    src.total_liabilities,
    src.equity,
    -- 比率归一化：小数形式 → 百分数
    CASE WHEN src.roe IS NOT NULL AND ABS(src.roe) <= 1
         THEN ROUND(src.roe * 100, 4) ELSE src.roe END               AS roe,
    CASE WHEN src.gross_margin IS NOT NULL AND ABS(src.gross_margin) <= 1
         THEN ROUND(src.gross_margin * 100, 4) ELSE src.gross_margin END AS gross_margin,
    CASE WHEN src.debt_ratio IS NOT NULL AND ABS(src.debt_ratio) <= 1
         THEN ROUND(src.debt_ratio * 100, 4) ELSE src.debt_ratio END AS debt_ratio,
    1                                                                AS is_ratio_normalized,
    -- 自然键合成 row_hash（CONCAT_WS 忽略 NULL）
    MD5(CONCAT_WS('|', src.stock_id, src.report_date, src.report_type)) AS row_hash
FROM (
    -- 同一 (stock_id, report_date, report_type) 多来源时优先 akshare
    SELECT ods.*,
           ROW_NUMBER() OVER (
               PARTITION BY ods.stock_id, ods.report_date, ods.report_type
               ORDER BY CASE WHEN ods.source = 'akshare' THEN 0 ELSE 1 END, ods.id
           ) AS rn
    FROM ods_stock_finance ods
    WHERE ods.stock_id IS NOT NULL
) src
INNER JOIN dim_stock ds
    ON ds.id = src.stock_id
WHERE src.rn = 1
"""


def run_clean_stock_finance_dwd() -> int:
    """执行 DWD 财务指标清洗，返回新增行数。            """
    with engine.begin() as conn:
        conn.execute(text("SET SESSION innodb_lock_wait_timeout = 300"))
        result = conn.execute(text(CLEAN_STOCK_FINANCE_DWD_SQL))
        return result.rowcount


def main() -> None:
    """脚本入口。                                       """
    logger.info("开始执行 DWD 股票财务指标清洗...")
    count = run_clean_stock_finance_dwd()
    logger.info("DWD 股票财务清洗完成，新增 %d 条记录", count)
    print(f"DWD 股票财务清洗完成，新增 {count} 条记录。")


if __name__ == "__main__":
    main()