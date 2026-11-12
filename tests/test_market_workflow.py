from datetime import date
from types import SimpleNamespace

import pytest


def test_expected_market_sessions_distinguish_holiday_suspension_and_unknown():
    from scheduler.market_workflow import sessions

    day = date(2026, 11, 11)
    instruments = [
        ("A1", "SSE", None), ("A2", "SSE", None), ("H1", "HKEX", None),
        ("L1", "GFEX", None), ("FUTURE", "SSE", date(2026, 11, 12)),
    ]
    open_codes, unknown = sessions(day, instruments, {("SSE", day): True, ("HKEX", day): False}, {("A2", day)})
    assert open_codes == {"A1"}
    assert unknown == {"GFEX"}


def test_calendar_sources_with_different_collations_are_read_independently(monkeypatch):
    from contextlib import nullcontext

    from base_infra import db
    from scheduler.market_workflow import STOCK, expected_sessions

    day = date(2026, 11, 11)

    def execute(statement, params=None):
        sql = str(statement)
        if "UNION" in sql.upper():
            raise RuntimeError("MySQL 1271: incompatible market collations")
        if "FROM dim_stock" in sql:
            return [("A1", "SSE", None, None), ("A2", "SSE", None, None), ("H1", "HKEX", None, None)]
        assert params == {"first": day, "last": day}
        if "FROM dim_trade_calendar" in sql:
            return [("SSE", day, 1)]
        if "FROM etl_market_calendar" in sql:
            return [("HKEX", day, 0)]
        if "FROM etl_stock_suspension" in sql:
            return [("A2", day)]
        pytest.fail(f"Unexpected query: {sql}")

    monkeypatch.setattr(db, "engine", SimpleNamespace(connect=lambda: nullcontext(SimpleNamespace(execute=execute))))
    assert expected_sessions(STOCK, [day]) == ({day: {"A1"}}, {day: set()})


def test_daily_loader_repair_bounds_do_not_skip_an_old_hole():
    from pipelines.ingest.common.market_quality import market_start

    assert market_start(date(2026, 11, 10), "20170101", "20261105") == "20261105"
    assert market_start(date(2026, 11, 4), "20170101", "20261105") == "20261104"
    assert market_start(None, "20170101", "20261105") == "20261105"


def test_duplicate_or_invalid_price_rows_fail_before_ods_write():
    from pipelines.ingest.common.market_quality import valid_price_rows

    day = date(2026, 11, 11)
    good = {"trade_date": day, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 20}
    assert valid_price_rows([good]) == [good]
    with pytest.raises(ValueError, match="duplicate"):
        valid_price_rows([good, good])
    with pytest.raises(ValueError, match="OHLC"):
        valid_price_rows([{**good, "close": 20}])


def test_observed_instrument_with_missing_ods_row_does_not_publish(tmp_path, monkeypatch):
    from scheduler import market_workflow as market
    from scheduler.workflow import State

    day = date(2026, 11, 11)
    monkeypatch.setattr(market, "expected_sessions", lambda *_: ({day: {"A1", "A2"}}, {day: set()}))
    observed = {"A1": {day}, "A2": {day}}
    monkeypatch.setattr(market, "ingest_daily", lambda *args: SimpleNamespace(observed=observed, failures={}))
    monkeypatch.setattr(market, "actual_sessions", lambda *_: {day: {"A1"}})
    monkeypatch.setattr(market, "publish_daily", lambda *_: pytest.fail("published an incomplete date"))
    store = State(tmp_path)
    assert not market.process_market(store, "load_stock_daily", day)
    assert store.batch("load_stock_daily", str(day))["status"] == "source_error"


def test_late_source_row_is_not_hidden_by_old_ods_copy(tmp_path, monkeypatch):
    from scheduler import market_workflow as market
    from scheduler.workflow import State

    day = date(2026, 11, 11)
    monkeypatch.setattr(market, "expected_sessions", lambda *_: ({day: {"A1", "A2"}}, {day: set()}))
    monkeypatch.setattr(market, "ingest_daily", lambda *args: SimpleNamespace(observed={"A1": {day}}, failures={}))
    monkeypatch.setattr(market, "actual_sessions", lambda *_: {day: {"A1", "A2"}})
    monkeypatch.setattr(market, "publish_daily", lambda *_: pytest.fail("published based on a stale ODS row"))
    store = State(tmp_path)

    assert not market.process_market(store, market.STOCK, day)
    assert store.batch(market.STOCK, str(day))["status"] == "waiting_source"


def test_historical_symbol_failure_does_not_block_complete_today(tmp_path, monkeypatch):
    from scheduler import market_workflow as market
    from scheduler.workflow import State

    old, today = date(2026, 11, 10), date(2026, 11, 11)
    expected = {old: {"OLD", "A1"}, today: {"A1"}}
    monkeypatch.setattr(market, "expected_sessions", lambda *_: (expected, {day: set() for day in expected}))
    report = SimpleNamespace(observed={"A1": {old, today}}, failures={"OLD": "network_timeout"})
    monkeypatch.setattr(market, "ingest_daily", lambda *args: report)
    monkeypatch.setattr(market, "actual_sessions", lambda *_: {old: {"A1"}, today: {"A1"}})
    published = []
    monkeypatch.setattr(market, "publish_daily", lambda _, day, codes: published.append((day, codes)) or True)
    store = State(tmp_path)

    assert not market._daily(store, market.STOCK, [old, today])
    assert store.batch(market.STOCK, str(old))["status"] == "source_error"
    assert store.batch(market.STOCK, str(today))["status"] == "published"
    assert published == [(today, {"A1"})]


def test_bounded_stock_lookup_can_load_a_delisted_requested_symbol(monkeypatch):
    from contextlib import nullcontext

    from pipelines.ingest.stock import load_stock_daily_akshare as loader

    active = (1, "A1", "Active", "A", "SSE")
    delisted = (2, "OLD", "Delisted", "HK", "HKEX")

    def execute(sql, params=None):
        if params is None:
            assert "is_listed = 1" in str(sql)
            return SimpleNamespace(fetchall=lambda: [active])
        assert "market IN ('A', 'HK')" in str(sql)
        assert "is_listed = 1" not in str(sql)
        assert "stock_code IN" in str(sql)
        assert set(params["codes"]) == {"OLD"}
        return SimpleNamespace(fetchall=lambda: [delisted])

    monkeypatch.setattr(loader, "engine", SimpleNamespace(connect=lambda: nullcontext(SimpleNamespace(execute=execute))))
    assert [item["stock_code"] for item in loader.get_all_stocks()] == ["A1"]
    assert [item["stock_code"] for item in loader.get_all_stocks({"OLD"})] == ["OLD"]


@pytest.mark.parametrize("write_error", [False, True])
def test_stock_fetch_report_tracks_only_committed_requested_dates(monkeypatch, write_error):
    from pandas import DataFrame

    from pipelines.ingest.stock import load_stock_daily_akshare as loader
    from pipelines.ingest.stock.network_runtime import DailyFetch
    from scheduler.jobs.common import build_scheduler_config

    old, today = date(2026, 11, 10), date(2026, 11, 11)
    info = {"stock_id": 1, "stock_code": "000001.SZ", "stock_name": "Active", "market": "A"}
    other = {**info, "stock_id": 2, "stock_code": "000002.SZ"}
    late = {**info, "stock_id": 3, "stock_code": "000003.SZ"}
    report = DailyFetch()
    config = build_scheduler_config()
    config.trade_dates = {"000001.SZ": frozenset({old, today}), "000002.SZ": frozenset({today}), "000003.SZ": frozenset({old})}
    config.daily_fetch = report
    config.stock_sleep_seconds = 0
    monkeypatch.setattr(loader, "get_all_stocks", lambda *_: [info, other, late])
    monkeypatch.setattr(loader, "get_last_trade_date", lambda *_: pytest.fail("bounded fetch used old ODS maximum"))
    monkeypatch.setattr(loader, "setup_proxy", lambda *_: None)
    monkeypatch.setattr(loader, "run_daily_preflight", lambda *_: {"ok": False, "status": "timeout", "message": "probe timed out"})
    frame = DataFrame([{"date": today, "open": 10, "high": 12, "low": 9, "close": 11}])
    monkeypatch.setattr(loader, "fetch_a_stock_daily_with_fallback", lambda *_: (frame, "akshare"))
    written = []
    def insert(rows):
        if write_error and rows[0]["stock_code_raw"] == "000001.SZ":
            raise RuntimeError("transaction failed")
        written.extend(rows)
        return len(rows)

    monkeypatch.setattr(loader, "insert_daily_data", insert)

    assert loader.run_daily_ingest(config) is not write_error
    if write_error:
        assert report.failures == {"000001.SZ": "RuntimeError"}
        assert report.observed == {"000002.SZ": {today}, "000003.SZ": set()}
        assert [(row["stock_code_raw"], row["trade_date"]) for row in written] == [("000002.SZ", today)]
    else:
        assert report.failures == {}
        assert report.observed == {"000001.SZ": {today}, "000002.SZ": {today}, "000003.SZ": set()}
        assert [(row["stock_code_raw"], row["trade_date"]) for row in written] == [("000001.SZ", today), ("000002.SZ", today)]


@pytest.mark.parametrize(
    ("module", "collector"),
    [
        ("stock.load_stock_daily_akshare", "run_daily_ingest"),
        ("futures.load_futures_main_sina", "run_futures_ingest"),
        ("index.load_ths_index_daily_akshare", "run_index_ingest"),
    ],
)
def test_direct_market_cli_requires_diagnostic_flag_for_ingest(module, collector, monkeypatch):
    import sys
    from importlib import import_module

    loader = import_module(f"pipelines.ingest.{module}")
    monkeypatch.setattr(sys, "argv", [module])
    monkeypatch.setattr(loader, collector, lambda *args, **kwargs: pytest.fail("unbounded CLI started ingest"))
    assert loader.main() is False
