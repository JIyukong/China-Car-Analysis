"""定时爬取 16888 销量数据任务。

通过 subprocess 调用 crawl_all_with_retry.py 实现断点续传爬取。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from base_infra.logging_config import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def crawl_16888_monthly() -> bool:
    """爬取上月 16888 style + city 数据。

    Returns:
        成功返回 True。
    """
    style_script = (
        PROJECT_ROOT
        / "pipelines" / "ingest" / "sales"
        / "crawl_16888_style" / "crawl_all_with_retry.py"
    )
    city_script = (
        PROJECT_ROOT
        / "pipelines" / "ingest" / "sales"
        / "crawl_16888_city" / "crawl_2025_full_year.py"
    )

    for name, script in [("16888_style", style_script), ("16888_city", city_script)]:
        if not script.exists():
            logger.warning("爬虫脚本不存在，跳过 %s: %s", name, script)
            continue
        logger.info("开始爬取 %s ...", name)
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            logger.error("%s 爬取失败（exit=%d）", name, result.returncode)
            return False
        logger.info("%s 爬取完成", name)

    return True


if __name__ == "__main__":
    crawl_16888_monthly()
