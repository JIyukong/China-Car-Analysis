"""股票数据 ODS 入库调度任务。

支持日线数据和财务数据的采集调度。
日线数据：每日收盘后执行（建议 16:00-18:00）
财务数据：每季度末执行（Q1:4月末, Q2:8月末, Q3:10月末, 年报:次年4月末）

用法:
    from scheduler.jobs.load_stock_ods_job import load_stock_daily, load_stock_finance
"""

from __future__ import annotations

from types import SimpleNamespace

from base_infra.logging_config import get_logger
from pipelines.ingest.stock.load_stock_daily_akshare import (
    STATS as daily_stats,
    run_daily_ingest,
)
from pipelines.ingest.stock.load_stock_finance_akshare import (
    STATS as finance_stats,
    run_finance_ingest,
)
from pipelines.ingest.stock.network_runtime import build_runtime_config

logger = get_logger(__name__)


def _build_scheduler_config(proxy_url: str | None = None, preflight_only: bool = False):
    return build_runtime_config(
        SimpleNamespace(
            proxy=proxy_url,
            direct_domains=None,
            timeout=15,
            max_retries=3,
            sleep_seconds=1.0,
            preflight_only=preflight_only,
            start_date="20170101",
        )
    )


def load_stock_daily(
    proxy_url: str | None = None,
    dry_run: bool = False,
    preflight_only: bool = False,
    business_key: str | None = None,
) -> bool:
    """采集股票日线数据到 ODS。

    :param business_key: 业务周期键（交易日 YYYY-MM-DD），由调度器传入，
        当前不参与业务逻辑，采集脚本按最新交易日处理。
    :returns: 成功返回 True，失败返回 False。
    """
    try:
        logger.info("开始采集股票日线数据...")
        ok = run_daily_ingest(
            _build_scheduler_config(proxy_url=proxy_url, preflight_only=preflight_only),
            dry_run=dry_run,
        )
        logger.info(
            "日线采集完成: 成功 %d, 失败 %d, 总行数 %d",
            daily_stats["success_count"],
            daily_stats["fail_count"],
            daily_stats["total_rows"],
        )
        return ok
    except Exception:
        logger.exception("日线数据采集失败")
        return False


def load_stock_finance(
    proxy_url: str | None = None,
    dry_run: bool = False,
    preflight_only: bool = False,
    business_key: str | None = None,
) -> bool:
    """采集股票财务数据到 ODS。

    :param business_key: 业务周期键（交易日 YYYY-MM-DD），由调度器传入，
        当前不参与业务逻辑，采集脚本按 MAX(report_date) 增量拉取。
    :returns: 成功返回 True，失败返回 False。
    """
    try:
        logger.info("开始采集股票财务数据...")
        ok = run_finance_ingest(
            _build_scheduler_config(proxy_url=proxy_url, preflight_only=preflight_only),
            dry_run=dry_run,
        )
        logger.info(
            "财务采集完成: 成功 %d, 失败 %d, 总行数 %d",
            finance_stats["success_count"],
            finance_stats["fail_count"],
            finance_stats["total_rows"],
        )
        return ok
    except Exception:
        logger.exception("财务数据采集失败")
        return False


def load_all_stock_data() -> bool:
    """采集全部股票数据（日线 + 财务）。

    Returns:
        成功返回 True，失败返回 False。
    """
    daily_ok = load_stock_daily()
    finance_ok = load_stock_finance()
    return daily_ok and finance_ok


if __name__ == "__main__":
    # 测试运行
    print("=" * 60)
    print("测试日线数据采集")
    print("=" * 60)
    load_stock_daily()

    print("\n" + "=" * 60)
    print("测试财务数据采集")
    print("=" * 60)
    load_stock_finance()
