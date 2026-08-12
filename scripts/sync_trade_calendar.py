"""同步 A 股交易日历到 dim_trade_calendar。

数据源：akshare.tool_trade_date_hist_sina()
执行时机：初始化时执行一次；每年 12 月再跑一次同步下一年。

用法:
    python -m scripts.sync_trade_calendar
    python scripts/sync_trade_calendar.py --year 2026
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import akshare as ak
import pandas as pd
from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 固定市场标识：沪深两市
_MARKET_SSE = "SSE"


def sync_year(year: int) -> int:
    """同步指定年份的交易日历。返回写入行数。"""
    df = ak.tool_trade_date_hist_sina()

    # akshare 返回列 trade_date，元素可能是 datetime.date 或 pd.Timestamp
    trade_dates: set[date] = set()
    for raw in df["trade_date"]:
        d = raw.date() if isinstance(raw, pd.Timestamp) else raw
        if d.year == year:
            trade_dates.add(d)

    rows = 0
    # Python 端逐日 UPSERT：akshare 命中的日期标为交易日，其余日期标为休市
    with engine.begin() as conn:
        current = date(year, 1, 1)
        while current.year == year:
            is_open = 1 if current in trade_dates else 0
            note = "akshare" if is_open else "weekend_or_holiday"
            conn.execute(
                text(
                    "INSERT INTO dim_trade_calendar (trade_date, is_open, market, note) "
                    "VALUES (:d, :o, :m, :n) AS new "
                    "ON DUPLICATE KEY UPDATE is_open = new.is_open, note = new.note"
                ),
                {"d": current, "o": is_open, "m": _MARKET_SSE, "n": note},
            )
            rows += 1
            current += timedelta(days=1)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 A 股交易日历")
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="同步哪一年（默认今年）",
    )
    args = parser.parse_args()
    n = sync_year(args.year)
    logger.info("已同步 %d 年 %d 个交易日", args.year, n)
    print(f"已同步 {args.year} 年 {n} 个交易日")


if __name__ == "__main__":
    main()
