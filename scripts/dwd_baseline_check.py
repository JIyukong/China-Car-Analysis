"""DWD 执行前后基线检查 + 后置验证。

用法:
    python scripts\dwd_baseline_check.py --phase base    # 基线行数（默认）
    python scripts\dwd_baseline_check.py --phase verify  # 后置五大校验
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from base_infra.db import engine


def run_baseline() -> None:
    """打印基线行数 + 表结构状态。                     """
    checks = [
        ("dwd_sales_monthly_model 行数",
         "SELECT COUNT(*) FROM dwd_sales_monthly_model"),
        ("dwd_sales_monthly_model stat_month 类型",
         "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
         "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='dwd_sales_monthly_model' "
         "AND COLUMN_NAME='stat_month'"),
        ("ods_sales_monthly_model 行数",
         "SELECT COUNT(*) FROM ods_sales_monthly_model"),
        ("ods_sales_monthly_model_city 行数",
         "SELECT COUNT(*) FROM ods_sales_monthly_model_city"),
        ("ods_stock_daily 行数",
         "SELECT COUNT(*) FROM ods_stock_daily"),
        ("ods_stock_finance 行数",
         "SELECT COUNT(*) FROM ods_stock_finance"),
        ("dwd_sales_monthly_model_city 是否已存在",
         "SELECT COUNT(*) FROM information_schema.TABLES "
         "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='dwd_sales_monthly_model_city'"),
        ("dwd_stock_daily 是否已存在",
         "SELECT COUNT(*) FROM information_schema.TABLES "
         "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='dwd_stock_daily'"),
        ("dwd_stock_finance 是否已存在",
         "SELECT COUNT(*) FROM information_schema.TABLES "
         "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='dwd_stock_finance'"),
    ]
    with engine.connect() as conn:
        for label, sql in checks:
            row = conn.execute(text(sql)).fetchone()
            print(f"[{label}] => {row[0]}")


def run_verify() -> None:
    """后置五大校验：行数、stat_month 格式、NULL 率、OHLC 异常、比率异常。"""
    with engine.connect() as conn:
        # 1. 4 张 DWD 行数
        print("\n========== 1. DWD 四表行数 ==========")
        for tbl in ("dwd_sales_monthly_model", "dwd_sales_monthly_model_city",
                     "dwd_stock_daily", "dwd_stock_finance"):
            row = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).fetchone()
            print(f"  {tbl}: {row[0]}")

        # 2. stat_month 格式合规性（仅检查有 stat_month 的表）
        print("\n========== 2. stat_month 格式合规 (YYYY-MM) ==========")
        for tbl in ("dwd_sales_monthly_model", "dwd_sales_monthly_model_city"):
            row = conn.execute(text(
                f"SELECT COUNT(*) FROM {tbl} "
                f"WHERE stat_month NOT REGEXP '^[0-9]{{4}}-(0[1-9]|1[0-2])$'"
            )).fetchone()
            total = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).fetchone()[0]
            bad = row[0]
            print(f"  {tbl}: {bad}/{total} 不合规（期望 0）")

        # 3. NULL 率检查
        print("\n========== 3. 维度 NULL 率 ==========")
        null_checks = [
            ("dwd_sales_monthly_model_city", "area_id"),
            ("dwd_sales_monthly_model_city", "vehicle_subtype_id"),
        ]
        for tbl, col in null_checks:
            total = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).fetchone()[0]
            if total == 0:
                print(f"  {tbl}.{col}: 表空，跳过")
                continue
            row = conn.execute(text(
                f"SELECT SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) FROM {tbl}"
            )).fetchone()
            null_count = row[0] or 0
            pct = round(null_count / total * 100, 2)
            print(f"  {tbl}.{col}: {null_count}/{total} ({pct}%)")

        # 4. dwd_stock_daily OHLC 异常行数
        print("\n========== 4. dwd_stock_daily OHLC 异常 ==========")
        row = conn.execute(text(
            "SELECT COUNT(*) FROM dwd_stock_daily WHERE is_valid_ohlc = 0"
        )).fetchone()
        print(f"  is_valid_ohlc=0 行数: {row[0]}")

        # 5. dwd_stock_finance 比率异常
        print("\n========== 5. dwd_stock_finance 比率异常 ==========")
        abnormal_checks = [
            ("roe > 100", "SELECT COUNT(*) FROM dwd_stock_finance WHERE roe > 100"),
            ("debt_ratio > 100", "SELECT COUNT(*) FROM dwd_stock_finance WHERE debt_ratio > 100"),
            ("gross_margin > 100", "SELECT COUNT(*) FROM dwd_stock_finance WHERE gross_margin > 100"),
        ]
        for label, sql in abnormal_checks:
            row = conn.execute(text(sql)).fetchone()
            print(f"  {label}: {row[0]}")

        print("\n========== 验证完成 ==========")


def main() -> None:
    parser = argparse.ArgumentParser(description="DWD 基线/验证")
    parser.add_argument("--phase", choices=["base", "verify"], default="base",
                        help="base=基线行数, verify=后置校验")
    args = parser.parse_args()

    if args.phase == "base":
        run_baseline()
    else:
        run_verify()


if __name__ == "__main__":
    main()