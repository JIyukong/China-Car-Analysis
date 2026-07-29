"""DWD 层：ODS 股价日线清洗 → dwd_stock_daily。

清洗规则：
  1. 幂等去重（依赖 UNIQUE (stock_id, trade_date)，INSERT IGNORE）
  2. 强制关联 dim_stock（未命中丢弃）
  3. 从 dim_stock 冗余快照字段：stock_name / market / exchange / currency
  4. OHLC 逻辑校验：low ≤ open,close ≤ high；异常置 is_valid_ohlc = 0
  5. pct_change 缺失时按 LAG 窗口回补（(close - prev_close) / prev_close * 100）
  6. 分年份分批入库，降低单事务锁持有时间；SET SESSION innodb_lock_wait_timeout=300

用法:
    python -m pipelines.staging.clean_stock_daily_dwd
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# ODS → DWD 股价日线清洗 SQL（按年份分批）
# 说明：
# 1. ODS 实际列：stock_code_raw / stock_name_raw / market_raw / exchange_raw，无 row_hash、无 currency
# 2. dwd_stock_daily 有 UNIQUE KEY (stock_id, trade_date)，用 INSERT IGNORE 天然幂等
# 3. currency 从 dim_stock.currency 冗余；row_hash 用 CONCAT_WS 合成，避免 NULL 污染
CLEAN_STOCK_DAILY_DWD_SQL = """
INSERT IGNORE INTO dwd_stock_daily (
    trade_date, stock_id,
    stock_code, stock_code_raw, stock_name,
    market, exchange, currency,
    `open`, high, low, `close`,
    volume, amount, pct_change,
    is_valid_ohlc, row_hash
)
SELECT
    ods.trade_date,
    ds.id                                                            AS stock_id,
    ds.stock_code                                                    AS stock_code,
    ods.stock_code_raw                                               AS stock_code_raw,
    ds.stock_name                                                    AS stock_name,
    ds.market                                                        AS market,
    ds.exchange                                                      AS exchange,
    ds.currency                                                      AS currency,
    ods.`open`,
    ods.high,
    ods.low,
    ods.`close`,
    ods.volume,
    ods.amount,
    ods.pct_change,
    -- OHLC 逻辑校验
    CASE
        WHEN ods.low  IS NULL OR ods.high IS NULL
          OR ods.`open` IS NULL OR ods.`close` IS NULL THEN 1
        WHEN ods.low <= ods.`open`
         AND ods.low <= ods.`close`
         AND ods.`open`  <= ods.high
         AND ods.`close` <= ods.high THEN 1
        ELSE 0
    END                                                              AS is_valid_ohlc,
    -- 自然键合成 row_hash（CONCAT_WS 忽略 NULL）
    MD5(CONCAT_WS('|', ods.stock_code_raw, ods.trade_date, ods.source)) AS row_hash
FROM ods_stock_daily ods
INNER JOIN dim_stock ds
    ON ds.stock_code = ods.stock_code_raw
WHERE YEAR(ods.trade_date) = :year
"""

# 涨跌幅回补：LAG 窗口 + 独立事务
BACKFILL_PCT_CHANGE_SQL = """
UPDATE dwd_stock_daily d
JOIN (
    SELECT id AS curr_id, curr_close, prev_close FROM (
        SELECT id,
               `close` AS curr_close,
               LAG(`close`) OVER (PARTITION BY stock_id ORDER BY trade_date) AS prev_close,
               pct_change
        FROM dwd_stock_daily
    ) w
    WHERE w.pct_change IS NULL
      AND w.curr_close IS NOT NULL
      AND w.prev_close IS NOT NULL
      AND w.prev_close <> 0
) t ON t.curr_id = d.id
SET d.pct_change = ROUND((t.curr_close - t.prev_close) / t.prev_close * 100, 4)
"""

# 年份探测：只处理 ODS 实际存在的年份
DISTINCT_YEARS_SQL = "SELECT DISTINCT YEAR(trade_date) FROM ods_stock_daily ORDER BY 1"


def run_clean_stock_daily_dwd() -> tuple[int, int]:
    """按年份分批执行清洗，返回 (总新增行数, 回补行数)。"""
    # 探测 ODS 存在的年份
    with engine.connect() as conn:
        conn.execute(text("SET SESSION innodb_lock_wait_timeout = 300"))
        years = [row[0] for row in conn.execute(text(DISTINCT_YEARS_SQL)).fetchall()]
    logger.info("ODS 存在年份: %s", years)

    total_inserted = 0
    for year in years:
        with engine.begin() as conn:
            conn.execute(text("SET SESSION innodb_lock_wait_timeout = 300"))
            result = conn.execute(text(CLEAN_STOCK_DAILY_DWD_SQL), {"year": int(year)})
            inserted = result.rowcount
            total_inserted += inserted
            logger.info("year=%d 新增 %d 条", year, inserted)

    # 回补 pct_change 独立事务
    with engine.begin() as conn:
        conn.execute(text("SET SESSION innodb_lock_wait_timeout = 300"))
        backfill_result = conn.execute(text(BACKFILL_PCT_CHANGE_SQL))
        backfill_rows = backfill_result.rowcount

    return total_inserted, backfill_rows


def main() -> None:
    """脚本入口。                                       """
    logger.info("开始执行 DWD 股价日线清洗...")
    inserted, backfilled = run_clean_stock_daily_dwd()
    logger.info("DWD 股价日线清洗完成：新增 %d 条，回补 pct_change %d 条", inserted, backfilled)
    print(f"DWD 股价日线清洗完成：新增 {inserted} 条，回补 pct_change {backfilled} 条。")


if __name__ == "__main__":
    main()
