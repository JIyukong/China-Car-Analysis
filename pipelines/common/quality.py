"""数据质量校验函数：行数、空值率、唯一性检查。

供 scripts/data_quality_check.py 和管道脚本复用。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def check_row_count(
    conn: Connection,
    table_name: str,
    min_rows: int = 1,
) -> bool:
    """检查表行数是否达标。

    Args:
        conn: SQLAlchemy 连接。
        table_name: 表名。
        min_rows: 最小期望行数。

    Returns:
        达标返回 True。
    """
    count = conn.execute(
        text(f"SELECT COUNT(*) FROM {table_name}")
    ).scalar()
    status = "PASS" if count >= min_rows else "FAIL"
    logger.info("[%s] %s: %d rows (min=%d)", status, table_name, count, min_rows)
    return count >= min_rows


def check_null_rate(
    conn: Connection,
    table_name: str,
    column: str,
    max_null_pct: float = 0.05,
) -> bool:
    """检查字段空值率是否在阈值内。

    Args:
        conn: SQLAlchemy 连接。
        table_name: 表名。
        column: 列名。
        max_null_pct: 最大允许空值率（0~1）。

    Returns:
        合格返回 True。
    """
    result = conn.execute(
        text(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN `{column}` IS NULL THEN 1 ELSE 0 END) AS nulls
            FROM {table_name}
        """)
    ).fetchone()
    total = result[0] if result else 0
    nulls = result[1] if result else 0
    null_pct = nulls / total if total > 0 else 1.0
    status = "PASS" if null_pct <= max_null_pct else "FAIL"
    logger.info(
        "[%s] %s.%s: %.2%% nulls (max=%.0%%)",
        status, table_name, column, null_pct * 100, max_null_pct * 100,
    )
    return null_pct <= max_null_pct


def check_unique(
    conn: Connection,
    table_name: str,
    column: str,
) -> bool:
    """检查字段唯一性（主键/唯一键校验）。

    Args:
        conn: SQLAlchemy 连接。
        table_name: 表名。
        column: 列名。

    Returns:
        无重复返回 True。
    """
    dup_count = conn.execute(
        text(f"""
            SELECT COUNT(*) FROM (
                SELECT `{column}` FROM {table_name}
                WHERE `{column}` IS NOT NULL
                GROUP BY `{column}`
                HAVING COUNT(*) > 1
            ) t
        """)
    ).scalar()
    status = "PASS" if dup_count == 0 else "FAIL"
    logger.info("[%s] %s.%s: %d duplicates", status, table_name, column, dup_count)
    return dup_count == 0
