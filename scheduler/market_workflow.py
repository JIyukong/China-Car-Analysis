"""Calendar-aware ODS coverage and bounded stock publication."""

import logging
from datetime import date, timedelta
from importlib import import_module

from filelock import Timeout

from pipelines.ingest.stock.network_runtime import DailyFetch
from scheduler.workflow import State

logger = logging.getLogger(__name__)
FINANCE = "load_stock_finance"
STOCK = "load_stock_daily"

_SOURCES = {
    STOCK: (
        "stock.load_stock_daily_akshare", "run_daily_ingest", "ods_stock_daily", "stock_code_raw",
        "SELECT stock_code, CASE market WHEN 'HK' THEN 'HKEX' ELSE 'SSE' END,"
        " listed_date, delisted_date FROM dim_stock WHERE market IN ('A','HK')"
        " AND (is_listed=1 OR delisted_date IS NOT NULL)",
    ),
    "load_futures_daily": (
        "futures.load_futures_main_sina", "run_futures_ingest", "ods_futures_daily", "commodity_code_raw",
        "SELECT commodity_code, exchange, listed_date, NULL FROM dim_commodity"
        " WHERE is_active=1 AND main_symbol IS NOT NULL",
    ),
    "load_index_daily": (
        "index.load_ths_index_daily_akshare", "run_index_ingest", "ods_index_daily", "index_code_raw",
        "SELECT index_code, 'SSE', NULL, NULL FROM dim_index"
        " WHERE is_active=1 AND index_source='THS' AND COALESCE(market, 'CN')='CN'",
    ),
}


def sessions(
    day: date, instruments: list[tuple[str, str, date | None]],
    calendar: dict[tuple[str, date], bool], suspended: set[tuple[str, date]],
) -> tuple[set[str], set[str]]:
    expected = set()
    unknown = set()
    for code, market, listed in instruments:
        if listed is not None and listed > day:
            continue
        opened = calendar.get((market, day))
        if opened is None:
            unknown.add(market)
        elif opened and (code, day) not in suspended:
            expected.add(code)
    return expected, unknown


def expected_sessions(task: str, days: list[date]) -> tuple[dict, dict]:
    from sqlalchemy import text

    from base_infra.db import engine

    params = {"first": min(days), "last": max(days)}
    with engine.connect() as conn:
        instruments = list(conn.execute(text(_SOURCES[task][4])))
        if not instruments:
            raise ValueError("未配置启用的采集标的")
        # The legacy and supplemental tables can use different MySQL collations.
        calendar = {}
        for statement in (
            """SELECT market, trade_date, is_open FROM dim_trade_calendar
               WHERE market='SSE' AND trade_date BETWEEN :first AND :last""",
            """SELECT market, trade_date, is_open FROM etl_market_calendar
               WHERE market <> 'SSE' AND trade_date BETWEEN :first AND :last""",
        ):
            for market, day, is_open in conn.execute(text(statement), params):
                calendar[market, day] = bool(is_open)
        suspended = set()
        if task == STOCK:
            suspended = {tuple(row) for row in conn.execute(text("""
                SELECT stock_code, trade_date FROM etl_stock_suspension
                WHERE trade_date BETWEEN :first AND :last
            """), params)}
    expected, unknown = {}, {}
    for day in days:
        eligible = [(code, market, listed) for code, market, listed, end in instruments if end is None or end >= day]
        expected[day], unknown[day] = sessions(day, eligible, calendar, suspended)
    return expected, unknown


def actual_sessions(task: str, days: list[date]) -> dict[date, set[str]]:
    from sqlalchemy import bindparam, text

    from base_infra.db import engine

    _, _, table, code, _ = _SOURCES[task]
    extra = " AND contract_type='main_continuous'" if task == "load_futures_daily" else ""
    stmt = text(f"""
        SELECT trade_date, {code} FROM {table}
        WHERE trade_date IN :days AND source='akshare' {extra}
          AND `open` > 0 AND `close` > 0 AND low > 0
          AND low <= LEAST(`open`, `close`) AND high >= GREATEST(`open`, `close`)
          AND (volume IS NULL OR volume >= 0) AND (amount IS NULL OR amount >= 0)
    """).bindparams(bindparam("days", expanding=True))
    result = {day: set() for day in days}
    with engine.connect() as conn:
        for day, symbol in conn.execute(stmt, {"days": days}):
            result[day].add(symbol)
    return result


def ingest_daily(task: str, needed: dict[str, frozenset[date]], earliest: date) -> DailyFetch:
    from scheduler.jobs.common import build_scheduler_config

    module, function, *_ = _SOURCES[task]
    collector = getattr(import_module(f"pipelines.ingest.{module}"), function)
    config = build_scheduler_config(start_date="19990101" if task == "load_futures_daily" else "20170101")
    config.repair_from = earliest.strftime("%Y%m%d")
    config.trade_dates = needed
    report = config.daily_fetch = DailyFetch()
    unattempted = "未采集，请检查来源预检与标的配置"
    try:
        collector(config)
    except Exception as exc:
        logger.exception("%s collection stopped", task)
        unattempted = type(exc).__name__
    for code in needed.keys() - report.observed.keys() - report.failures.keys():
        report.failures[code] = unattempted
    return report


def publish_daily(task: str, day: date, expected: set[str]) -> bool:
    from base_infra.config import get_settings

    if task == STOCK and get_settings().ETL_STOCK_DWD_ENABLED:
        from pipelines.staging.clean_stock_daily_dwd import publish_stock_day

        publish_stock_day(day, expected)
        return True
    return False


def _dates(state: State, task: str, day: date, catch_up: bool) -> list[date]:
    if not catch_up:
        return [day]
    rows = state.batches(task)
    from base_infra.config import get_settings

    terminal = {"published", "closed"}
    if task != STOCK or not get_settings().ETL_STOCK_DWD_ENABLED:
        terminal.add("ods_accepted")
    start = date.fromisoformat(rows[0]["period"]) if rows else day - timedelta(days=6)
    # Recheck yesterday alongside today's data to catch late rows and prior-close corrections.
    start = min(start, day - timedelta(days=1))
    dates = {start + timedelta(days=offset) for offset in range((day - start).days + 1)}
    for row in rows:
        period = date.fromisoformat(row["period"])
        if period <= day and row["status"] not in terminal:
            dates.add(period)
    return sorted(dates)


def _daily(state: State, task: str, dates: list[date]) -> bool:
    expected, unknown = expected_sessions(task, dates)
    requested: dict[str, set[date]] = {}
    for day, codes in expected.items():
        for code in codes:
            requested.setdefault(code, set()).add(day)
    report = ingest_daily(task, {code: frozenset(days) for code, days in requested.items()}, min(dates)) if requested else DailyFetch()
    observed_dates = sorted({day for observed in report.observed.values() for day in observed})
    actual = actual_sessions(task, observed_dates) if observed_dates else {}
    complete = True
    for day in dates:
        period = day.isoformat()
        source_missing = {code for code in expected[day] if day not in report.observed.get(code, ())}
        failed = source_missing & report.failures.keys()
        ods_missing = expected[day] - source_missing - actual.get(day, set())
        if unknown[day]:
            state.save(task, period, "waiting_calendar", message="缺交易日历: " + ", ".join(sorted(unknown[day])))
        elif not expected[day]:
            state.save(task, period, "closed", message="休市或无应采标的（含已确认停牌）")
            continue
        elif failed or ods_missing:
            failures = [f"{code} ({report.failures[code]})" for code in sorted(failed)[:15]]
            if ods_missing:
                failures.append("ODS 未查到有效记录 " + ", ".join(sorted(ods_missing)[:15]))
            state.save(task, period, "source_error", message="采集或入库失败: " + ", ".join(failures))
        elif source_missing:
            state.save(
                task, period, "waiting_source",
                message=f"本次源响应缺 {len(source_missing)} 个应采标的: " + ", ".join(sorted(source_missing)[:15]),
            )
        else:
            try:
                published = publish_daily(task, day, expected[day])
            except Exception as exc:
                logger.exception("%s %s publication failed", task, day)
                state.save(task, period, "publish_error", message=type(exc).__name__)
            else:
                status = "published" if published else "ods_accepted"
                state.save(task, period, status, message=f"{len(expected[day])} 个应采标的已对账")
                continue
        complete = False
    return complete


def process_market(state: State, task: str, day: date, *, catch_up: bool = False) -> bool:
    try:
        with state.lock(task):
            if task == FINANCE:
                from scheduler.finance_workflow import process_finance

                return process_finance(state, day)
            okay = _daily(state, task, _dates(state, task, day, catch_up))
            state.service(task, "ok" if okay else "incomplete")
            return okay
    except Timeout:
        logger.info("Market step %s already running", task)
        return True
    except Exception as exc:
        logger.exception("Market step %s failed", task)
        state.service(task, "error", type(exc).__name__)
        if task != FINANCE:
            state.save(task, day.isoformat(), "source_error", message=type(exc).__name__)
        return False
    finally:
        state.export()
