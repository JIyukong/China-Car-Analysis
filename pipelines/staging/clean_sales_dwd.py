"""DWD清洗：ODS车型月销 → DWD层，调用存储过程，幂等。

对应存储过程：db/procedures/02_sp_etl_clean_sales_dwd.sql

执行流程（零容忍数据质量策略）：
1. Python 预检：查询未匹配 dim_model/dim_oem 的车型明细
   - 有问题 → 组装明细发飞书告警 → 抛 RuntimeError 阻断（不调 SP，不丢数据）
   - 无问题 → 调用 sp_etl_clean_sales_dwd 执行正式写入
2. SP 内部同样有预检 + INNER JOIN + SIGNAL，作为数据库层最后防线
3. DDL 层 model_id/oem_id/brand_id/company_id 均为 NOT NULL + 外键约束

幂等：依赖 row_hash 唯一键，NOT EXISTS 跳过已存在记录。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from base_infra.db import engine  # noqa: E402
from base_infra.logging_config import get_logger  # noqa: E402
from scheduler import alert  # noqa: E402

logger = get_logger("etl.clean_sales_dwd")


# 未匹配明细在飞书消息中最多展示多少组（防止消息过长）
_MAX_ALERT_DETAIL_ROWS = 30


class DimensionMismatchError(RuntimeError):
    """ODS 数据在维度表中找不到匹配，DWD 要求 NOT NULL，阻断等待人工处理。"""

    def __init__(self, unmatched_rows: int, unmatched_groups: int, detail_text: str) -> None:
        self.unmatched_rows = unmatched_rows
        self.unmatched_groups = unmatched_groups
        self.detail_text = detail_text
        super().__init__(
            f"DWD清洗阻断：{unmatched_rows} 行（{unmatched_groups} 组车型）未匹配维度表，"
            f"已发飞书告警，请人工补维后重跑"
        )


def _parse_month(month_str: str | None) -> date | None:
    if not month_str:
        return None
    return datetime.strptime(month_str, "%Y-%m").date()


def _query_unmatched_models(conn: Any, target_month: date | None) -> list[dict]:
    """预检：查询未匹配 dim_oem / dim_model 的车型明细。

    返回每组（厂商+车型+原因）的统计，按影响行数降序。
    原因分两类：
      - OEM_NOT_FOUND：厂商名在 dim_oem 中不存在
      - MODEL_NOT_FOUND：厂商存在但车型在该 OEM 下不存在
    """
    month_clause = ""
    params: dict = {}
    if target_month is not None:
        month_clause = "AND ods.stat_month = :p_month"
        params["p_month"] = target_month

    sql = text(
        f"""
        SELECT
            TRIM(ods.manufacturer_name_raw) AS manufacturer_name,
            TRIM(ods.model_name_raw)        AS model_name,
            CASE
                WHEN doem.id IS NULL THEN 'OEM_NOT_FOUND'
                ELSE 'MODEL_NOT_FOUND'
            END                             AS reason,
            COUNT(*)                        AS row_count
        FROM ods_sales_monthly_model ods
        LEFT JOIN dim_oem doem
            ON doem.oem_name = TRIM(ods.manufacturer_name_raw)
        LEFT JOIN dim_model dm
            ON dm.model_name = TRIM(ods.model_name_raw)
           AND dm.oem_id     = doem.id
        WHERE ods.sales_volume IS NOT NULL
          AND ods.sales_volume > 0
          AND ods.model_name_raw IS NOT NULL
          AND TRIM(ods.model_name_raw) <> ''
          AND ods.manufacturer_name_raw IS NOT NULL
          AND TRIM(ods.manufacturer_name_raw) <> ''
          {month_clause}
          AND NOT EXISTS (
                SELECT 1 FROM dwd_sales_monthly_model d
                 WHERE d.row_hash = ods.row_hash
          )
          AND (doem.id IS NULL OR dm.id IS NULL)
        GROUP BY TRIM(ods.manufacturer_name_raw), TRIM(ods.model_name_raw),
                 CASE WHEN doem.id IS NULL THEN 'OEM_NOT_FOUND' ELSE 'MODEL_NOT_FOUND' END
        ORDER BY row_count DESC
        """
    )
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "manufacturer": r[0],
            "model": r[1],
            "reason": r[2],
            "count": int(r[3]),
        }
        for r in rows
    ]


def _format_unmatched_detail(items: list[dict]) -> str:
    """把未匹配明细格式化为多行文本（用于飞书告警）。"""
    lines = []
    for i, item in enumerate(items[:_MAX_ALERT_DETAIL_ROWS], 1):
        reason_cn = (
            "厂商缺失" if item["reason"] == "OEM_NOT_FOUND" else "车型缺失"
        )
        lines.append(
            f"{i}. [{reason_cn}] {item['manufacturer']} / {item['model']} "
            f"（{item['count']} 行）"
        )
    if len(items) > _MAX_ALERT_DETAIL_ROWS:
        lines.append(f"... 等共 {len(items)} 组，仅展示前 {_MAX_ALERT_DETAIL_ROWS} 组")
    return "\n".join(lines)


def _send_unmatched_alert(
    items: list[dict],
    total_rows: int,
    target_month: date | None,
) -> None:
    """发送飞书告警：包含未匹配车型明细和人工处理指引。"""
    detail = _format_unmatched_detail(items)
    month_label = target_month.strftime("%Y-%m") if target_month else "全量"
    message = (
        f"DWD 清洗阻断：{month_label} 存在 {total_rows} 行销量数据无法匹配维度表\n"
        f"\n"
        f"未匹配明细：\n{detail}\n"
        f"\n"
        f"处理方式：\n"
        f"1. 检查上述厂商/车型名称是否有拼写差异（空格、繁简体、别名）\n"
        f"2. 若为新厂商：在 dim_oem 中补录后运行 sp_etl_build_sales_dimensions\n"
        f"3. 若为新车型：直接运行 sp_etl_build_sales_dimensions 自动派生\n"
        f"4. 补维完成后重跑 clean_sales_dwd 任务"
    )
    alert.send_alert(
        "ERROR",
        "clean_sales_dwd",
        f"dim_mismatch_{month_label}",
        message,
        error_type="DimensionMismatchError",
        business_key=month_label,
        unmatched_rows=total_rows,
        unmatched_groups=len(items),
    )


def clean_sales_dwd(month: str | None = None) -> int:
    """执行清洗。month=None 时清洗所有未处理数据；month='YYYY-MM' 清洗指定月。

    流程：
      1. 预检未匹配维度 → 有问题发飞书 + 抛异常
      2. 调用存储过程正式写入

    Returns:
        本次插入 DWD 的行数
    """
    target_month = _parse_month(month)
    month_label = target_month.strftime("%Y-%m") if target_month else "全量"
    logger.info("开始 DWD 清洗: month=%s", month_label)

    with engine.begin() as conn:
        # ---- 第一步：Python 层预检 ----
        unmatched = _query_unmatched_models(conn, target_month)
        total_unmatched = sum(item["count"] for item in unmatched)

        if total_unmatched > 0:
            logger.error(
                "DWD清洗预检失败：%d 行（%d 组车型）未匹配维度表",
                total_unmatched, len(unmatched),
            )
            for item in unmatched[:10]:
                logger.error(
                    "  未匹配: %s / %s / %s (%d 行)",
                    item["manufacturer"], item["model"],
                    item["reason"], item["count"],
                )
            # 发飞书告警
            _send_unmatched_alert(unmatched, total_unmatched, target_month)
            # 抛异常阻断，不调用 SP，不写入任何数据
            raise DimensionMismatchError(
                unmatched_rows=total_unmatched,
                unmatched_groups=len(unmatched),
                detail_text=_format_unmatched_detail(unmatched),
            )

        # ---- 第二步：预检通过，调用存储过程 ----
        logger.info("预检通过，调用 sp_etl_clean_sales_dwd")
        conn.execute(
            text("CALL sp_etl_clean_sales_dwd(:p_month)"),
            {"p_month": target_month},
        )

        result = conn.execute(text("SELECT @etl_affected_rows")).fetchone()
        rows = int(result[0] or 0) if result else 0

    logger.info("DWD清洗完成: month=%s, 插入 %d 行", month_label, rows)
    return rows


if __name__ == "__main__":
    from scheduler.monthly_publish import resume_month_cli

    raise SystemExit(resume_month_cli('style'))
