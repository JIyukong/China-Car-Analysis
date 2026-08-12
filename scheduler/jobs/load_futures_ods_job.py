"""商品期货主力连续 ODS 入库调度任务。

支持碳酸锂/铜/铝等品种的主力连续日线采集调度。
日线数据：每日收盘后执行（建议 16:00-18:00）

用法:
    from scheduler.jobs.load_futures_ods_job import load_futures_main_daily
"""

from __future__ import annotations

from types import SimpleNamespace

from base_infra.logging_config import get_logger
from pipelines.ingest.futures.load_futures_main_sina import (
    STATS as futures_stats,
    run_futures_ingest,
)
from pipelines.ingest.stock.network_runtime import build_runtime_config

logger = get_logger(__name__)


def _build_scheduler_config(proxy_url: str | None = None, preflight_only: bool = False):
    """构造调度器默认运行时配置。"""
    return build_runtime_config(
        SimpleNamespace(
            proxy=proxy_url,
            direct_domains=None,
            timeout=15,
            max_retries=3,
            sleep_seconds=1.0,
            preflight_only=preflight_only,
            start_date="19990101",
        )
    )


def load_futures_main_daily(
    proxy_url: str | None = None,
    dry_run: bool = False,
    preflight_only: bool = False,
    commodity_filter: str | None = None,
    business_key: str | None = None,
) -> bool:
    """采集商品期货主力连续日线数据到 ODS。

    Args:
        proxy_url: 可选代理地址
        dry_run: 是否仅测试不写入
        preflight_only: 是否仅执行网络预检
        commodity_filter: 可选品种过滤（LC/CU/AL）
        business_key: 业务周期键（交易日 YYYY-MM-DD），调度器传入，当前不参与业务逻辑

    Returns:
        成功返回 True，失败返回 False。
    """
    try:
        logger.info("开始采集商品期货主力连续日线数据...")
        ok = run_futures_ingest(
            _build_scheduler_config(
                proxy_url=proxy_url,
                preflight_only=preflight_only,
            ),
            dry_run=dry_run,
            commodity_filter=commodity_filter,
        )
        logger.info(
            "期货采集完成: 成功 %d, 失败 %d, 总行数 %d",
            futures_stats["success_count"],
            futures_stats["fail_count"],
            futures_stats["total_rows"],
        )
        return ok
    except Exception:
        logger.exception("商品期货数据采集失败")
        return False


if __name__ == "__main__":
    # 测试运行
    print("=" * 60)
    print("测试商品期货主力连续日线数据采集")
    print("=" * 60)
    load_futures_main_daily()
