"""数据质量校验脚本：检查 ODS/DIM/Bridge 各层数据完整性和一致性。

用法:
    python scripts/data_quality_check.py
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.common.quality import (
    check_null_rate,
    check_row_count,
    check_unique,
)

logger = get_logger(__name__)


def check_current_bridge_end_date(conn) -> bool:
    """校验当前桥接关系是否统一使用 9999-12-31。"""
    invalid_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM bridge_brand_oem
            WHERE is_current = 1
              AND (
                  effective_end_date IS NULL
                  OR effective_end_date <> '9999-12-31'
              )
            """
        )
    ).scalar_one()
    if invalid_count > 0:
        logger.error("bridge_brand_oem 当前有效记录存在无效结束时间: %d 条", invalid_count)
        return False
    logger.info("bridge_brand_oem 当前有效记录结束时间校验通过")
    return True


def check_single_current_brand_per_oem(conn) -> bool:
    """校验同一 OEM 不存在多条当前有效品牌关系。"""
    duplicated = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM (
                SELECT oem_id
                FROM bridge_brand_oem
                WHERE is_current = 1
                GROUP BY oem_id
                HAVING COUNT(*) > 1
            ) t
            """
        )
    ).scalar_one()
    if duplicated > 0:
        logger.error("bridge_brand_oem 存在 %d 个 OEM 命中多条当前有效品牌关系", duplicated)
        return False
    logger.info("bridge_brand_oem 当前有效品牌唯一性校验通过")
    return True


def check_dim_model_brand_alignment(conn) -> bool:
    """校验 dim_model.brand_id 与当前桥接关系保持一致。"""
    mismatch_count = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM dim_model m
            JOIN bridge_brand_oem b
              ON b.oem_id = m.oem_id
             AND b.is_current = 1
             AND b.effective_end_date = '9999-12-31'
            WHERE m.brand_id <> b.brand_id
            """
        )
    ).scalar_one()
    if mismatch_count > 0:
        logger.error("dim_model.brand_id 与当前桥接关系不一致: %d 条", mismatch_count)
        return False
    logger.info("dim_model.brand_id 与当前桥接关系一致性校验通过")
    return True


def main() -> None:
    """主校验流程：ODS → DIM → Bridge 各层逐项检查。      """
    all_pass = True

    with engine.connect() as conn:
        logger.info("===== 数据质量校验开始 =====")

        # --- ODS 层校验 ---
        checks = [
            # 行数校验
            lambda: check_row_count(conn, "ods_sales_monthly_model", min_rows=10000),
            # 空值校验
            lambda: check_null_rate(conn, "ods_sales_monthly_model", "model_name_raw"),
            lambda: check_null_rate(conn, "ods_sales_monthly_model", "manufacturer_name_raw"),
            lambda: check_null_rate(conn, "ods_sales_monthly_model", "sales_volume"),
            # 唯一性校验
            lambda: check_unique(conn, "ods_sales_monthly_model", "row_hash"),

            # --- DIM 层校验 ---
            lambda: check_row_count(conn, "dim_company", min_rows=19),
            lambda: check_row_count(conn, "dim_oem", min_rows=100),
            lambda: check_row_count(conn, "dim_model", min_rows=100),
            lambda: check_row_count(conn, "dim_date", min_rows=1000),
            lambda: check_unique(conn, "dim_company", "company_code"),
            lambda: check_unique(conn, "dim_oem", "oem_code"),
            lambda: check_unique(conn, "dim_model", "model_code"),

            # --- Bridge 层校验 ---
            lambda: check_row_count(conn, "bridge_company_stock", min_rows=10),
            lambda: check_row_count(conn, "bridge_brand_oem", min_rows=10),
            lambda: check_current_bridge_end_date(conn),
            lambda: check_single_current_brand_per_oem(conn),
            lambda: check_dim_model_brand_alignment(conn),
        ]

        for check in checks:
            if not check():
                all_pass = False

    # --- 汇总结果 ---
    if all_pass:
        logger.info("===== 全部校验通过 =====")
        print("数据质量校验：全部通过。")
        sys.exit(0)
    else:
        logger.error("===== 存在校验失败项 =====")
        print("数据质量校验：存在失败项，请检查日志。")
        sys.exit(1)


if __name__ == "__main__":
    main()
