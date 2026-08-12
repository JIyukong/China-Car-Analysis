"""DIM 层：从 ODS 派生车型维度（下沉为 sp_etl_build_sales_dimensions）。

用法:
    python -m pipelines.dim.build_sales_dimensions
"""

from __future__ import annotations

import sys
from datetime import date, datetime

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def _parse_month(p_month: str | date | None) -> date | None:
    """把 'YYYY-MM' 字符串转 date；date 原样返回；None 返回 None。"""
    if p_month is None:
        return None
    if isinstance(p_month, date):
        return p_month
    return datetime.strptime(p_month, "%Y-%m").date()


def run_build_sales_dimensions(p_month: str | date | None = None) -> int:
    """执行 DIM 派生 SP，返回受影响行数。             """
    m = _parse_month(p_month)
    with engine.connect() as conn:
        conn.execute(
            text("CALL sp_etl_build_sales_dimensions(:p_month)"),
            {"p_month": m},
        )
        affected = conn.execute(text("SELECT @etl_affected_rows")).scalar()
        conn.commit()
    return int(affected or 0)


def main() -> None:
    """脚本入口：派生车型维度。                        """
    logger.info("开始执行 dim_model 派生...")
    count = run_build_sales_dimensions()
    logger.info("dim_model 派生/更新 %d 条", count)
    print(f"dim_model 派生/更新 {count} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("派生维度失败: %s", exc)
        print(f"[FATAL] 派生维度失败: {exc}")
        sys.exit(1)
