"""通用 DDL 文件执行器。

读取 .sql 文件 → 按分号切分为独立语句 → 依次执行；
自动跳过纯注释语句、空语句、CREATE 表已存在错误由 IF NOT EXISTS 自身兜住。

用法:
    python scripts/run_sql_file.py db/migrations/003_alter_dwd_sales_monthly_model.sql
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def _strip_sql_comments(sql: str) -> str:
    """去掉 `--` 行注释与 `/* */` 块注释。"""
    # 块注释
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # 行注释
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def split_statements(sql_content: str) -> list[str]:
    """按分号切分 SQL 语句（简单实现，不处理字符串内分号）。"""
    cleaned = _strip_sql_comments(sql_content)
    parts = [p.strip() for p in cleaned.split(";")]
    return [p for p in parts if p]


def run_sql_file(path: Path) -> None:
    """执行 SQL 文件。                                     """
    sql_content = path.read_text(encoding="utf-8")
    statements = split_statements(sql_content)
    logger.info("准备执行 %s，共 %d 条语句", path.name, len(statements))
    with engine.begin() as conn:
        for idx, stmt in enumerate(statements, start=1):
            preview = " ".join(stmt.split())[:120]
            logger.info("[%d/%d] %s", idx, len(statements), preview)
            conn.execute(text(stmt))
    logger.info("%s 执行完成", path.name)
    print(f"{path.name} 执行完成，共 {len(statements)} 条语句。")


def main() -> None:
    """脚本入口。                                          """
    if len(sys.argv) < 2:
        print("用法: python scripts/run_sql_file.py <sql_file>")
        sys.exit(1)
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"文件不存在: {arg}")
            sys.exit(1)
        run_sql_file(path)


if __name__ == "__main__":
    main()
