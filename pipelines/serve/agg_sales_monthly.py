"""DWS 层：月度销量多粒度聚合（下沉为 sp_etl_agg_sales_monthly）。

聚合粒度：
  - 按上市公司（company_id）× 月度
  - 按品牌（brand_id）× 月度

用法:
    python -m pipelines.serve.agg_sales_monthly YYYY-MM
"""

from __future__ import annotations

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


def run_agg_sales_monthly(p_month: str | date | None = None) -> int:
    """执行 DWS 聚合 SP，返回受影响行数。              """
    m = _parse_month(p_month)
    with engine.connect() as conn:
        conn.execute(
            text("CALL sp_etl_agg_sales_monthly(:p_month)"),
            {"p_month": m},
        )
        affected = conn.execute(text("SELECT @etl_affected_rows")).scalar()
        conn.commit()
    return int(affected or 0)


def main() -> int:
    """通过统一调度入口恢复已验证的全国销量批次。"""
    from scheduler.monthly_publish import resume_month_cli

    return resume_month_cli('style')


if __name__ == "__main__":
    raise SystemExit(main())
