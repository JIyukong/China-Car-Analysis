"""Validate typed provider rows before upserting market ODS."""

from datetime import date
from math import isfinite


def market_start(last_date: date | None, start: str, repair_from: str | None) -> str:
    if last_date is None:
        return repair_from or start
    latest = last_date.strftime("%Y%m%d")
    return min(latest, repair_from) if repair_from else latest


def valid_price_rows(rows: list[dict], allowed: frozenset[date] | None = None) -> list[dict]:
    result = []
    seen = set()
    for row in rows:
        day = row["trade_date"]
        if type(day) is not date:
            raise ValueError("Invalid trade date")
        if allowed is not None and day not in allowed:
            continue
        if day in seen:
            raise ValueError(f"duplicate source trade date: {day}")
        prices = [row.get(name) for name in ("open", "high", "low", "close")]
        if any(value is None or not isfinite(value) or value <= 0 for value in prices):
            raise ValueError(f"Missing or invalid OHLC for {day}")
        open_, high, low, close = prices
        if not (low <= min(open_, close) <= max(open_, close) <= high):
            raise ValueError(f"Inconsistent OHLC for {day}")
        for key in ("volume", "amount", "open_interest"):
            value = row.get(key)
            if value is not None and (not isfinite(value) or value < 0):
                raise ValueError(f"Invalid {key} for {day}")
        result.append(row)
        seen.add(day)
    return result


def validate_finance_rows(rows: list[dict], through: date | None = None) -> list[dict]:
    seen = set()
    accepted = []
    for row in rows:
        period, report_type = row["report_date"], row["report_type"]
        if type(period) is not date or report_type not in {"Q1", "Q2", "Q3", "Q4", "年报"}:
            raise ValueError("Invalid report period")
        if through is not None and period > through:
            continue
        if (period, report_type) in seen:
            raise ValueError(f"Duplicate finance report: {period} {report_type}")
        metrics = [row[key] for key in ("eps", "net_profit", "total_revenue")]
        if all(value is None for value in metrics) or any(
            value is not None and not isfinite(value) for value in metrics
        ):
            raise ValueError(f"Missing or invalid core finance metrics: {period} {report_type}")
        accepted.append(row)
        seen.add((period, report_type))
    return accepted
