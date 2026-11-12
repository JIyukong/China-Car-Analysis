"""指数数据 ODS 入库调度任务。

支持同花顺行业指数日线数据的采集调度。
日线数据：每日收盘后执行（建议 16:00-18:00）

用法:
    from scheduler.jobs.load_index_ods_job import load_ths_index_daily
"""

from __future__ import annotations

from base_infra.logging_config import get_logger
from pipelines.ingest.index.load_ths_index_daily_akshare import (
    STATS as index_stats,
    run_index_ingest,
)
from scheduler.jobs.common import build_scheduler_config

logger = get_logger(__name__)


def load_ths_index_daily(
    proxy_url: str | None = None,
    dry_run: bool = False,
    preflight_only: bool = False,
    index_filter: str | None = None,
    business_key: str | None = None,
) -> bool:
    """采集同花顺行业指数日线数据到 ODS。

    Args:
        proxy_url: 可选代理地址
        dry_run: 是否仅测试不写入
        preflight_only: 是否仅执行网络预检
        index_filter: 可选指数过滤（index_code 或 index_name）
        business_key: 业务周期键（交易日 YYYY-MM-DD），调度器传入，当前不参与业务逻辑

    Returns:
        成功返回 True，失败返回 False。
    """
    try:
        logger.info("开始采集同花顺行业指数日线数据...")
        ok = run_index_ingest(
            build_scheduler_config(
                proxy_url=proxy_url,
                preflight_only=preflight_only,
            ),
            dry_run=dry_run,
            index_filter=index_filter,
        )
        logger.info(
            "指数日线采集完成: 成功 %d, 失败 %d, 总行数 %d",
            index_stats["success_count"],
            index_stats["fail_count"],
            index_stats["total_rows"],
        )
        return ok
    except Exception:
        logger.exception("指数日线数据采集失败")
        return False


if __name__ == "__main__":
    raise SystemExit("请使用 python -m scheduler.runner --chain daily --step load_index_daily")
