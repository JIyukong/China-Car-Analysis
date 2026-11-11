"""月度销量维度刷新入口：从 ODS 自动派生 dim_oem / dim_brand / dim_model。

本脚本由调度器每月 load_style_ods 完成后调用，等价 SQL：

    CALL sp_etl_build_sales_dimensions();

存储过程保持 SET 类型操作（幂等），通过 NOT EXISTS 防重。
执行前对 dim_model 做一次 model_name 快照，执行后回查本次新增的车型名，
供调用方（scheduler job）用于"本月新增车型"飞书通知。通知不阻断链路：
新车型属于可自动入维的实体，缺失的是 ADS/能源类型等手工属性，
后者由 dimension_readiness 门控在 DWD 之后阻断 DWS。
"""

from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import text

from base_infra.db import engine

logger = logging.getLogger(__name__)


def _snapshot_models(conn) -> set[str]:
    rows = conn.execute(
        text("SELECT model_name FROM dim_model")
    ).fetchall()
    return {r[0] for r in rows}


def run_build_sales_dimensions(
    target_month: _dt.date | None = None,
) -> dict:
    """调用 sp_etl_build_sales_dimensions 派生销量维度。

    :param target_month: 目标月份（仅用于日志/通知展示，SP 本身无参全量刷新）。
    :return: dict，含 affected_rows（SP 影响行数）与 new_models（本次新增车型名列表）。
    """
    month_label = target_month.strftime("%Y-%m") if target_month else "ALL"
    logger.info(
        "开始调用 sp_etl_build_sales_dimensions() target_month=%s", month_label,
    )

    with engine.begin() as conn:
        before = _snapshot_models(conn)
        conn.execute(text("CALL sp_etl_build_sales_dimensions()"))
        row = conn.execute(text("SELECT @etl_affected_rows AS n")).fetchone()
        after = _snapshot_models(conn)

    affected = int(getattr(row, "n", 0) or 0)
    new_models = sorted(after - before)
    logger.info(
        "维度刷新完成 affected_rows=%s new_models=%s",
        affected, len(new_models),
    )
    if new_models:
        preview = ", ".join(new_models[:20])
        more = "" if len(new_models) <= 20 else f" 等共 {len(new_models)} 个"
        logger.info("本次新增车型：%s%s", preview, more)

    return {"affected_rows": affected, "new_models": new_models}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_build_sales_dimensions()
    print(f"OK affected_rows={result['affected_rows']} new_models={len(result['new_models'])}")

