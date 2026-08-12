"""ETL 存储过程调度任务薄壳。

每个函数对应一个数据仓库内的集合运算步骤，
业务逻辑均封装在 MySQL 存储过程中，Python 仅负责 CALL 和结果上报。
"""

from __future__ import annotations

from datetime import date, datetime

from scheduler.runtime import TaskResult


def _to_date(business_key: str | None) -> date | None:
    """把 YYYY-MM 或 YYYY-MM-DD 业务键转 date；空值返回 None。"""
    if not business_key:
        return None
    try:
        return datetime.strptime(business_key[:7], "%Y-%m").date()
    except ValueError:
        return None


def build_dimensions(business_key: str | None = None) -> TaskResult:
    """DIM 派生：ods_sales_monthly_model → dim_model。"""
    from pipelines.dim.build_sales_dimensions import run_build_sales_dimensions

    n = run_build_sales_dimensions(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"dim_model 派生 {n} 条")


def refresh_ads(business_key: str | None = None) -> TaskResult:
    """调用现有 sp_refresh_dim_model_ads_all（无参，无 OUT 变量）。"""
    from sqlalchemy import text

    from base_infra.db import engine

    with engine.begin() as conn:
        conn.execute(text("CALL sp_refresh_dim_model_ads_all()"))
    return TaskResult(success=True, message="ADS 拉链刷新完成")


def run_dwd_clean(business_key: str | None = None) -> TaskResult:
    """DWD 清洗：ods_sales_monthly_model → dwd_sales_monthly_model。"""
    from pipelines.staging.clean_sales_dwd import run_clean_sales_dwd

    n = run_clean_sales_dwd(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"DWD 清洗新增 {n} 条")


def update_lifecycle(business_key: str | None = None) -> TaskResult:
    """维度生命周期刷新：dim_oem / dim_model 活跃位。"""
    from pipelines.dim.update_lifecycle import run_update_lifecycle

    n = run_update_lifecycle(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"生命周期更新影响 {n} 行")


def run_dws_agg(business_key: str | None = None) -> TaskResult:
    """DWS 聚合：公司 + 品牌两层月度销量。"""
    from pipelines.serve.agg_sales_monthly import run_agg_sales_monthly

    n = run_agg_sales_monthly(_to_date(business_key))
    return TaskResult(success=True, affected_rows=n, message=f"DWS 聚合影响 {n} 行")
