"""使用 akshare 拉取股票财务数据到 ods_stock_finance 表。"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

import akshare as ak
import pandas as pd
from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.ingest.stock.network_runtime import (
    RuntimeConfig,
    build_runtime_config,
    classify_fetch_error,
    patched_requests_runtime,
    run_preflight_check,
    setup_proxy,
    sleep_before_retry,
)

logger = get_logger(__name__)

FINANCE_PREFLIGHT_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbDateAjaxNew"
    "?companyType=4&reportDateType=0&code=SZ002594"
)

# 数据采集结果统计
STATS = {
    "total_stocks": 0,
    "success_count": 0,
    "fail_count": 0,
    "skip_count": 0,
    "warning_count": 0,
    "total_rows": 0,
    "fail_details": [],
    "error_types": {},
    "preflight": None,
}


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="使用 akshare 拉取股票财务数据到 ODS"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅测试连接，不写入数据库",                          #
    )
    parser.add_argument(
        "--stock",
        type=str,
        default=None,
        help="指定单个股票代码测试（如 002594.SZ）",               #
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="代理服务器地址（如 socks5://127.0.0.1:6789 或 http://127.0.0.1:7890）",
    )
    parser.add_argument(
        "--direct-domains",
        type=str,
        default=None,
        help="需要直连而不走代理的域名，逗号分隔",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="HTTP 请求超时时间（秒）",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="单只股票最大重试次数",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="股票之间与重试前的基础等待秒数",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="仅做网络预检，不执行采集",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="20170101",
        help="保持与日线脚本一致的全量起始日期参数",
    )
    return parser.parse_args()


def get_all_stocks():
    """从 dim_stock 表获取所有股票信息。"""
    sql = text("""
        SELECT id, stock_code, stock_name, market, exchange
        FROM dim_stock
        WHERE is_listed = 1
        ORDER BY market, stock_code
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
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
    return code


def get_report_type(report_date):
    """根据报告期判断报告类型（季报/年报）。"""
    month = report_date.month
    if month == 3:
        return "Q1"
    elif month == 6:
        return "Q2"
    elif month == 9:
        return "Q3"
    elif month == 12:
        return "年报"
    return "年报"


def fetch_a_stock_finance(stock_code):
    """拉取 A 股财务数据（利润表）。"""
    symbol = convert_a_stock_code(stock_code)
    try:
        # 获取利润表数据
        df_income = ak.stock_profit_sheet_by_report_em(symbol=symbol)
        return df_income
    except Exception as e:
        logger.warning("获取利润表失败: %s", e)
        return None


def transform_finance_data(df, stock_info):
    """将 akshare 返回的财务数据转换为 ODS 格式。

    Args:
        df: akshare 返回的原始 DataFrame
        stock_info: 股票信息字典，包含 stock_id / stock_code / stock_name / market / exchange

    Returns:
        转换后的行列表
    """
    rows = []
    fetch_time = datetime.now()
    stock_id = stock_info["stock_id"]
    stock_code = stock_info["stock_code"]
    stock_name = stock_info["stock_name"]
    market = stock_info["market"]
    exchange = stock_info["exchange"]
    source_symbol = convert_a_stock_code(stock_code) if stock_code else None

    if df is None or df.empty:
        return rows

    # 遍历每行数据
    for _, row in df.iterrows():
        try:
            # 获取报告期
            report_date = (
                row.get("日期")
                or row.get("报告日期")
                or row.get("REPORT_DATE")
            )
            if report_date is None:
                continue

            # 转换日期
            report_date = pd.to_datetime(report_date).date()

            # 判断报告类型
            report_type = get_report_type(report_date)

            rows.append({
                # 原始自然键快照（ODS 原则）
                "stock_id": stock_id,
                "stock_code_raw": stock_code,
                "stock_name_raw": stock_name,
                "market_raw": market,
                "exchange_raw": exchange,
                "source_symbol": source_symbol,
                # 报告信息
                "report_date": report_date,
                "report_type": report_type,
                # 收益指标
                "eps": float(row["基本每股收益"]) if pd.notna(row.get("基本每股收益")) else (
                    float(row["BASIC_EPS"]) if pd.notna(row.get("BASIC_EPS")) else None
                ),
                "net_profit": float(row["净利润"]) if pd.notna(row.get("净利润")) else (
                    float(row["NETPROFIT"]) if pd.notna(row.get("NETPROFIT")) else None
                ),
                # 收入指标
                "total_revenue": float(row["营业总收入"]) if pd.notna(row.get("营业总收入")) else (
                    float(row["TOTAL_OPERATE_INCOME"]) if pd.notna(row.get("TOTAL_OPERATE_INCOME")) else None
                ),
                "main_revenue": float(row["主营业务收入"]) if pd.notna(row.get("主营业务收入")) else (
                    float(row["OPERATE_INCOME"]) if pd.notna(row.get("OPERATE_INCOME")) else None
                ),
                "total_profit": float(row["利润总额"]) if pd.notna(row.get("利润总额")) else (
                    float(row["TOTAL_PROFIT"]) if pd.notna(row.get("TOTAL_PROFIT")) else None
                ),
                # 资产负债指标（财务数据中可能没有）
                "total_assets": float(row.get("资产总计")) if pd.notna(row.get("资产总计")) else (
                    float(row.get("TOTAL_ASSETS")) if pd.notna(row.get("TOTAL_ASSETS")) else None
                ),
                "total_liabilities": float(row.get("负债合计")) if pd.notna(row.get("负债合计")) else (
                    float(row.get("TOTAL_LIABILITIES")) if pd.notna(row.get("TOTAL_LIABILITIES")) else None
                ),
                "equity": float(row.get("所有者权益合计")) if pd.notna(row.get("所有者权益合计")) else (
                    float(row.get("TOTAL_EQUITY")) if pd.notna(row.get("TOTAL_EQUITY")) else None
                ),
                # 比率指标（财务数据中可能没有）
                "roe": float(row.get("净资产收益率")) if pd.notna(row.get("净资产收益率")) else (
                    float(row.get("ROE")) if pd.notna(row.get("ROE")) else None
                ),
                "gross_margin": float(row.get("销售毛利率")) if pd.notna(row.get("销售毛利率")) else (
                    float(row.get("GROSS_PROFIT_MARGIN")) if pd.notna(row.get("GROSS_PROFIT_MARGIN")) else None
                ),
                "debt_ratio": float(row.get("资产负债率")) if pd.notna(row.get("资产负债率")) else (
                    float(row.get("DEBT_ASSET_RATIO")) if pd.notna(row.get("DEBT_ASSET_RATIO")) else None
                ),
                # 元数据
                "source": "akshare",
                "fetch_time": fetch_time,
            })
        except Exception as e:
            logger.warning("转换行数据失败: %s", e)
            continue

    return rows


def insert_finance_data(rows):
    """将财务数据批量写入 ods_stock_finance 表。"""
    if not rows:
        return 0

    sql = text("""
        INSERT INTO ods_stock_finance
            (stock_id, stock_code_raw, stock_name_raw, market_raw, exchange_raw,
             source_symbol, report_date, report_type, eps, net_profit,
             total_revenue, main_revenue, total_profit,
             total_assets, total_liabilities, equity,
             roe, gross_margin, debt_ratio, source, fetch_time)
        VALUES
            (:stock_id, :stock_code_raw, :stock_name_raw, :market_raw, :exchange_raw,
             :source_symbol, :report_date, :report_type, :eps, :net_profit,
             :total_revenue, :main_revenue, :total_profit,
             :total_assets, :total_liabilities, :equity,
             :roe, :gross_margin, :debt_ratio, :source, :fetch_time)
        ON DUPLICATE KEY UPDATE
            stock_id = VALUES(stock_id),
            stock_name_raw = VALUES(stock_name_raw),
            market_raw = VALUES(market_raw),
            exchange_raw = VALUES(exchange_raw),
            source_symbol = VALUES(source_symbol),
            eps = VALUES(eps),
            net_profit = VALUES(net_profit),
            total_revenue = VALUES(total_revenue),
            main_revenue = VALUES(main_revenue),
            total_profit = VALUES(total_profit),
            total_assets = VALUES(total_assets),
            total_liabilities = VALUES(total_liabilities),
            equity = VALUES(equity),
            roe = VALUES(roe),
            gross_margin = VALUES(gross_margin),
            debt_ratio = VALUES(debt_ratio),
            fetch_time = VALUES(fetch_time)
    """)

    with engine.begin() as conn:
        conn.execute(sql, rows)

    return len(rows)


def reset_stats() -> None:
    STATS.update({
        "total_stocks": 0,
        "success_count": 0,
        "fail_count": 0,
        "skip_count": 0,
        "warning_count": 0,
        "total_rows": 0,
        "fail_details": [],
        "error_types": {},
        "preflight": None,
    })


def _record_error_type(error_type: str) -> None:
    STATS["error_types"][error_type] = STATS["error_types"].get(error_type, 0) + 1


def check_data_completeness(rows, is_full_load=False):
    """检查财务数据完整性。

    Args:
        rows: 转换后的数据列表

    Returns:
        dict: 包含完整性检查结果
    """
    result = {
        "total_rows": len(rows),
        "key_fields_missing": [],
        "report_years": set(),
        "report_types": set(),
        "is_complete": True,
    }

    if not rows:
        result["is_complete"] = False
        return result

    # 收集报告年份和类型
    for row in rows:
        if row["report_date"]:
            result["report_years"].add(row["report_date"].year)
        if row["report_type"]:
            result["report_types"].add(row["report_type"])

    # 检查关键指标缺失情况
    eps_missing = sum(1 for r in rows if r["eps"] is None)
    profit_missing = sum(1 for r in rows if r["net_profit"] is None)
    revenue_missing = sum(1 for r in rows if r["total_revenue"] is None)

    # 缺失率超过 30% 则记录
    if eps_missing > len(rows) * 0.3:
        result["key_fields_missing"].append(f"eps缺失率: {eps_missing}/{len(rows)}")
    if profit_missing > len(rows) * 0.3:
        result["key_fields_missing"].append(f"净利润缺失率: {profit_missing}/{len(rows)}")
    if revenue_missing > len(rows) * 0.3:
        result["key_fields_missing"].append(f"营业收入缺失率: {revenue_missing}/{len(rows)}")

    if is_full_load and len(rows) < 4:
        result["key_fields_missing"].append("首次全量返回报告期过少，疑似抓取不完整")
        result["is_complete"] = False

    if len(result["report_types"]) < 2:
        result["key_fields_missing"].append("报告类型分布偏少，需人工复核")

    # 检查是否至少有近4年数据（每年4个季度报告）
    current_year = datetime.now().year
    min_year = current_year - 4
    missing_years = [y for y in range(min_year, current_year) if y not in result["report_years"]]
    if missing_years:
        result["key_fields_missing"].append(f"缺少以下年份数据: {missing_years}")
        result["is_complete"] = False

    return result


def fetch_single_stock(stock_info, config: RuntimeConfig, dry_run=False):
    """采集单只股票的财务数据。                                       #

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

    if market == "US":
        result["status"] = "skipped"
        result["error"] = "US 市场财务数据当前不在采集范围内"
        result["error_type"] = "unsupported_market"
        return result

    if market != "A":
        result["status"] = "skipped"
        result["error"] = f"暂不支持 {market} 市场财务数据采集"
        result["error_type"] = "unsupported_market"
        return result

    df = None
    for attempt_index in range(config.max_retries):
        try:
            with patched_requests_runtime(config):
                df = fetch_a_stock_finance(stock_code)
            break
        except Exception as exc:  # noqa: BLE001
            result["error_type"] = classify_fetch_error(exc)
            result["error"] = str(exc)
            if attempt_index == config.max_retries - 1:
                result["status"] = "failed"
                return result

            delay = sleep_before_retry(config, attempt_index)
            logger.warning(
                "采集财务数据失败，股票=%s (%s)，类型=%s，第 %d/%d 次重试前等待 %.2f 秒: %s",
                stock_name,
                stock_code,
                result["error_type"],
                attempt_index + 1,
                config.max_retries,
                delay,
                exc,
            )

    if df is None or df.empty:
        result["status"] = "failed"
        result["error"] = "接口返回空数据"
        result["error_type"] = "empty_payload"
        return result

    result["rows_fetched"] = len(df)
    rows = transform_finance_data(df, stock_info)
    if not rows:
        result["status"] = "failed"
        result["error"] = "数据转换后为空"
        result["error_type"] = "empty_payload"
        return result

    completeness = check_data_completeness(rows, is_full_load=True)
    result["completeness"] = completeness

    if not completeness["is_complete"]:
        result["status"] = "failed"
        result["error"] = "; ".join(completeness["key_fields_missing"])
        result["error_type"] = "data_incomplete"
        return result
    if completeness["key_fields_missing"]:
        result["status"] = "warning"
        result["error"] = "; ".join(completeness["key_fields_missing"])
        result["error_type"] = "data_warning"

    if not dry_run:
        result["rows_inserted"] = insert_finance_data(rows)

    return result


def print_summary():
    """打印数据采集汇总报告。"""
    print("\n" + "=" * 60)
    print("财务数据采集汇总报告")
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


def run_finance_ingest(config: RuntimeConfig, dry_run=False, stock_code=None) -> bool:
    reset_stats()
    setup_proxy(config)

    logger.info("开始采集股票财务数据...")
    logger.info("当前代理: %s", config.proxy_url or "未显式设置")
    logger.info("直连域名: %s", ",".join(config.direct_domains))

    preflight = run_preflight_check(
        config,
        preflight_url=FINANCE_PREFLIGHT_URL,
        response_validator=lambda payload: bool(payload.get("data")),
    )
    STATS["preflight"] = preflight
    if not preflight["ok"]:
        logger.error("网络预检失败: [%s] %s", preflight["status"], preflight["message"])
        print_summary()
        return False

    if config.preflight_only:
        logger.info("仅执行预检，未启动财务采集")
        print_summary()
        return True

    stocks = get_all_stocks()
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
            "正在采集财务数据 (%d/%d): %s (%s)",
            index,
            len(stocks),
            stock["stock_name"],
            stock["stock_code"],
        )

        result = fetch_single_stock(stock, config=config, dry_run=dry_run)
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
            _record_error_type(result["error_type"] or "data_warning")
            logger.warning("  -> 成功（有警告）: %s", result["error"])
        elif result["status"] == "skipped":
            STATS["skip_count"] += 1
            _record_error_type(result["error_type"] or "skipped")
            logger.warning("  -> 跳过: %s", result["error"])
        else:
            STATS["fail_count"] += 1
            _record_error_type(result["error_type"] or "unknown")
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
    config = build_runtime_config(args)
    return run_finance_ingest(config, dry_run=args.dry_run, stock_code=args.stock)


if __name__ == "__main__":
    main()
