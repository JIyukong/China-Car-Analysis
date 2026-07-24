"""维度生命周期更新脚本：基于 DWD 销量数据计算 first_seen_month / last_seen_month / is_active。

使用时机：
  - 在 DWD 清洗完成后执行
  - 每次 ODS 新增月份数据后重跑

用法:
    python -m pipelines.dim.update_lifecycle
"""

from __future__ import annotations

import sys

from base_infra.db import engine
from base_infra.logging_config import get_logger

from pipelines.dim.build_sales_dimensions import update_dim_lifecycle

logger = get_logger(__name__)


def main() -> None:
    """脚本入口：更新 dim_oem / dim_model 生命周期字段。"""
    logger.info("开始更新维度生命周期")

    with engine.begin() as conn:
        update_dim_lifecycle(conn)

    print("维度生命周期更新完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("生命周期更新失败: %s", exc)
        print(f"[FATAL] 生命周期更新失败: {exc}")
        sys.exit(1)
