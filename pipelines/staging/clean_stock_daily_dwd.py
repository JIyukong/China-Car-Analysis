"""按交易日将验收通过的股票日线 ODS 发布到 DWD。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from hashlib import md5

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from base_infra.db import engine

_DAILY_MARKETS = "market IN ('A', 'HK')"
_STOCK_FIELDS = ("stock_id", "stock_code", "stock_code_raw", "stock_name", "market")
_PRICE_FIELDS = ("open", "high", "low", "close")
_PRICE_LIMIT = Decimal("99999999.9999")
_PCT_LIMIT = Decimal("9999.9999")
_COLUMNS = (
    "trade_date", *_STOCK_FIELDS, "exchange", "currency", *_PRICE_FIELDS, "volume", "amount", "pct_change",
    "is_valid_ohlc", "row_hash",
)
_MARKET_CALENDARS = {"A": ("dim_trade_calendar", "SSE"), "HK": ("etl_market_calendar", "HKEX")}
_SOURCE_SQL = text("""
    SELECT ods.trade_date, ods.stock_id AS ods_stock_id, ods.stock_code_raw, ods.market_raw, ods.exchange_raw,
           ds.id AS stock_id, ds.stock_code, ds.stock_name, ds.market, ds.exchange, ds.currency,
           ods.`open`, ods.high, ods.low, ods.`close`, ods.volume, ods.amount, ods.pct_change
    FROM ods_stock_daily ods
    LEFT JOIN dim_stock ds ON ds.stock_code = ods.stock_code_raw
    WHERE ods.trade_date = :period AND ods.source = 'akshare' AND ods.market_raw IN ('A', 'HK')
    ORDER BY ods.stock_code_raw
    FOR UPDATE
""")


def _finite(value: object) -> bool:
    return type(value) is int or isinstance(value, Decimal) and value.is_finite()


def _exact_keys(rows: list, key: Callable[[Mapping], object], expected: set, label: str) -> None:
    keys = [key(row) for row in rows]
    observed = set(keys)
    if len(observed) != len(keys) or observed != expected:
        raise ValueError(
            f"{label}业务键未对账：期望 {len(expected)}，实际 {len(observed)}，"
            f"缺失 {len(expected - observed)}，额外 {len(observed - expected)}，重复 {len(keys) - len(observed)}"
        )


def _stock_mapping_valid(row: Mapping) -> bool:
    raw_exchange = row.get("exchange_raw")
    return (
        row["stock_id"] is not None
        and row["stock_code_raw"] == row["stock_code"]
        and row["market_raw"] == row["market"]
        and row["ods_stock_id"] in (None, row["stock_id"])
        and (not raw_exchange or not row.get("exchange") or raw_exchange == row["exchange"])
    )


def _natural_hash(*fields: object) -> str:
    return md5("|".join(map(str, fields)).encode(), usedforsecurity=False).hexdigest()


def _publish_rows(
    conn: Connection,
    table: str,
    period: date,
    market_scope: str,
    columns: tuple[str, ...],
    natural_key: tuple[str, ...],
    payload: list[dict],
) -> int:
    # 固定标识符由日线和财务发布器提供，所有行值使用绑定参数。
    def key(row: Mapping) -> tuple:
        return tuple(row[name] for name in natural_key)

    day_column = natural_key[1]
    column_list = ", ".join(f"`{column}`" for column in columns)
    select_period = text(f"""
        SELECT {column_list} FROM {table}
        WHERE {day_column} = :period AND {market_scope}
        ORDER BY {', '.join(natural_key)} FOR UPDATE
    """)
    params = {"period": period}
    existing_rows = conn.execute(select_period, params).mappings().all()
    existing = {key(row) for row in existing_rows}
    expected = {key(row): row for row in payload}
    if existing - expected.keys() or len(existing) != len(existing_rows):
        raise ValueError(f"{table} 既有业务键与本期验收范围冲突，须先核对")

    inserts = [row for row in payload if key(row) not in existing]
    updates = [row for row in payload if key(row) in existing]
    if inserts:
        bindings = ", ".join(f":{column}" for column in columns)
        conn.execute(text(f"INSERT INTO {table} ({column_list}) VALUES ({bindings})"), inserts)
    if updates:
        assignments = ", ".join(f"`{column}` = :{column}" for column in columns if column not in natural_key)
        key_filter = " AND ".join(f"{column} = :{column}" for column in natural_key)
        conn.execute(text(f"UPDATE {table} SET {assignments} WHERE {key_filter}"), updates)

    actual_rows = conn.execute(select_period, params).mappings().all()
    actual = {key(row): {column: row[column] for column in columns} for row in actual_rows}
    if actual != expected or len(actual_rows) != len(expected):
        raise RuntimeError(f"{table} 发布后复查失败，交易回滚")
    return len(payload)


def _valid_prices(row: Mapping) -> bool:
    return all(_finite(row[key]) and 0 < row[key] <= _PRICE_LIMIT for key in _PRICE_FIELDS) and (
        row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]
    )


def _calendar_row(conn: Connection, day: date, market: str) -> Mapping:
    table, market_code = _MARKET_CALENDARS[market]
    rows = conn.execute(text(f"""
        SELECT bounds.is_open, bounds.previous,
               (SELECT COUNT(*) FROM {table} dates
                WHERE dates.market = :market AND dates.trade_date BETWEEN bounds.previous AND :day)
                = DATEDIFF(:day, bounds.previous) + 1 AS complete
        FROM (
            SELECT today.is_open,
                   (SELECT MAX(prior.trade_date) FROM {table} prior
                    WHERE prior.market = :market AND prior.trade_date < :day AND prior.is_open = 1) AS previous
            FROM {table} today
            WHERE today.market = :market AND today.trade_date = :day
        ) bounds
    """), {"market": market_code, "day": day}).mappings().all()
    if len(rows) != 1 or rows[0]["is_open"] != 1:
        raise ValueError(f"{market} {day} 在交易日历中未确认为开市")
    return rows[0]


def _previous_closes(conn: Connection, day: date, market: str, codes: list[str]) -> dict[str, Decimal]:
    calendar = _calendar_row(conn, day, market)
    if not codes or not calendar["complete"] or calendar["previous"] is None:
        return {}
    # DWD 已发布有效收盘价优先，其次用同一日期同一标的的有效 ODS 行。
    query = text("""
        SELECT dwd.stock_code, dwd.`open`, dwd.high, dwd.low, dwd.`close`, 0 AS priority
        FROM dwd_stock_daily dwd JOIN dim_stock ds ON ds.id = dwd.stock_id AND ds.stock_code = dwd.stock_code
        WHERE dwd.trade_date = :previous AND dwd.market = :market AND ds.market = :market
          AND dwd.stock_code IN :codes AND dwd.is_valid_ohlc = 1
        UNION ALL
        SELECT ods.stock_code_raw AS stock_code, ods.`open`, ods.high, ods.low, ods.`close`, 1 AS priority
        FROM ods_stock_daily ods JOIN dim_stock ds ON ds.stock_code = ods.stock_code_raw
        WHERE ods.trade_date = :previous AND ods.market_raw = :market AND ds.market = :market
          AND ods.stock_code_raw IN :codes AND ods.source = 'akshare'
          AND (ods.stock_id IS NULL OR ods.stock_id = ds.id)
        ORDER BY priority
    """).bindparams(bindparam("codes", expanding=True))
    rows = conn.execute(query, {"previous": calendar["previous"], "market": market, "codes": codes}).mappings().all()
    previous = {}
    for row in rows:
        if row["stock_code"] in codes and _valid_prices(row):
            previous.setdefault(row["stock_code"], row["close"])
    return previous


def _validate_daily_row(day: date, row: Mapping) -> None:
    code = row["stock_code_raw"]
    if row["trade_date"] != day or not _stock_mapping_valid(row) or row["market"] not in _MARKET_CALENDARS:
        raise ValueError(f"日线业务键未匹配交易日或股票维度：{code}")
    if not _valid_prices(row):
        raise ValueError(f"日线开高低收字段无效：{code}")
    for field, upper_bound in (
        ("volume", 2**64 - 1), ("amount", Decimal("9999999999999999.99")), ("pct_change", _PCT_LIMIT),
    ):
        value = row[field]
        lower_bound = -upper_bound if field == "pct_change" else 0
        if value is not None and (not _finite(value) or not lower_bound <= value <= upper_bound):
            raise ValueError(f"日线 {field} 字段无效：{code}")


def publish_stock_day(day: date, expected_codes: set[str]) -> int:
    """校验并在同一事务内发布、复查指定交易日的全部规范日线。"""
    if type(day) is not date or not expected_codes:
        raise ValueError("日线发布需要交易日和非空股票清单")
    with engine.begin() as conn:
        rows = conn.execute(_SOURCE_SQL, {"period": day}).mappings().all()
        _exact_keys(rows, lambda row: row["stock_code_raw"], expected_codes, "日线 ODS")
        for row in rows:
            _validate_daily_row(day, row)

        previous = {}
        for market in sorted({row["market"] for row in rows}):
            missing_returns = [row["stock_code"] for row in rows if row["market"] == market and row["pct_change"] is None]
            previous.update(_previous_closes(conn, day, market, missing_returns))

        payload = []
        for row in rows:
            values = {column: row[column] for column in _COLUMNS if column not in ("is_valid_ohlc", "row_hash")}
            prior_close = previous.get(row["stock_code"])
            if values["pct_change"] is None and prior_close is not None:
                change = ((Decimal(row["close"]) / Decimal(prior_close) - 1) * 100).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP,
                )
                if abs(change) <= _PCT_LIMIT:
                    values["pct_change"] = change
            values.update(is_valid_ohlc=1, row_hash=_natural_hash(row["stock_code_raw"], day, "akshare"))
            payload.append(values)
        return _publish_rows(conn, "dwd_stock_daily", day, _DAILY_MARKETS, _COLUMNS, ("stock_id", "trade_date"), payload)


def main(argv: list[str] | None = None) -> int:
    from scheduler.runner import main as run_scheduler

    parser = argparse.ArgumentParser(description="通过统一调度入口运行指定交易日的股票日线发布")
    parser.add_argument("--date", required=True, type=date.fromisoformat, metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)
    return run_scheduler(["--chain", "daily", "--step", "load_stock_daily", "--period", args.date.isoformat()])


if __name__ == "__main__":
    raise SystemExit(main())
