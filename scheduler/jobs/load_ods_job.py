"""爬取完成后将 CSV 加载到 ODS 层的任务薄壳。

拆为两个独立函数以匹配 style / city 两条月链：
    - :func:`load_style_ods`  → ods_sales_monthly_model
    - :func:`load_city_ods`   → ods_sales_monthly_model_city

调度器按 business_key（YYYY-MM）只入库目标月 CSV，避免每月重放 data 目录
下全部历史文件；入库脚本自身 UPSERT 幂等。
"""

from __future__ import annotations

from pathlib import Path

from base_infra.logging_config import get_logger
from pipelines.ingest.sales.load_16888_city_to_ods import (
    data_dir as city_data_dir,
    main as load_city_main,
)
from pipelines.ingest.sales.load_16888_style_to_ods import (
    data_dir as style_data_dir,
    main as load_style_main,
)
from scheduler.runtime import SkipTask

logger = get_logger(__name__)


def _target_csv(
    business_key: str | None,
    data_dir_getter,
    prefix: str,
) -> Path | None:
    """按 business_key 定位目标月 CSV；business_key 为空时返回 None（全量）。

    若目标月 CSV 不存在（爬虫未生成目标月数据），抛 :class:`SkipTask`，
    交由调度器重试/次日探测，避免误跑全量入库。
    """
    if not business_key:
        return None
    month = business_key[:7]
    csv_path = data_dir_getter() / f"{prefix}_{month}.csv"
    if not csv_path.exists():
        raise SkipTask(f"目标月 CSV 不存在，跳过入库: {csv_path}")
    return csv_path


def load_style_ods(business_key: str | None = None) -> bool:
    """车型月销量 CSV → ods_sales_monthly_model（只入目标月）。"""
    try:
        target = _target_csv(business_key, style_data_dir, "style")
        logger.info("开始加载 16888 style → ODS (file=%s)", target or "ALL")
        load_style_main(target, expected_month=business_key[:7] if business_key else None)
        logger.info("style ODS 入库完成")
        return True
    except SkipTask:
        raise
    except Exception:
        logger.exception("style ODS 入库失败")
        return False


def load_city_ods(business_key: str | None = None) -> bool:
    """城市车型月销量 CSV → ods_sales_monthly_model_city（只入目标月）。"""
    try:
        target = _target_csv(business_key, city_data_dir, "city")
        logger.info("开始加载 16888 city → ODS (file=%s)", target or "ALL")
        load_city_main(target, expected_month=business_key[:7] if business_key else None)
        logger.info("city ODS 入库完成")
        return True
    except SkipTask:
        raise
    except Exception:
        logger.exception("city ODS 入库失败")
        return False


if __name__ == "__main__":
    load_style_ods()
