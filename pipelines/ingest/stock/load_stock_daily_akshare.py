"""使用 akshare 拉取股票日线数据到 ods_stock_daily 表。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from sqlalchemy import bindparam, text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.ingest.common.ingest_utils import (
    build_common_parser,
    execute_insert,
    make_stats,
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
STATS = make_stats("total_stocks")


def parse_args():
    """解析命令行参数。"""
    parser = build_common_parser("股票日线数据采集")
    parser.add_argument(
        "--stock",
        type=str,
        default=None,
        help="指定单个股票代码测试（如 002594.SZ）",               #
    )
    return parser.parse_args()


def get_all_stocks(codes=None):
    """读取指定的 A/H 股（含已退市）或当前上市股票。"""
    condition = "is_listed = 1" if codes is None else "market IN ('A', 'HK') AND stock_code IN :codes"
    sql = text(f"""
        SELECT id, stock_code, stock_name, market, exchange
        FROM dim_stock
        WHERE {condition}
        ORDER BY market, stock_code
    """)
    with engine.connect() as conn:
        if codes is None:
            rows = conn.execute(sql).fetchall()
        else:
            rows = conn.execute(sql.bindparams(bindparam("codes", expanding=True)), {"codes": list(codes)}).fetchall()
    return [
        {
            "stock_id": row[0],
            "stock_code": row[1],
            "stock_name": row[2],
            "market": row[3],
            "exchange": row[4],
        }
        for row in rows
    ]


def get_last_trade_date(stock_id):
    """获取指定股票在 ods_stock_daily 中的最新交易日期。"""
    sql = text("""
        SELECT MAX(trade_date) FROM ods_stock_daily
        WHERE stock_id = :stock_id
    """)
    with engine.connect() as conn:
        result = conn.execute(sql, {"stock_id": stock_id}).scalar()
    return result


def convert_a_stock_code(stock_code):
    """将 002594.SZ 转换为 akshare 需要的格式。"""
    code, market = stock_code.split(".")
    if market == "SZ":
        return f"sz{code}"
    elif market == "SH":
        return f"sh{code}"
    return stock_code


def convert_hk_stock_code(stock_code):
    """将 1211.HK 转换为 akshare 需要的格式。"""
    code = stock_code.replace(".HK", "")
    return code.zfill(5)


def convert_us_stock_code(stock_code):
    """将 NIO.US 转换为 akshare 需要的格式。"""
    code = stock_code.replace(".US", "")
    return code


def fetch_a_stock_daily(stock_code, start_date=None):
    """拉取 A 股日线数据。"""
    symbol = convert_a_stock_code(stock_code)
    return ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date or "20170101",
        end_date=datetime.now().strftime("%Y%m%d"),
        adjust="qfq",
    )


def fetch_a_stock_daily_tx(stock_code, start_date=None):
    """使用腾讯接口拉取 A 股日线数据。"""
    symbol = convert_a_stock_code(stock_code)
    return ak.stock_zh_a_hist_tx(
        symbol=symbol,
        start_date=start_date or "20170101",
        end_date=datetime.now().strftime("%Y%m%d"),
        adjust="qfq",
        timeout=15,
    )


def fetch_a_stock_daily_sina(stock_code, start_date=None):
    """使用新浪接口拉取 A 股日线数据。"""
    symbol = convert_a_stock_code(stock_code)
    return ak.stock_zh_a_daily(
        symbol=symbol,
        start_date=start_date or "20170101",
        end_date=datetime.now().strftime("%Y%m%d"),
        adjust="qfq",
    )


def fetch_a_stock_daily_with_fallback(stock_code, start_date, config: RuntimeConfig):
    errors = []
    sources = [
        ("em", fetch_a_stock_daily),
        ("tx", fetch_a_stock_daily_tx),
        ("sina", fetch_a_stock_daily_sina),
    ]
    for source_name, fetcher in sources:
        try:
            with patched_requests_runtime(config):
                return fetcher(stock_code, start_date), source_name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_name}:{exc}")
            logger.warning("A股日线源 %s 失败: %s", source_name, exc)
    raise RuntimeError(" | ".join(errors))


def fetch_hk_stock_daily(stock_code, start_date=None):
    """拉取港股日线数据。"""
    symbol = convert_hk_stock_code(stock_code)
    normalized_start = (start_date or "20170101").replace("-", "")
    return ak.stock_hk_hist(
        symbol=symbol,
        period="daily",
        start_date=normalized_start,
        end_date=datetime.now().strftime("%Y%m%d"),
        adjust="",
    )


def fetch_hk_stock_daily_sina(stock_code, start_date=None):
    """使用新浪接口拉取港股日线数据。

    新浪源全量返回历史行情。若指定 ``start_date`` 后过滤结果为空
    （通常是新浪数据延迟于当前增量窗口，例如新上市或最新交易日尚未同步），
    则回退到全量数据，让 ``ON DUPLICATE KEY UPDATE`` 幂等入库，避免出现
    “源侧有历史但增量窗口内空返回”的假失败。
    """
    symbol = convert_hk_stock_code(stock_code)
    df = ak.stock_hk_daily(symbol=symbol, adjust="")
    if df is None or df.empty:
        return df

    normalized_start = pd.to_datetime(start_date or "20170101").date()
    date_col = None
    if "date" in df.columns:
        date_col = "date"
    elif "日期" in df.columns:
        date_col = "日期"

    if date_col is None:
        return df

    filtered = df[pd.to_datetime(df[date_col]).dt.date >= normalized_start].copy()
    if filtered.empty:
        logger.warning(
            "港股新浪源过滤窗口 %s 后为空，回退到全量数据: %s",
            normalized_start,
            stock_code,
        )
        return df
    return filtered


def fetch_hk_stock_daily_with_fallback(stock_code, start_date, config: RuntimeConfig):
    errors = []
    sources = [
        ("em", fetch_hk_stock_daily),
        ("sina", fetch_hk_stock_daily_sina),
    ]
    for source_name, fetcher in sources:
        try:
            with patched_requests_runtime(config):
                return fetcher(stock_code, start_date), source_name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_name}:{exc}")
            logger.warning("港股日线源 %s 失败: %s", source_name, exc)
    raise RuntimeError(" | ".join(errors))


def fetch_us_stock_daily(stock_code, start_date=None):
    """拉取美股日线数据。"""
    symbol = convert_us_stock_code(stock_code)
    normalized_start = (start_date or "20170101").replace("-", "")
    return ak.stock_us_hist(
        symbol=symbol,
        period="daily",
        start_date=normalized_start,
        end_date=datetime.now().strftime("%Y%m%d"),
    )


def transform_daily_data(df, stock_info, source_symbol=None):
    """将 akshare 返回的数据转换为 ODS 格式。"""
    rows = []
    fetch_time = datetime.now()
    stock_id = stock_info.get("stock_id")
    stock_code = stock_info.get("stock_code")
    stock_name = stock_info.get("stock_name")
    market = stock_info.get("market")
    exchange = stock_info.get("exchange")
    source = "akshare"

    date_col = None
    if "日期" in df.columns:
        date_col = "日期"
    elif "date" in df.columns:
        date_col = "date"

    if date_col is None:
        return rows

    for _, row in df.iterrows():
        try:
            trade_date = pd.to_datetime(row[date_col]).date()
        except Exception as exc:  # noqa: BLE001
            logger.warning("转换日期失败 %s: %s", row.get(date_col), exc)
            continue

        rows.append({
            "stock_id": stock_id,
            "stock_code_raw": stock_code,
            "stock_name_raw": stock_name,
            "market_raw": market,
            "exchange_raw": exchange,
            "source_symbol": source_symbol,
            "trade_date": trade_date,
            "open": float(row["开盘"]) if pd.notna(row.get("开盘")) else (
                float(row["open"]) if pd.notna(row.get("open")) else None
            ),
            "high": float(row["最高"]) if pd.notna(row.get("最高")) else (
                float(row["high"]) if pd.notna(row.get("high")) else None
            ),
            "low": float(row["最低"]) if pd.notna(row.get("最低")) else (
                float(row["low"]) if pd.notna(row.get("low")) else None
            ),
            "close": float(row["收盘"]) if pd.notna(row.get("收盘")) else (
                float(row["close"]) if pd.notna(row.get("close")) else None
            ),
            "volume": int(row["成交量"]) if pd.notna(row.get("成交量")) else (
                int(row["volume"]) if pd.notna(row.get("volume")) else None
            ),
            "amount": float(row["成交额"]) if pd.notna(row.get("成交额")) else (
                float(row["amount"]) if pd.notna(row.get("amount")) else None
            ),
            "pct_change": float(row["涨跌幅"]) if pd.notna(row.get("涨跌幅")) else (
                float(row["pct_change"]) if pd.notna(row.get("pct_change")) else None
            ),
            "source": source,
            "fetch_time": fetch_time,
        })

    return rows


def insert_daily_data(rows):
    """将日线数据批量写入 ods_stock_daily 表。"""
    if not rows:
        return 0

    sql = """
        INSERT INTO ods_stock_daily
            (stock_id, stock_code_raw, stock_name_raw, market_raw, exchange_raw,
             source_symbol, trade_date, open, high, low, close, volume, amount,
             pct_change, source, fetch_time)
        VALUES
            (:stock_id, :stock_code_raw, :stock_name_raw, :market_raw, :exchange_raw,
             :source_symbol, :trade_date, :open, :high, :low, :close, :volume, :amount,
             :pct_change, :source, :fetch_time)
        ON DUPLICATE KEY UPDATE
            stock_id = VALUES(stock_id),
            open = VALUES(open),
            high = VALUES(high),
            low = VALUES(low),
            close = VALUES(close),
            volume = VALUES(volume),
            amount = VALUES(amount),
            pct_change = VALUES(pct_change),
            fetch_time = VALUES(fetch_time)
    """
    return execute_insert(rows, sql)


def check_daily_data_completeness(rows, last_date=None, start_date="20170101", *, bounded=False):
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

    for field in ("open", "high", "low", "close"):
        missing = sum(1 for row in rows if row.get(field) is None)
        rate = missing / total_rows
        result["missing_rates"][field] = rate
        if field == "close" and rate > 0.1:
            result["is_complete"] = False
            result["issues"].append(f"{field} 缺失率过高: {missing}/{total_rows}")
        elif field != "close" and rate > 0.3:
            result["issues"].append(f"{field} 缺失率偏高: {missing}/{total_rows}")

    if is_full_load and start_date <= "20170101" and total_rows < 30:
        result["is_complete"] = False
        result["issues"].append("首次全量返回记录过少，疑似网络/限频导致抓取不完整")

    recent_dates = sorted(
        {
            row["trade_date"]
            for row in rows
            if row.get("trade_date") and row["trade_date"] >= (datetime.now().date() - timedelta(days=400))
        }
    )
    if is_full_load and recent_dates and len(recent_dates) < 50:
        result["issues"].append("近一年交易日覆盖偏少，需人工复核是否存在限频或长期停牌")

    return result


def fetch_single_stock(stock_info, config: RuntimeConfig, dry_run=False):
    """采集单只股票的日线数据。                                          #

    Args:
        stock_info: 股票信息字典
        dry_run: 是否仅测试不写入

    Returns:
        采集结果字典，包含状态和详细信息
    """
    stock_id = stock_info["stock_id"]
    stock_code = stock_info["stock_code"]
    stock_name = stock_info["stock_name"]
    market = stock_info["market"]

    result = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "market": market,
        "status": "success",
        "rows_fetched": 0,
        "rows_inserted": 0,
        "error": None,
        "error_type": None,
        "completeness": None,
    }

    if market == "A":
        fetcher = None
    elif market == "HK":
        fetcher = fetch_hk_stock_daily
    elif market == "US":
        fetcher = fetch_us_stock_daily
        result["status"] = "skipped"
        result["error"] = "US 市场日线采集不在当前范围内"
        result["error_type"] = "unsupported_market"
        return result
    else:
        result["status"] = "skipped"
        result["error"] = f"未知市场类型: {market}"
        result["error_type"] = "unknown_market"
        return result

    # 获取增量起始日期
    requested = config.trade_dates[stock_code] if config.trade_dates is not None else None
    last_date = get_last_trade_date(stock_id) if requested is None else None
    start_date = (market_start(last_date, config.start_date, config.repair_from) if requested is None
                  else min(requested).strftime("%Y%m%d"))

    df = None
    source_name = "akshare"
    for attempt_index in range(config.max_retries):
        try:
            if market == "A":
                df, source_name = fetch_a_stock_daily_with_fallback(
                    stock_code,
                    start_date,
                    config,
                )
                source_symbol = convert_a_stock_code(stock_code)
            elif market == "HK":
                df, source_name = fetch_hk_stock_daily_with_fallback(
                    stock_code,
                    start_date,
                    config,
                )
                source_symbol = convert_hk_stock_code(stock_code)
            else:
                with patched_requests_runtime(config):
                    df = fetcher(stock_code, start_date)
                source_symbol = None
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
                stock_name,
                stock_code,
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

    if "日期" not in df.columns and "date" not in df.columns:
        result["status"] = "failed"
        result["error"] = "缺少日期字段"
        result["error_type"] = "schema_mismatch"
        return result

    result["rows_fetched"] = len(df)

    rows = transform_daily_data(df, stock_info, source_symbol=source_symbol)
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

    if source_name != "akshare":
        result["error"] = f"已切换至 {source_name} 备援源"
        result["status"] = "warning"
        result["error_type"] = "fallback_source"

    return result


def print_summary():
    """打印数据采集汇总报告。"""
    print("\n" + "=" * 60)
    print("日线数据采集汇总报告")
    print("=" * 60)
    if STATS["preflight"]:
        print(f"预检状态: {STATS['preflight']['status']}")
        print(f"预检说明: {STATS['preflight']['message']}")
    print(f"总股票数: {STATS['total_stocks']}")
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
                f"  - {item['stock_code']} ({item['stock_name']}): "
                f"[{item['error_type']}] {item['error']}"
            )

    print("=" * 60 + "\n")


def run_daily_preflight(config: RuntimeConfig) -> dict:
    preflight = run_preflight_check(config)
    if preflight["ok"]:
        return preflight

    try:
        probe_start_date = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
        df, source_name = fetch_a_stock_daily_with_fallback(
            "002594.SZ",
            probe_start_date,
            config,
        )
        if df is not None and not df.empty:
            return {
                "ok": True,
                "status": "ok",
                "url": preflight["url"],
                "message": (
                    f"默认东财预检失败，但真实抓取已通过 {source_name} 备援源验证: "
                    f"{preflight['message']}"
                ),
                "using_proxy": preflight["using_proxy"],
                "direct_domains": preflight["direct_domains"],
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("日线真实抓取预检失败: %s", exc)

    return preflight


def run_daily_ingest(config: RuntimeConfig, dry_run=False, stock_code: str | None = None) -> bool:
    reset_stats(STATS, "total_stocks")
    setup_proxy(config)

    logger.info("开始采集股票日线数据...")
    logger.info("当前代理: %s", config.proxy_url or "未显式设置")
    logger.info("直连域名: %s", ",".join(config.direct_domains))

    preflight = run_daily_preflight(config)
    STATS["preflight"] = preflight
    if not preflight["ok"]:
        if config.trade_dates is None or config.preflight_only:
            logger.error("网络预检失败: [%s] %s", preflight["status"], preflight["message"])
            print_summary()
            return False
        logger.warning("网络预检未通过: [%s] %s，将尝试本批应采标的", preflight["status"], preflight["message"])

    if config.preflight_only:
        logger.info("仅执行预检，未启动采集")
        print_summary()
        return True

    stocks = get_all_stocks() if config.trade_dates is None else get_all_stocks(config.trade_dates)
    STATS["total_stocks"] = len(stocks)

    if stock_code:
        stocks = [item for item in stocks if item["stock_code"] == stock_code]
        STATS["total_stocks"] = len(stocks)
        if not stocks:
            logger.error("未找到股票: %s", stock_code)
            return False

    logger.info("待采集股票数: %d", len(stocks))

    for index, stock in enumerate(stocks, start=1):
        logger.info(
            "正在采集 (%d/%d): %s (%s)",
            index,
            len(stocks),
            stock["stock_name"],
            stock["stock_code"],
        )

        result = fetch_daily_symbol(fetch_single_stock, stock, stock["stock_code"], config, dry_run=dry_run)

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
                "stock_code": result["stock_code"],
                "stock_name": result["stock_name"],
                "error": result["error"],
                "error_type": result["error_type"] or "unknown",
            })
            logger.error(
                "  -> 失败 [%s]: %s",
                result["error_type"] or "unknown",
                result["error"],
            )

        if index < len(stocks):
            time.sleep(config.stock_sleep_seconds)

    print_summary()
    if dry_run:
        logger.info("dry-run 模式完成，未写入数据库")
    return STATS["fail_count"] == 0


def main():
    """脚本入口。"""
    args = parse_args()
    if not args.dry_run and not args.preflight_only:
        logger.error("请通过 python -m scheduler.runner --chain daily --step load_stock_daily --period YYYY-MM-DD 入库")
        return False
    config = build_runtime_config(args)
    return run_daily_ingest(config, dry_run=args.dry_run, stock_code=args.stock)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
