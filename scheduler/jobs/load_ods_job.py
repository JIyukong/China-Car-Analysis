"""爬取完成后自动将 CSV 加载到 ODS 层。"""

from __future__ import annotations

from base_infra.logging_config import get_logger
from pipelines.ingest.sales.load_16888_style_to_ods import main as load_style
from pipelines.ingest.sales.load_16888_city_to_ods import main as load_city

logger = get_logger(__name__)


def load_ods_after_crawl() -> bool:
    """爬取完成后自动入库 style + city 数据到 ODS。

    Returns:
        成功返回 True。
    """
    try:
        logger.info("开始加载 16888 style → ODS ...")
        load_style()
        logger.info("开始加载 16888 city → ODS ...")
        load_city()
        logger.info("ODS 入库完成")
        return True
    except Exception:
        logger.exception("ODS 入库失败")
        return False


if __name__ == "__main__":
    load_ods_after_crawl()
