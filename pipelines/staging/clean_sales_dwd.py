"""DWD 层：ODS 销量数据清洗（下沉为 sp_etl_clean_sales_dwd）。

用法:
    python -m pipelines.staging.clean_sales_dwd
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


def run_clean_sales_dwd(p_month: str | date | None = None) -> int:
    """执行 DWD 清洗 SP，返回新增行数。                """
    m = _parse_month(p_month)
    with engine.connect() as conn:
        conn.execute(
            text("CALL sp_etl_clean_sales_dwd(:p_month)"),
            {"p_month": m},
        )
        affected = conn.execute(text("SELECT @etl_affected_rows")).scalar()
        conn.commit()
    return int(affected or 0)


def main() -> None:
    """脚本入口。                                     """
    logger.info("开始执行 DWD 销量数据清洗...")
    count = run_clean_sales_dwd()
    logger.info("DWD 清洗完成，新增 %d 条记录", count)
    print(f"DWD 清洗完成，新增 {count} 条记录。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("DWD 清洗失败: %s", exc)
        sys.exit(1)
