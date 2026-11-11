"""调度任务公共工具：运行时配置构造、通用日志格式等。"""

from __future__ import annotations

from types import SimpleNamespace

from pipelines.ingest.stock.network_runtime import build_runtime_config


def build_scheduler_config(
    start_date: str = "20170101",
    proxy_url: str | None = None,
    preflight_only: bool = False,
):
    """构造调度器默认运行时配置。

    各 job 文件调用此函数即可，无需各自重复定义 _build_scheduler_config。
    """
    return build_runtime_config(
        SimpleNamespace(
            proxy=proxy_url,
            direct_domains=None,
            timeout=15,
            max_retries=3,
            sleep_seconds=1.0,
            preflight_only=preflight_only,
            start_date=start_date,
        )
    )
