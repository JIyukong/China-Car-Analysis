"""使用 akshare 拉取商品期货主力连续日线数据到 ods_futures_daily 表。

首期覆盖碳酸锂 LC0 / 铜 CU0 / 铝 AL0，作为整车金属 BOM 成本基准。
数据源: ak.futures_main_sina(symbol=主力连续 symbol, start_date, end_date)
"""

from __future__ import annotations

import time
from datetime import datetime

import akshare as ak
import pandas as pd
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
from pipelines.ingest.common.market_quality import market_start, valid_price_rows
from pipelines.ingest.stock.network_runtime import (
    RuntimeConfig,
    build_runtime_config,
    classify_fetch_error,
    fetch_daily_symbol,
    patched_requests_runtime,
    run_preflight_check,
    setup_proxy,
    sleep_before_retry,
    write_daily_rows,
)

logger = get_logger(__name__)

# 数据采集结果统计
STATS = make_stats("total_commodities")


def parse_args():
    """解析命令行参数。"""
    parser = build_common_parser("商品期货主力连续日线数据采集")
    parser.add_argument(
        "--commodity",
        type=str,
        default=None,
        help="指定单个品种代码（如 LC/CU/AL），默认全量采集 dim_commodity",
    )
    parser.set_defaults(start_date="19990101")
    return parser.parse_args()


def get_all_commodities(commodity_filter: str | None = None):
    """从 dim_commodity 表获取所有启用中的品种。"""
    sql = text("""
        SELECT id, commodity_code, commodity_name, exchange, category,
               trading_unit, quote_unit, main_symbol, listed_date
        FROM dim_commodity
        WHERE is_active = 1
        ORDER BY exchange, commodity_code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    items = [
        {
            "commodity_id": row[0],
            "commodity_code": row[1],
            "commodity_name": row[2],
            "exchange": row[3],
            "category": row[4],
            "trading_unit": row[5],
            "quote_unit": row[6],
            "main_symbol": row[7],
            "listed_date": row[8],
        }
        for row in rows
    ]

    if commodity_filter:
        code = commodity_filter.upper()
        items = [
            item
            for item in items
            if item["commodity_code"] == code
        ]
    return items


def get_last_trade_date(commodity_id):
    """获取指定品种主力连续在 ods_futures_daily 中的最新交易日期。"""
    sql = text("""
        SELECT MAX(trade_date) FROM ods_futures_daily
        WHERE commodity_id = :commodity_id
          AND contract_type = 'main_continuous'
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, {"commodity_id": commodity_id}).scalar()
    return result


def fetch_futures_main_daily(main_symbol: str, start_date: str):
    """使用 akshare 拉取主力连续日线数据。

    Args:
        main_symbol: 主力连续 symbol（如 LC0/CU0/AL0）
        start_date: 起始日期 YYYYMMDD
    """
    end_date = datetime.now().strftime("%Y%m%d")
    return ak.futures_main_sina(
        symbol=main_symbol,
        start_date=start_date,
        end_date=end_date,
    )


def transform_daily_data(df, commodity_info):
    """将 akshare 返回的数据转换为 ODS 格式。"""
    rows = []
    fetch_time = datetime.now()
    commodity_id = commodity_info.get("commodity_id")
    commodity_code = commodity_info.get("commodity_code")
    commodity_name = commodity_info.get("commodity_name")
    main_symbol = commodity_info.get("main_symbol")
    exchange = commodity_info.get("exchange")

    # 兼容中英文列名
    date_col = None
    for cand in ("日期", "date", "trade_date"):
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
            "commodity_id": commodity_id,
            "commodity_code_raw": commodity_code,
            "commodity_name_raw": commodity_name,
            "main_symbol_raw": main_symbol,
            "exchange_raw": exchange,
            "contract_type": "main_continuous",
            "trade_date": trade_date,
            "open": pick_float(row, ("开盘价", "open")),
            "high": pick_float(row, ("最高价", "high")),
            "low": pick_float(row, ("最低价", "low")),
            "close": pick_float(row, ("收盘价", "close")),
            "volume": pick_int(row, ("成交量", "volume")),
            "open_interest": pick_int(row, ("持仓量", "hold", "open_interest")),
            "settle": pick_float(row, ("动态结算价", "settle")),
            "amount": None,
            "source": "akshare",
            "fetch_time": fetch_time,
        })

    return rows


def insert_daily_data(rows):
    """将日线数据批量写入 ods_futures_daily 表。"""
    sql = """
        INSERT INTO ods_futures_daily
            (commodity_id, commodity_code_raw, commodity_name_raw,
             main_symbol_raw, exchange_raw, contract_type, trade_date,
             open, high, low, close, volume, open_interest, settle, amount,
             source, fetch_time)
        VALUES
            (:commodity_id, :commodity_code_raw, :commodity_name_raw,
             :main_symbol_raw, :exchange_raw, :contract_type, :trade_date,
             :open, :high, :low, :close, :volume, :open_interest, :settle, :amount,
             :source, :fetch_time)
        ON DUPLICATE KEY UPDATE
            commodity_id   = VALUES(commodity_id),
            open           = VALUES(open),
            high           = VALUES(high),
            low            = VALUES(low),
            close          = VALUES(close),
            volume         = VALUES(volume),
            open_interest  = VALUES(open_interest),
            settle         = VALUES(settle),
            amount         = VALUES(amount),
            fetch_time     = VALUES(fetch_time)
    """
    return execute_insert(rows, sql)


def check_daily_data_completeness(rows, last_date=None, start_date="19990101", *, bounded=False):
    """完整性校验：close/settle/open_interest 缺失率、首次全量行数。"""
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

    # close 缺失率                                              # 收盘价必填
    close_missing = sum(1 for row in rows if row.get("close") is None)
    close_rate = close_missing / total_rows
    result["missing_rates"]["close"] = close_rate
    if close_rate > 0.1:
        result["is_complete"] = False
        result["issues"].append(
            f"close 缺失率过高: {close_missing}/{total_rows}"
        )

    # settle 缺失率（期货特有核心字段）                          # 结算价必填
    settle_missing = sum(1 for row in rows if row.get("settle") is None)
    settle_rate = settle_missing / total_rows
    result["missing_rates"]["settle"] = settle_rate
    if settle_rate > 0.1:
        result["is_complete"] = False
        result["issues"].append(
            f"settle 缺失率过高: {settle_missing}/{total_rows}"
        )

    # open_interest 缺失率                                       # 持仓量必填
    oi_missing = sum(1 for row in rows if row.get("open_interest") is None)
    oi_rate = oi_missing / total_rows
    result["missing_rates"]["open_interest"] = oi_rate
    if oi_rate > 0.1:
        result["issues"].append(
            f"open_interest 缺失率偏高: {oi_missing}/{total_rows}"
        )

    if is_full_load and start_date <= "19990101" and total_rows < 20:
        result["is_complete"] = False
        result["issues"].append("首次全量返回记录过少，疑似网络/限频导致抓取不完整")

    return result


def fetch_single_commodity(commodity_info, config: RuntimeConfig, dry_run=False):
    """采集单个品种的主力连续日线。"""
    commodity_id = commodity_info["commodity_id"]
    commodity_code = commodity_info["commodity_code"]
    commodity_name = commodity_info["commodity_name"]
    main_symbol = commodity_info["main_symbol"]

    result = {
        "commodity_code": commodity_code,
        "commodity_name": commodity_name,
        "main_symbol": main_symbol,
        "status": "success",
        "rows_fetched": 0,
        "rows_inserted": 0,
        "error": None,
        "error_type": None,
        "completeness": None,
    }

    # 获取增量起始日期
    requested = config.trade_dates[commodity_code] if config.trade_dates is not None else None
    last_date = get_last_trade_date(commodity_id) if requested is None else None
    start_date = (market_start(last_date, config.start_date, config.repair_from) if requested is None
                  else min(requested).strftime("%Y%m%d"))

    df = None
    for attempt_index in range(config.max_retries):
        try:
            with patched_requests_runtime(config):
                df = fetch_futures_main_daily(main_symbol, start_date)
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
                commodity_name,
                main_symbol,
                error_type,
                attempt_index + 1,
                config.max_retries,
                delay,
                exc,
            )

    if df is None or df.empty:
        if df is not None and requested is not None:
            result.update(status="no_new_rows", error="接口尚未返回请求日期的数据")
            return result
        result["status"] = "failed"
        result["error"] = "接口返回空数据"
        result["error_type"] = "empty_payload"
        return result

    date_candidates = ("日期", "date", "trade_date")
    if not any(c in df.columns for c in date_candidates):
        result["status"] = "failed"
        result["error"] = "缺少日期字段"
        result["error_type"] = "schema_mismatch"
        return result

    result["rows_fetched"] = len(df)

    rows = transform_daily_data(df, commodity_info)
    if requested is not None and rows:
        try:
            rows = valid_price_rows(rows, requested)
        except ValueError as exc:
            result.update(status="failed", error_type="invalid_rows", error=str(exc))
            return result
        if not rows:
            result["status"] = "no_new_rows"
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
    print("商品期货主力连续日线采集汇总报告")
    print("=" * 60)
    if STATS["preflight"]:
        print(f"预检状态: {STATS['preflight']['status']}")
        print(f"预检说明: {STATS['preflight']['message']}")
    print(f"总品种数: {STATS['total_commodities']}")
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
                f"  - {item['commodity_code']} ({item['commodity_name']}): "
                f"[{item['error_type']}] {item['error']}"
            )

    print("=" * 60 + "\n")


def run_futures_ingest(
    config: RuntimeConfig,
    dry_run: bool = False,
    commodity_filter: str | None = None,
) -> bool:
    """采集主流程。"""
    reset_stats(STATS, "total_commodities")
    setup_proxy(config)

    logger.info("开始采集商品期货主力连续日线数据...")
    logger.info("当前代理: %s", config.proxy_url or "未显式设置")
    logger.info("直连域名: %s", ",".join(config.direct_domains))

    # 复用东财通用预检；失败不阻断（新浪可能有独立链路）
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

    commodities = get_all_commodities(commodity_filter=commodity_filter)
    if config.trade_dates is not None:
        commodities = [item for item in commodities if item["commodity_code"] in config.trade_dates]
    STATS["total_commodities"] = len(commodities)

    if not commodities:
        logger.error(
            "dim_commodity 中未找到匹配的品种记录，commodity_filter=%s",
            commodity_filter,
        )
        print_summary()
        return False

    logger.info("待采集品种数: %d", len(commodities))

    for i, commodity in enumerate(commodities, start=1):
        logger.info(
            "正在采集 (%d/%d): %s (%s)",
            i,
            len(commodities),
            commodity["commodity_name"],
            commodity["main_symbol"],
        )

        result = fetch_daily_symbol(fetch_single_commodity, commodity, commodity["commodity_code"], config, dry_run=dry_run)

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
                "commodity_code": result["commodity_code"],
                "commodity_name": result["commodity_name"],
                "error": result["error"],
                "error_type": result["error_type"] or "unknown",
            })
            logger.error(
                "  -> 失败 [%s]: %s",
                result["error_type"] or "unknown",
                result["error"],
            )

        if i < len(commodities):
            time.sleep(config.stock_sleep_seconds)

    print_summary()
    if dry_run:
        logger.info("dry-run 模式完成，未写入数据库")
    return STATS["fail_count"] == 0


def main():
    """脚本入口。"""
    args = parse_args()
    if not args.dry_run and not args.preflight_only:
        logger.error("请通过 python -m scheduler.runner --chain daily --step load_futures_daily --period YYYY-MM-DD 入库")
        return False
    config = build_runtime_config(args)
    return run_futures_ingest(
        config,
        dry_run=args.dry_run,
        commodity_filter=args.commodity,
    )


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
