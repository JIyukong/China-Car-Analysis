"""初始化通用日期维度表 dim_date。

用法:
    python scripts/seed/seed_dim_date.py
    python scripts/seed/seed_dim_date.py --start-date 2017-01-01 --end-date 2030-12-31
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="初始化 dim_date 通用日期维度表")
    parser.add_argument("--start-date", default="2017-01-01", help="起始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default="2035-12-31", help="结束日期，格式 YYYY-MM-DD")
    return parser.parse_args()


def daterange(start_date: date, end_date: date):
    """按天遍历日期区间。"""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def month_end(current_date: date) -> date:
    """返回当前日期所在月份的月末日期。"""
    if current_date.month == 12:
        return date(current_date.year, 12, 31)
    next_month = date(current_date.year, current_date.month + 1, 1)
    return next_month - timedelta(days=1)


def build_rows(start_date: date, end_date: date) -> list[dict[str, object]]:
    """构建 dim_date 入库参数列表。"""
    rows: list[dict[str, object]] = []
    for current in daterange(start_date, end_date):
        month_start = current.replace(day=1)
        month_last = month_end(current)
        iso_calendar = current.isocalendar()
        rows.append({
            "date_key": int(current.strftime("%Y%m%d")),
            "full_date": current,
            "year_num": current.year,
            "quarter_num": (current.month - 1) // 3 + 1,
            "month_num": current.month,
            "month_key": int(current.strftime("%Y%m")),
            "month_label": current.strftime("%Y-%m"),
            "week_num": iso_calendar.week,
            "day_of_month": current.day,
            "day_of_week": current.isoweekday(),
            "is_month_start": 1 if current == month_start else 0,
            "is_month_end": 1 if current == month_last else 0,
            "is_quarter_end": 1 if current in {
                month_end(date(current.year, 3, 1)),
                month_end(date(current.year, 6, 1)),
                month_end(date(current.year, 9, 1)),
                month_end(date(current.year, 12, 1)),
            } else 0,
            "is_year_end": 1 if current.month == 12 and current.day == 31 else 0,
            "month_start_date": month_start,
            "month_end_date": month_last,
        })
    return rows


def upsert_rows(rows: list[dict[str, object]]) -> int:
    """批量 UPSERT 到 dim_date。"""
    sql = text("""
        INSERT INTO dim_date
            (date_key, full_date, year_num, quarter_num, month_num,
             month_key, month_label, week_num, day_of_month, day_of_week,
             is_month_start, is_month_end, is_quarter_end, is_year_end,
             month_start_date, month_end_date)
        VALUES
            (:date_key, :full_date, :year_num, :quarter_num, :month_num,
             :month_key, :month_label, :week_num, :day_of_month, :day_of_week,
             :is_month_start, :is_month_end, :is_quarter_end, :is_year_end,
             :month_start_date, :month_end_date)
        ON DUPLICATE KEY UPDATE
            year_num         = VALUES(year_num),
            quarter_num      = VALUES(quarter_num),
            month_num        = VALUES(month_num),
            month_key        = VALUES(month_key),
            month_label      = VALUES(month_label),
            week_num         = VALUES(week_num),
            day_of_month     = VALUES(day_of_month),
            day_of_week      = VALUES(day_of_week),
            is_month_start   = VALUES(is_month_start),
            is_month_end     = VALUES(is_month_end),
            is_quarter_end   = VALUES(is_quarter_end),
            is_year_end      = VALUES(is_year_end),
            month_start_date = VALUES(month_start_date),
            month_end_date   = VALUES(month_end_date)
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def main() -> None:
    """脚本入口。"""
    args = parse_args()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if start_date > end_date:
        raise ValueError("start-date 不能晚于 end-date")

    rows = build_rows(start_date, end_date)
    count = upsert_rows(rows)
    logger.info("dim_date 处理 %d 条", count)
    print(f"dim_date 处理 {count} 条。")


if __name__ == "__main__":
    main()
