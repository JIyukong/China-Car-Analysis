from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipelines.staging import clean_stock_daily_dwd as daily
from pipelines.staging import clean_stock_finance_dwd as finance

DAY = date(2026, 7, 6)
PREVIOUS = date(2026, 7, 3)
PERIOD = date(2026, 6, 30)
CODE = '002594.SZ'


def _stock(**overrides):
    return dict(
        stock_id=2, ods_stock_id=2, stock_code=CODE, stock_code_raw=CODE, stock_name='比亚迪',
        market='A', market_raw='A', exchange='SZ', currency='CNY', **overrides,
    )


def _daily(**overrides):
    return _stock() | {
        'trade_date': DAY, 'open': Decimal('9'), 'high': Decimal('11'), 'low': Decimal('8'),
        'close': Decimal('10'), 'volume': 1, 'amount': Decimal('10'), 'pct_change': Decimal('2.5'),
    } | overrides


def _finance(**overrides):
    return _stock() | {
        'report_date': PERIOD, 'report_type': 'Q2', 'eps': Decimal('0.1'), 'net_profit': None,
        'total_revenue': None, 'main_revenue': None, 'total_profit': None, 'total_assets': None,
        'total_liabilities': None, 'equity': None, 'roe': Decimal('0.50'), 'gross_margin': None, 'debt_ratio': None,
    } | overrides


class MockTransaction:
    def __init__(self, source, existing=(), *, mismatch=False, calendar=None, previous=()):
        self.source = source
        self.existing = list(existing)
        self.mismatch = mismatch
        self.calendar = calendar if calendar is not None else [{'previous': PREVIOUS, 'complete': 1, 'is_open': 1}]
        self.previous = previous
        self.calls = []
        self.written = []
        self.committed = False
        self.rolled_back = False
        self.read_dwd = False

    @contextmanager
    def begin(self):
        try:
            yield self
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    def execute(self, sql, params):
        sql = str(sql).strip()
        self.calls.append((sql, params))
        result = MagicMock()
        if sql.startswith(('UPDATE ', 'INSERT ')):
            self.written.extend(params)
        elif 'FROM dim_trade_calendar' in sql or 'FROM etl_market_calendar' in sql:
            result.mappings.return_value.all.return_value = self.calendar
        elif params.get('previous') is not None:
            result.mappings.return_value.all.return_value = self.previous
        elif 'FROM ods_stock_' in sql:
            result.mappings.return_value.all.return_value = self.source
        elif 'FROM dwd_stock_' in sql:
            rows = self.written if self.read_dwd and not self.mismatch else self.existing
            result.mappings.return_value.all.return_value = rows
            self.read_dwd = True
        else:
            raise AssertionError(sql)
        return result


@pytest.mark.parametrize('rows', [[], [_daily(), _daily()], [_daily(close=Decimal('20'))], [_daily(ods_stock_id=3)]])
def test_daily_blocks_missing_duplicate_invalid_and_mismapped_rows(monkeypatch, rows):
    transaction = MockTransaction(rows)
    monkeypatch.setattr(daily, 'engine', transaction)
    with pytest.raises(ValueError):
        daily.publish_stock_day(DAY, {CODE})
    assert transaction.rolled_back
    assert not transaction.written


def test_daily_updates_same_natural_key_and_reconciles_before_commit(monkeypatch):
    transaction = MockTransaction([_daily()], existing=[_daily(close=Decimal('9'))])
    monkeypatch.setattr(daily, 'engine', transaction)
    assert daily.publish_stock_day(DAY, {CODE}) == 1
    assert transaction.committed
    assert transaction.written[0]['close'] == Decimal('10')
    update = next(sql for sql, _ in transaction.calls if sql.startswith('UPDATE'))
    assert 'stock_id = :stock_id' in update and 'trade_date = :trade_date' in update


def test_daily_keeps_return_empty_without_the_previous_open_session_close(monkeypatch):
    transaction = MockTransaction([_daily(pct_change=None)])
    monkeypatch.setattr(daily, 'engine', transaction)
    assert daily.publish_stock_day(DAY, {CODE}) == 1
    assert transaction.written[0]['pct_change'] is None
    assert any(params.get('previous') == PREVIOUS for _, params in transaction.calls)


def test_daily_fills_return_from_exact_previous_open_session(monkeypatch):
    transaction = MockTransaction([_daily(pct_change=None)], previous=[_daily(close=Decimal('8'))])
    monkeypatch.setattr(daily, 'engine', transaction)
    assert daily.publish_stock_day(DAY, {CODE}) == 1
    assert transaction.written[0]['pct_change'] == Decimal('25.0000')


def test_daily_blocks_closed_market_and_leaves_return_empty_for_incomplete_calendar(monkeypatch):
    closed = MockTransaction([_daily()], calendar=[{'is_open': 0, 'previous': PREVIOUS, 'complete': 1}])
    monkeypatch.setattr(daily, 'engine', closed)
    with pytest.raises(ValueError, match='未确认为开市'):
        daily.publish_stock_day(DAY, {CODE})
    assert not closed.written

    incomplete = MockTransaction(
        [_daily(pct_change=None)], calendar=[{'is_open': 1, 'previous': PREVIOUS, 'complete': 0}],
    )
    monkeypatch.setattr(daily, 'engine', incomplete)
    assert daily.publish_stock_day(DAY, {CODE}) == 1
    assert incomplete.written[0]['pct_change'] is None
    assert not any('previous' in params for _, params in incomplete.calls)


def test_finance_preserves_percentage_units_and_rejects_incomplete_postwrite(monkeypatch):
    transaction = MockTransaction([_finance()], mismatch=True)
    monkeypatch.setattr(finance, 'engine', transaction)
    with pytest.raises(RuntimeError, match='复查'):
        finance.publish_finance_period(PERIOD, {(CODE, 'Q2')})
    assert transaction.written[0]['roe'] == Decimal('0.50')
    assert transaction.rolled_back
    assert not transaction.committed


@pytest.mark.parametrize('row', [_finance(report_type='Q1'), _finance(eps=None), _finance(roe=Decimal('NaN'))])
def test_finance_rejects_bad_report_type_and_missing_or_invalid_critical_values(monkeypatch, row):
    transaction = MockTransaction([row])
    monkeypatch.setattr(finance, 'engine', transaction)
    with pytest.raises(ValueError):
        finance.publish_finance_period(PERIOD, {(CODE, 'Q2')})
    assert not transaction.written
