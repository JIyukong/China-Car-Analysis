"""使用 akshare 拉取同花顺行业指数日线数据到 ods_index_daily 表。

首期覆盖 881125 汽车整车，作为汽车板块基准线。
数据源: ak.stock_board_industry_index_ths(symbol=行业中文名, start_date, end_date)
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import akshare as ak
import pandas as pd
import requests
from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.ingest.common.ingest_utils import (
    build_common_parser,
    execute_insert,
    make_stats,
    pick_float,
    pick_int,
    record_error_type,
    reset_stats,
)
from pipelines.ingest.common.market_quality import market_start
from pipelines.ingest.stock.network_runtime import (
    RuntimeConfig,
    build_runtime_config,
    classify_fetch_error,
    fetch_daily_symbol,
    fetch_requested_prices,
    patched_requests_runtime,
    run_preflight_check,
    setup_proxy,
    sleep_before_retry,
    write_daily_rows,
)

logger = get_logger(__name__)

# 数据采集结果统计
STATS = make_stats("total_indices")


def parse_args():
    """解析命令行参数。"""
    parser = build_common_parser("同花顺行业指数日线数据采集")
    parser.add_argument(
        "--index",
        type=str,
        default=None,
        help="指定单个指数代码或中文名（如 881125 或 汽车整车），默认全量采集 dim_index",
    )
    return parser.parse_args()


def get_all_indices(index_filter: str | None = None):
    """从 dim_index 表获取所有启用中的指数信息。

    Args:
        index_filter: 可选指定 index_code 或 index_name 过滤。
    """
    sql = text("""
        SELECT id, index_code, index_name, index_source, market, category
        FROM dim_index
        WHERE is_active = 1
        ORDER BY index_source, index_code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    indices = [
        {
            "index_id": row[0],
            "index_code": row[1],
            "index_name": row[2],
            "index_source": row[3],
            "market": row[4],
            "category": row[5],
        }
        for row in rows
    ]

    if index_filter:
        indices = [
            item
            for item in indices
            if item["index_code"] == index_filter or item["index_name"] == index_filter
        ]
    return indices


def get_last_trade_date(index_id):
    """获取指定指数在 ods_index_daily 中的最新交易日期。"""
    sql = text("""
        SELECT MAX(trade_date) FROM ods_index_daily
        WHERE index_id = :index_id
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, {"index_id": index_id}).scalar()
    return result


def fetch_ths_index_daily(index_name: str, start_date: str):
    """使用 akshare 拉取同花顺行业指数日线数据。

    Args:
        index_name: 行业中文名（如 汽车整车）
        start_date: 起始日期 YYYYMMDD
    """
    end_date = datetime.now().strftime("%Y%m%d")
    return ak.stock_board_industry_index_ths(
        symbol=index_name,
        start_date=start_date,
        end_date=end_date,
    )


def fetch_ths_recent_daily(index_code: str):
    """Read the same THS index from its rolling daily-history endpoint."""
    callback = f"quotebridge_v4_line_bk_{index_code}_01_last"
    response = requests.get(
        f"https://d.10jqka.com.cn/v4/line/bk_{index_code}/01/last.js",
        headers={"Referer": "https://q.10jqka.com.cn/"},
    )
    response.raise_for_status()
    body = response.text.strip()
    if not body.startswith(f"{callback}(") or not body.endswith(")"):
        raise ValueError("Unexpected THS recent-history response")
    payload = json.loads(body[len(callback) + 1:-1])
    fields = ["date", "open", "high", "low", "close", "volume", "amount"]
    records = [line.split(",")[:len(fields)] for line in payload["data"].split(";") if line]
    frame = pd.DataFrame(records, columns=fields)
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d").dt.date
    return frame


def transform_daily_data(df, index_info):
    """将 akshare 返回的数据转换为 ODS 格式。"""
    rows = []
    fetch_time = datetime.now()
    index_id = index_info.get("index_id")
    index_code = index_info.get("index_code")
    index_name = index_info.get("index_name")
    index_source = index_info.get("index_source") or "THS"

    # 兼容中英文列名
    date_col = None
    for cand in ("日期", "date"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        return rows

    for _, row in df.iterrows():
        try:
            trade_date = pd.to_datetime(row[date_col]).date()
        except Exception as exc:  # noqa: BLE001
            logger.warning("转换日期失败 %s: %s", row.get(date_col), exc)
            continue

        rows.append({
            "index_id": index_id,
            "index_code_raw": index_code,
            "index_name_raw": index_name,
            "index_source_raw": index_source,
            "trade_date": trade_date,
            "open": pick_float(row, ("开盘价", "开盘", "open")),
            "high": pick_float(row, ("最高价", "最高", "high")),
            "low": pick_float(row, ("最低价", "最低", "low")),
            "close": pick_float(row, ("收盘价", "收盘", "close")),
            "volume": pick_int(row, ("成交量", "volume")),
            "amount": pick_float(row, ("成交额", "amount")),
            "pct_change": pick_float(row, ("涨跌幅", "pct_change")),
            "change_amount": pick_float(row, ("涨跌额", "change_amount")),
            "turnover_rate": pick_float(row, ("换手率", "turnover_rate")),
            "source": "akshare",
            "fetch_time": fetch_time,
        })

    return rows


def insert_daily_data(rows):
    """将日线数据批量写入 ods_index_daily 表。"""
    sql = """
        INSERT INTO ods_index_daily
            (index_id, index_code_raw, index_name_raw, index_source_raw,
             trade_date, open, high, low, close, volume, amount,
             pct_change, change_amount, turnover_rate, source, fetch_time)
        VALUES
            (:index_id, :index_code_raw, :index_name_raw, :index_source_raw,
             :trade_date, :open, :high, :low, :close, :volume, :amount,
             :pct_change, :change_amount, :turnover_rate, :source, :fetch_time)
        ON DUPLICATE KEY UPDATE
            index_id = VALUES(index_id),
            open = VALUES(open),
            high = VALUES(high),
            low = VALUES(low),
            close = VALUES(close),
            volume = VALUES(volume),
            amount = VALUES(amount),
            pct_change = VALUES(pct_change),
            change_amount = VALUES(change_amount),
            turnover_rate = VALUES(turnover_rate),
            fetch_time = VALUES(fetch_time)
    """
    return execute_insert(rows, sql)


def check_daily_data_completeness(rows, last_date=None, start_date="20170101", *, bounded=False):
    """完整性校验：close 缺失率、首次全量行数。"""
    result = {
        "is_complete": True,
        "issues": [],
        "row_count": len(rows),
        "missing_rates": {},
    }
    if not rows:
        result["is_complete"] = False
        result["issues"].append("接口返回空数据")
        return result

    total_rows = len(rows)
    is_full_load = last_date is None and not bounded

    close_missing = sum(1 for row in rows if row.get("close") is None)
    close_rate = close_missing / total_rows
    result["missing_rates"]["close"] = close_rate
    if close_rate > 0.1:
        result["is_complete"] = False
        result["issues"].append(
            f"close 缺失率过高: {close_missing}/{total_rows}"
        )

    if is_full_load and start_date <= "20170101" and total_rows < 30:
        result["is_complete"] = False
        result["issues"].append("首次全量返回记录过少，疑似网络/限频导致抓取不完整")

    return result


def fetch_single_index(index_info, config: RuntimeConfig, dry_run=False):
    """采集单只指数的日线数据。

    Args:
        index_info: 指数信息字典
        config: 运行时配置
        dry_run: 是否仅测试不写入
    """
    index_id = index_info["index_id"]
    index_code = index_info["index_code"]
    index_name = index_info["index_name"]
    index_source = index_info.get("index_source") or "THS"

    result = {
        "index_code": index_code,
        "index_name": index_name,
        "index_source": index_source,
        "status": "success",
        "rows_fetched": 0,
        "rows_inserted": 0,
        "error": None,
        "error_type": None,
        "completeness": None,
    }

    # 当前仅支持 THS 行业指数
    if index_source != "THS":
        result["status"] = "skipped"
        result["error"] = f"暂不支持的指数来源: {index_source}"
        result["error_type"] = "unsupported_source"
        return result

    # 获取增量起始日期
    requested = config.trade_dates[index_code] if config.trade_dates is not None else None
    last_date = get_last_trade_date(index_id) if requested is None else None
    start_date = (market_start(last_date, config.start_date, config.repair_from) if requested is None
                  else min(requested).strftime("%Y%m%d"))

    df = None
    rows = None
    for attempt_index in range(config.max_retries):
        try:
            if requested is None:
                with patched_requests_runtime(config):
                    df = fetch_ths_index_daily(index_name, start_date)
            else:
                rows, used = fetch_requested_prices(
                    [("ths_year", lambda: fetch_ths_index_daily(index_name, start_date)),
                     ("ths_recent", lambda: fetch_ths_recent_daily(index_code))],
                    lambda frame: transform_daily_data(frame, index_info),
                    requested, config,
                )
                if "ths_recent" in used:
                    logger.info("THS recent-history fallback supplied rows for %s", index_code)
            break
        except Exception as exc:  # noqa: BLE001
            error_type = classify_fetch_error(exc)
            result["error_type"] = error_type
            result["error"] = str(exc)
            if attempt_index == config.max_retries - 1:
                result["status"] = "failed"
                return result

            delay = sleep_before_retry(config, attempt_index)
            logger.warning(
                "采集 %s (%s) 失败，类型=%s，第 %d/%d 次重试前等待 %.2f 秒: %s",
                index_name,
                index_code,
                error_type,
                attempt_index + 1,
                config.max_retries,
                delay,
                exc,
            )

    if rows is None:
        if df is None or df.empty:
            result["status"] = "failed"
            result["error"] = "接口返回空数据"
            result["error_type"] = "empty_payload"
            return result

        if "日期" not in df.columns and "date" not in df.columns:
            result["status"] = "failed"
            result["error"] = "缺少日期字段"
            result["error_type"] = "schema_mismatch"
            return result

        result["rows_fetched"] = len(df)

        rows = transform_daily_data(df, index_info)
    else:
        result["rows_fetched"] = len(rows)
        if not rows:
            result.update(status="no_new_rows", error="所有来源均未返回请求日期的有效行情")
            return result
    if not rows:
        result["status"] = "failed"
        result["error"] = "数据转换后为空"
        result["error_type"] = "empty_payload"
        return result

    completeness = check_daily_data_completeness(
        rows,
        last_date=last_date,
        start_date=config.repair_from or config.start_date,
        bounded=requested is not None,
    )
    result["completeness"] = completeness
    if not completeness["is_complete"]:
        result["status"] = "failed"
        result["error"] = "; ".join(completeness["issues"])
        result["error_type"] = "data_incomplete"
        return result
    if completeness["issues"]:
        result["status"] = "warning"
        result["error"] = "; ".join(completeness["issues"])
        result["error_type"] = "data_warning"

    if not dry_run:
        write_daily_rows(insert_daily_data, rows, result)

    return result


def print_summary():
    """打印数据采集汇总报告。"""
    print("\n" + "=" * 60)
    print("指数日线数据采集汇总报告")
    print("=" * 60)
    if STATS["preflight"]:
        print(f"预检状态: {STATS['preflight']['status']}")
        print(f"预检说明: {STATS['preflight']['message']}")
    print(f"总指数数: {STATS['total_indices']}")
    print(f"成功数: {STATS['success_count']}")
    print(f"警告数: {STATS['warning_count']}")
    print(f"失败数: {STATS['fail_count']}")
    print(f"跳过数: {STATS['skip_count']}")
    print(f"总采集行数: {STATS['total_rows']}")

    if STATS["error_types"]:
        print("\n错误分类统计:")
        for error_type, count in sorted(STATS["error_types"].items()):
            print(f"  - {error_type}: {count}")

    if STATS["fail_details"]:
        print("\n失败详情:")
        for item in STATS["fail_details"]:
            print(
                f"  - {item['index_code']} ({item['index_name']}): "
                f"[{item['error_type']}] {item['error']}"
            )

    print("=" * 60 + "\n")


def run_index_ingest(
    config: RuntimeConfig,
    dry_run: bool = False,
    index_filter: str | None = None,
) -> bool:
    """采集主流程。"""
    reset_stats(STATS, "total_indices")
    setup_proxy(config)

    logger.info("开始采集同花顺行业指数日线数据...")
    logger.info("当前代理: %s", config.proxy_url or "未显式设置")
    logger.info("直连域名: %s", ",".join(config.direct_domains))

    # 复用东财通用预检
    preflight = run_preflight_check(config)
    STATS["preflight"] = preflight
    if not preflight["ok"]:
        logger.warning(
            "网络预检未通过: [%s] %s，将继续尝试采集",
            preflight["status"],
            preflight["message"],
        )

    if config.preflight_only:
        logger.info("仅执行预检，未启动采集")
        print_summary()
        return preflight["ok"]

    indices = get_all_indices(index_filter=index_filter)
    if config.trade_dates is not None:
        indices = [
            item for item in indices
            if item["index_code"] in config.trade_dates and item["index_source"] == "THS" and (item["market"] or "CN") == "CN"
        ]
    STATS["total_indices"] = len(indices)

    if not indices:
        logger.error(
            "dim_index 中未找到匹配的指数记录，index_filter=%s",
            index_filter,
        )
        print_summary()
        return False

    logger.info("待采集指数数: %d", len(indices))

    for i, index_info in enumerate(indices, start=1):
        logger.info(
            "正在采集 (%d/%d): %s (%s)",
            i,
            len(indices),
            index_info["index_name"],
            index_info["index_code"],
        )

        result = fetch_daily_symbol(fetch_single_index, index_info, index_info["index_code"], config, dry_run=dry_run)

        if result["status"] == "success":
            STATS["success_count"] += 1
            STATS["total_rows"] += result["rows_inserted"]
            logger.info(
                "  -> 成功，采集 %d 行，入库 %d 行",
                result["rows_fetched"],
                result["rows_inserted"],
            )
        elif result["status"] == "warning":
            STATS["warning_count"] += 1
            STATS["success_count"] += 1
            STATS["total_rows"] += result["rows_inserted"]
            record_error_type(STATS, result["error_type"] or "data_warning")
            logger.warning("  -> 成功（有警告）: %s", result["error"])
        elif result["status"] in {"skipped", "no_new_rows"}:
            STATS["skip_count"] += 1
            record_error_type(STATS, result["error_type"] or "skipped")
            logger.warning("  -> 跳过: %s", result["error"])
        else:
            STATS["fail_count"] += 1
            record_error_type(STATS, result["error_type"] or "unknown")
            STATS["fail_details"].append({
                "index_code": result["index_code"],
                "index_name": result["index_name"],
                "error": result["error"],
                "error_type": result["error_type"] or "unknown",
            })
            logger.error(
                "  -> 失败 [%s]: %s",
                result["error_type"] or "unknown",
                result["error"],
            )

        if i < len(indices):
            time.sleep(config.stock_sleep_seconds)

    print_summary()
    if dry_run:
        logger.info("dry-run 模式完成，未写入数据库")
    return STATS["fail_count"] == 0


def main():
    """脚本入口。"""
    args = parse_args()
    if not args.dry_run and not args.preflight_only:
        logger.error("请通过 python -m scheduler.runner --chain daily --step load_index_daily --period YYYY-MM-DD 入库")
        return False
    config = build_runtime_config(args)
    return run_index_ingest(
        config,
        dry_run=args.dry_run,
        index_filter=args.index,
    )


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
