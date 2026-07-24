"""管道公共 SQL 工具：幂等 UPSERT、批量插入、表存在性检查。

供 ingest / staging / serve 各层管道脚本复用。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def upsert_batch(
    conn: Connection,
    table_name: str,
    rows: list[dict[str, Any]],
    conflict_keys: list[str],
) -> int:
    """批量 UPSERT：冲突时更新非冲突字段。

    Args:
        conn: SQLAlchemy 连接（需在事务中）。
        table_name: 目标表名。
        rows: 字典列表，每个字典代表一行数据。
        conflict_keys: 冲突判断的列名列表（如 ``["row_hash"]``）。

    Returns:
        受影响行数。
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join([f":{col}" for col in columns])
    col_str = ", ".join(columns)
    update_cols = [col for col in columns if col not in conflict_keys]
    update_str = ", ".join([f"{col} = VALUES({col})" for col in update_cols])

    sql_text = f"""
        INSERT INTO {table_name} ({col_str})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_str}
    """
    result = conn.execute(text(sql_text), rows)
    return result.rowcount


def table_exists(conn: Connection, table_name: str) -> bool:
    """检查表是否存在。

    Args:
        conn: SQLAlchemy 连接。
        table_name: 表名。

    Returns:
        存在返回 True，否则 False。
    """
    result = conn.execute(
        text("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = :name
        """),
        {"name": table_name},
    )
    return result.scalar() > 0


def get_row_count(conn: Connection, table_name: str) -> int:
    """获取表行数。

    Args:
        conn: SQLAlchemy 连接。
        table_name: 表名。

    Returns:
        行数。
    """
    return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()


def safe_execute(
    conn: Connection,
    sql: str,
    params: dict[str, Any] | None = None,
    commit: bool = True,
) -> int:
    """安全执行 SQL，捕获异常并记录日志。

    Args:
        conn: SQLAlchemy 连接。
        sql: SQL 语句。
        params: 参数字典。
        commit: 是否自动提交。

    Returns:
        受影响行数。
    """
    try:
        result = conn.execute(text(sql), params or {})
        if commit:
            conn.commit()
        return result.rowcount
    except Exception:
        logger.exception("SQL 执行失败: %s", sql[:200])
        raise
