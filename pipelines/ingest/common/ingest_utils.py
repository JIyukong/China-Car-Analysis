"""采集脚本公共工具：STATS 字典、公共命令行参数、行工具函数、入库骨架。

四个采集脚本（股票日线 / 股票财务 / 指数日线 / 期货日线）共享大量重复代码，
本模块将完全相同或近乎相同的部分抽出，各脚本只需保留数据源 fetch、transform、
insert SQL、completeness 校验等真正差异化的逻辑。
"""

from __future__ import annotations

import argparse
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from base_infra.db import engine as default_engine


# ---------------------------------------------------------------------------
# STATS 字典
# ---------------------------------------------------------------------------

def make_stats(total_key: str = "total_items") -> dict[str, Any]:
    """创建采集统计字典。

    :param total_key: 第一项计数的 key 名，如 ``"total_stocks"`` / ``"total_indices"``。
    """
    return {
        total_key: 0,
        "success_count": 0,
        "fail_count": 0,
        "skip_count": 0,
        "warning_count": 0,
        "total_rows": 0,
        "fail_details": [],
        "error_types": {},
        "preflight": {"passed": False, "latency_ms": None, "error": None},
    }


def reset_stats(stats: dict[str, Any], total_key: str = "total_items") -> None:
    """重置统计字典到初始状态。"""
    stats.update(make_stats(total_key))


def record_error_type(stats: dict[str, Any], error_type: str) -> None:
    """记录错误类型计数。"""
    stats["error_types"][error_type] = stats["error_types"].get(error_type, 0) + 1


# ---------------------------------------------------------------------------
# 公共命令行参数
# ---------------------------------------------------------------------------

def build_common_parser(description: str) -> argparse.ArgumentParser:
    """构建采集脚本公共参数解析器。

    各脚本可在此基础上追加 ``--stock`` / ``--index`` / ``--commodity`` 等业务过滤参数，
    以及覆盖 ``--start-date`` 的默认值。
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--dry-run", action="store_true", help="仅抓取和转换，不写入数据库")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP/HTTPS 代理地址")
    parser.add_argument("--direct-domains", type=str, default=None, help="不走代理的域名列表（逗号分隔）")
    parser.add_argument("--timeout", type=int, default=15, help="单次请求超时秒数")
    parser.add_argument("--max-retries", type=int, default=3, help="单只股票/指数/品种的最大重试次数")
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="每次请求间隔秒数")
    parser.add_argument("--preflight-only", action="store_true", help="仅执行网络预检后退出")
    parser.add_argument("--start-date", type=str, default="20170101", help="采集起始日期（YYYYMMDD）")
    return parser


# ---------------------------------------------------------------------------
# 行工具函数
# ---------------------------------------------------------------------------

def pick_float(row: pd.Series, keys: list[str]) -> float | None:
    """从 pandas Series 中按候选列名顺序取第一个非空 float 值。"""
    for key in keys:
        if key in row.index and pd.notna(row.get(key)):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def pick_int(row: pd.Series, keys: list[str]) -> int | None:
    """从 pandas Series 中按候选列名顺序取第一个非空 int 值。"""
    for key in keys:
        if key in row.index and pd.notna(row.get(key)):
            try:
                return int(row[key])
            except (TypeError, ValueError):
                continue
    return None


# ---------------------------------------------------------------------------
# 入库骨架
# ---------------------------------------------------------------------------

def execute_insert(rows: list[dict], sql_text: str, engine: Engine | None = None) -> int:
    """执行批量 INSERT ... ON DUPLICATE KEY UPDATE。

    :param rows: 参数化行列表
    :param sql_text: SQL 文本（含 ON DUPLICATE KEY UPDATE 子句）
    :param engine: 可选引擎，默认使用全局 engine
    :returns: 写入行数
    """
    if not rows:
        return 0
    eng = engine or default_engine
    stmt = text(sql_text)
    with eng.begin() as conn:
        conn.execute(stmt, rows)
    return len(rows)
