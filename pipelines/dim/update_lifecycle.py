"""维度生命周期更新（下沉为 sp_etl_update_lifecycle）。

使用时机：
  - 在 DWD 清洗完成后执行
  - 每次 ODS 新增月份数据后重跑

用法:
    python -m pipelines.dim.update_lifecycle
"""

from __future__ import annotations

import sys
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


def run_update_lifecycle(p_month: str | date | None = None) -> int:
    """执行生命周期更新 SP，返回受影响行数。          """
    m = _parse_month(p_month)
    with engine.connect() as conn:
        conn.execute(
            text("CALL sp_etl_update_lifecycle(:p_month)"),
            {"p_month": m},
        )
        affected = conn.execute(text("SELECT @etl_affected_rows")).scalar()
        conn.commit()
    return int(affected or 0)


def main() -> None:
    """脚本入口：更新 dim_oem / dim_model 生命周期字段。"""
    logger.info("开始更新维度生命周期")
    count = run_update_lifecycle()
    logger.info("维度生命周期更新完成，影响 %d 行", count)
    print("维度生命周期更新完成。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("生命周期更新失败: %s", exc)
        print(f"[FATAL] 生命周期更新失败: {exc}")
        sys.exit(1)
