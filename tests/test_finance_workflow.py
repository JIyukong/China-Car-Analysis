from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, event, text

from pipelines.ingest.stock import load_stock_finance_akshare as loader
from scheduler.workflow import State


STOCK = dict(stock_id=1, stock_code='000001.SZ', stock_name='Example', market='A', exchange='SZ')


def report(period='2026-06-30', **values):
    return loader.transform_finance_data(pd.DataFrame([{
        'REPORT_DATE': period, 'BASIC_EPS': '1.2500', 'NETPROFIT': '100.00',
        'TOTAL_OPERATE_INCOME': '120.00', **values,
    }]), STOCK)[0]


def test_malformed_provider_row_is_rejected_before_reconciliation():
    with pytest.raises(ValueError, match='日期'):
        report('bad date')


def test_core_values_keep_decimal_precision_for_revisions():
    row = report(NETPROFIT='9999999999999999.31')
    assert row['net_profit'] == Decimal('9999999999999999.31')


def test_one_valid_new_listing_report_needs_no_multiyear_history(monkeypatch):
    monkeypatch.setattr(loader, 'fetch_a_stock_finance', lambda code: pd.DataFrame([
        {'REPORT_DATE': '2026-06-30', 'NETPROFIT': 100},
        {'REPORT_DATE': '2026-09-30', 'NETPROFIT': 120},
    ]))
    config = loader.build_runtime_config(SimpleNamespace())
    config.report_through = date(2026, 7, 5)
    result = loader.fetch_single_stock(STOCK, config=config, dry_run=True)
    assert result['status'] == 'success'
    assert [row['report_date'] for row in result['rows']] == [date(2026, 6, 30)]


def test_published_revision_retries_and_keeps_last_good_digest(tmp_path, monkeypatch):
    from scheduler import finance_workflow as finance

    state = State(tmp_path)
    period = date(2026, 6, 30)
    day = date(2026, 9, 1)
    returned = {period: [report()]}
    calls = []

    def collect(config, *, tracked_periods, on_verified):
        assert config.report_through == day
        on_verified(returned)
        return returned

    def publish(month, keys):
        calls.append((month, keys))
        if len(calls) == 2:
            raise RuntimeError('simulated DWD failure')

    monkeypatch.setattr(finance, 'collect_finance_reports', collect)
    monkeypatch.setattr(finance, '_dwd_enabled', lambda: True)
    monkeypatch.setattr(finance, '_publish_finance_period', publish)
    assert finance.process_finance(state, day)
    first = state.batch('load_stock_finance', period.isoformat())
    returned[period] = [report(NETPROFIT='112.50')]
    assert not finance.process_finance(state, day)
    failed = state.batch('load_stock_finance', period.isoformat())
    assert failed['status'] == 'publish_error'
    assert failed['published_digest'] == first['published_digest'] != failed['digest']
    assert finance.process_finance(state, day)
    assert state.batch('load_stock_finance', period.isoformat())['published_digest'] == failed['digest']
    assert calls == [(period, {('000001.SZ', 'Q2')})] * 3


def test_disabled_dwd_keeps_verified_period_for_later_publication(tmp_path, monkeypatch):
    from scheduler import finance_workflow as finance

    state = State(tmp_path)
    period = date(2026, 6, 30)
    report_rows = {period: [report()]}

    def collect(config, *, tracked_periods, on_verified):
        on_verified(report_rows)
        return report_rows

    monkeypatch.setattr(finance, 'collect_finance_reports', collect)
    monkeypatch.setattr(finance, '_dwd_enabled', lambda: False)
    monkeypatch.setattr(finance, '_publish_finance_period', lambda *args: pytest.fail('DWD must stay disabled'))
    assert finance.process_finance(state, date(2026, 9, 1))
    row = state.batch('load_stock_finance', period.isoformat())
    assert row['status'] == 'ods_accepted'
    assert row['digest'] == row['published_digest']
    published = []
    monkeypatch.setattr(finance, '_dwd_enabled', lambda: True)
    monkeypatch.setattr(finance, '_publish_finance_period', lambda *args: published.append(args))
    assert finance.process_finance(state, date(2026, 9, 2))
    assert published == [(period, {('000001.SZ', 'Q2')})]


@pytest.fixture
def ods(monkeypatch):
    engine = create_engine('sqlite://')

    @event.listens_for(engine, 'before_cursor_execute', retval=True)
    def emulate_mysql_row_lock(conn, cursor, statement, params, context, executemany):
        return statement.removesuffix(' FOR UPDATE'), params

    columns = ', '.join(f'{field} {"INTEGER" if field == "stock_id" else "TEXT"}' for field in loader.CONTENT_FIELDS)

    def query(sql):
        with engine.begin() as conn:
            result = conn.execute(text(sql))
            return result.all() if result.returns_rows else None

    query(f'''CREATE TABLE ods_stock_finance (id INTEGER PRIMARY KEY, {columns}, fetch_time TEXT,
        UNIQUE(stock_code_raw, report_date, report_type))''')
    monkeypatch.setattr(loader, 'engine', engine)
    yield query
    engine.dispose()


def test_bootstrap_skips_old_new_ods_rows_but_tracks_real_revisions(ods):
    day = date(2026, 9, 1)
    recent = report()
    old = report('2022-12-31')
    for row in (recent, old):
        row['fetch_time'] = 'first fetch'
    assert set(loader.reconcile_finance_rows([recent, old], day)) == {date(2026, 6, 30)}
    unchanged = dict(recent, fetch_time='second fetch')
    corrected = dict(old, net_profit=Decimal('112.75'), fetch_time='second fetch')
    assert set(loader.reconcile_finance_rows([unchanged, corrected], day)) == {date(2022, 12, 31), date(2026, 6, 30)}
    assert ods('SELECT id,net_profit,fetch_time FROM ods_stock_finance ORDER BY id') == [
        (1, '100.00', 'first fetch'), (2, '112.75', 'second fetch'),
    ]


def test_missing_known_report_rejects_all_writes(ods):
    older, latest = report('2026-03-31'), report('2026-06-30')
    older['fetch_time'] = latest['fetch_time'] = 'first fetch'
    day = date(2026, 9, 1)
    loader.reconcile_finance_rows([older, latest], day)
    with pytest.raises(loader.FinanceSourceError, match='来源缺少'):
        loader.reconcile_finance_rows([dict(older, net_profit=Decimal('112.75'))], day)
    assert ods('SELECT net_profit,fetch_time FROM ods_stock_finance ORDER BY id') == [
        ('100.00', 'first fetch'), ('100.00', 'first fetch'),
    ]


@pytest.mark.parametrize(('older', 'track'), [('2026-06-30', False), ('2022-12-31', True)])
def test_omitting_first_report_cannot_shorten_protected_coverage(ods, older, track):
    day = date(2026, 10, 30)
    previous, latest = report(older), report('2026-09-30')
    previous['fetch_time'] = latest['fetch_time'] = 'first fetch'
    loader.reconcile_finance_rows([previous, latest], day)
    tracked = {date.fromisoformat(older)} if track else set()
    with pytest.raises(loader.FinanceSourceError, match='来源缺少'):
        loader.reconcile_finance_rows([dict(latest, net_profit=Decimal('112.75'))], day, tracked_periods=tracked)
    assert ods('SELECT net_profit,fetch_time FROM ods_stock_finance ORDER BY report_date') == [
        ('100.00', 'first fetch'), ('100.00', 'first fetch'),
    ]


def test_old_untracked_report_can_roll_off_and_future_ods_is_outside_cutoff(ods):
    old, current, future = [report(period) for period in ('2022-12-31', '2026-09-30', '2026-12-31')]
    for row in (old, current, future):
        row['fetch_time'] = 'first fetch'
    loader.reconcile_finance_rows([old, current, future], date(2027, 1, 31))
    tracked = {date(2026, 12, 31)}
    assert set(loader.reconcile_finance_rows([current], date(2026, 10, 30), tracked_periods=tracked)) == {
        date(2026, 9, 30),
    }
    assert ods('SELECT report_date FROM ods_stock_finance ORDER BY report_date') == [
        ('2022-12-31',), ('2026-09-30',), ('2026-12-31',),
    ]


def test_finance_coverage_includes_only_active_a_shares_listed_by_cutoff(ods):
    ods('''CREATE TABLE dim_stock (
        id INTEGER PRIMARY KEY, stock_code TEXT, stock_name TEXT, market TEXT,
        exchange TEXT, is_listed INTEGER, listed_date TEXT
    )''')
    ods('''INSERT INTO dim_stock VALUES
        (1, '000001.SZ', 'Current', 'A', 'SZ', 1, '2026-06-01'),
        (2, '000002.SZ', 'Future', 'A', 'SZ', 1, '2026-11-01'),
        (3, '000003.SZ', 'Delisted', 'A', 'SZ', 0, '2020-01-01'),
        (4, '0004.HK', 'Hong Kong', 'HK', 'HKEX', 1, '2020-01-01')
    ''')
    assert [stock['stock_code'] for stock in loader.get_all_stocks(date(2026, 10, 30))] == ['000001.SZ']


def test_disappeared_tracked_periods_flag_source_error_and_preserve_publication(ods, tmp_path, monkeypatch):
    from scheduler import finance_workflow as finance

    state = State(tmp_path)
    chain = 'load_stock_finance'
    historical = ('2022-12-31', '2023-03-31')
    for period in historical:
        state.save(chain, period, 'published', digest='previous-' + period)
    recent = dict(report('2026-09-30'), fetch_time='current fetch')
    monkeypatch.setattr(loader, '_collect_rows', lambda config: [recent])
    monkeypatch.setattr(finance, '_dwd_enabled', lambda: False)

    assert not finance.process_finance(state, date(2026, 10, 30))
    for period in historical:
        row = state.batch(chain, period)
        assert row['status'] == 'source_error'
        assert row['published_digest'] == 'previous-' + period
    with state.transaction() as conn:
        assert conn.execute('SELECT status FROM service_status WHERE task=?', (chain,)).fetchone()['status'] == 'source_error'
