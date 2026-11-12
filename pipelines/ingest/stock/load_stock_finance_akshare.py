"""Validate Eastmoney A-share income statements and reconcile finance ODS."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import akshare as ak
import pandas as pd
from sqlalchemy import bindparam, text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.ingest.common.ingest_utils import build_common_parser, make_stats, reset_stats
from pipelines.ingest.common.market_quality import validate_finance_rows
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
STATS = make_stats("total_stocks")
_TABLE = 'ods_stock_finance'
_KEYS = ('stock_code_raw', 'report_date', 'report_type')
_PERIOD_END = {(3, 31): 'Q1', (6, 30): 'Q2', (9, 30): 'Q3', (12, 31): '年报'}
_METRICS = {
    'eps': ('基本每股收益', 'BASIC_EPS', 12, 4),
    'net_profit': ('净利润', 'NETPROFIT', 18, 2),
    'total_revenue': ('营业总收入', 'TOTAL_OPERATE_INCOME', 18, 2),
    'main_revenue': ('主营业务收入', 'OPERATE_INCOME', 18, 2),
    'total_profit': ('利润总额', 'TOTAL_PROFIT', 18, 2),
    'total_assets': ('资产总计', 'TOTAL_ASSETS', 18, 2),
    'total_liabilities': ('负债合计', 'TOTAL_LIABILITIES', 18, 2),
    'equity': ('所有者权益合计', 'TOTAL_EQUITY', 18, 2),
    'roe': ('净资产收益率', 'ROE', 8, 4),
    'gross_margin': ('销售毛利率', 'GROSS_PROFIT_MARGIN', 8, 4),
    'debt_ratio': ('资产负债率', 'DEBT_ASSET_RATIO', 8, 4),
}
CONTENT_FIELDS = (
    *_KEYS, 'stock_id', 'stock_name_raw', 'market_raw', 'exchange_raw', 'source_symbol',
    *_METRICS, 'source',
)


class FinanceSourceError(RuntimeError):
    """A probe cannot establish trustworthy finance ODS coverage."""


def parse_args():
    parser = build_common_parser('股票财务数据采集')
    parser.add_argument('--stock', help='指定单个 A 股股票代码测试（如 002594.SZ）')
    return parser.parse_args()


def get_all_stocks(through: date | None = None):
    sql = text('''
        SELECT id AS stock_id, stock_code, stock_name, market, exchange
          FROM dim_stock
         WHERE is_listed = 1 AND market = 'A'
           AND (listed_date IS NULL OR listed_date <= :through)
         ORDER BY stock_code
    ''')
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(sql, {'through': (through or date.today()).isoformat()}).mappings()]


def convert_a_stock_code(stock_code):
    if not re.fullmatch(r'[0-9]{6}\.(SZ|SH|BJ)', stock_code):
        raise ValueError(f'无效 A 股代码：{stock_code}')
    code, exchange = stock_code.split('.')
    return f'{exchange.lower()}{code}'


def get_report_type(report_date):
    try:
        return _PERIOD_END[report_date.month, report_date.day]
    except KeyError as exc:
        raise ValueError(f'财报日期必须为季度末：{report_date}') from exc


def fetch_a_stock_finance(stock_code):
    return ak.stock_profit_sheet_by_report_em(symbol=convert_a_stock_code(stock_code))


def _first_value(row, aliases):
    for alias in aliases:
        value = row.get(alias)
        if pd.notna(value) and str(value).strip() not in ('', '-', '--'):
            return value
    return None


def _decimal(value, field):
    if value is None:
        return None
    _, _, precision, scale = _METRICS[field]
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        rounded = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        if abs(rounded) >= Decimal(10) ** (precision - scale):
            raise InvalidOperation
    except InvalidOperation as exc:
        raise ValueError(f'无效财务指标：{field}') from exc
    return rounded.copy_abs() if rounded.is_zero() else rounded


def transform_finance_data(df, stock_info, through: date | None = None):
    if df is None or df.empty:
        return []
    stock_code = stock_info['stock_code']
    identity = {
        'stock_id': stock_info['stock_id'], 'stock_code_raw': stock_code,
        'stock_name_raw': stock_info['stock_name'], 'market_raw': stock_info['market'],
        'exchange_raw': stock_info['exchange'], 'source_symbol': convert_a_stock_code(stock_code),
        'source': 'akshare', 'fetch_time': datetime.now(),
    }
    rows = []
    for _, raw in df.iterrows():
        value = _first_value(raw, ('日期', '报告日期', 'REPORT_DATE'))
        try:
            if value is None or isinstance(value, (int, float)):
                raise ValueError
            report_date = pd.to_datetime(value).date()
            if type(report_date) is not date or report_date.year < 1900:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f'{stock_code} 财报日期无效：{value!r}') from exc
        if through is not None and report_date > through:
            continue
        rows.append({
            **identity, 'report_date': report_date, 'report_type': get_report_type(report_date),
            **{field: _decimal(_first_value(raw, spec[:2]), field) for field, spec in _METRICS.items()},
        })
    return rows


def fetch_single_stock(stock_info, config: RuntimeConfig, dry_run=False):
    stock_code = stock_info['stock_code']
    result = {'stock_code': stock_code, 'stock_name': stock_info['stock_name'],
              'market': stock_info['market'], 'rows_fetched': 0, 'rows_inserted': 0, 'rows': []}
    if stock_info['market'] != 'A':
        return {**result, 'status': 'skipped', 'error_type': 'unsupported_market', 'error': '仅采集 A 股财报'}
    for attempt in range(config.max_retries):
        try:
            with patched_requests_runtime(config):
                df = fetch_a_stock_finance(stock_code)
            break
        except Exception as exc:
            if attempt == config.max_retries - 1:
                return {**result, 'status': 'failed', 'error_type': classify_fetch_error(exc), 'error': str(exc)}
            sleep_before_retry(config, attempt)
    try:
        rows = validate_finance_rows(transform_finance_data(df, stock_info, config.report_through), config.report_through)
        if not rows:
            raise ValueError('没有可验证的财报记录')
        inserted = 0 if dry_run else insert_finance_data(rows)
        return {**result, 'status': 'success', 'rows': rows, 'rows_fetched': len(rows), 'rows_inserted': inserted}
    except (TypeError, ValueError) as exc:
        return {**result, 'status': 'failed', 'error_type': 'data_incomplete', 'error': str(exc)}


def _collect_rows(config: RuntimeConfig, stock_code=None):
    reset_stats(STATS, 'total_stocks')
    setup_proxy(config)
    STATS['preflight'] = run_preflight_check(
        config, preflight_url=FINANCE_PREFLIGHT_URL,
        response_validator=lambda payload: bool(payload.get('data')),
    )
    if not STATS['preflight']['ok']:
        raise FinanceSourceError(f"财报网络预检失败：{STATS['preflight']['status']}")
    if config.preflight_only:
        return []

    stocks = [stock for stock in get_all_stocks(config.report_through) if stock_code in (None, stock['stock_code'])]
    expected = {stock['stock_code'] for stock in stocks}
    STATS['total_stocks'] = len(stocks)
    if not expected or len(expected) != len(stocks):
        raise FinanceSourceError('A 股采集范围为空或存在重复代码')
    rows = []
    for index, stock in enumerate(stocks):
        result = fetch_single_stock(stock, config=config, dry_run=True)
        if result['status'] == 'success':
            STATS['success_count'] += 1
            rows.extend(result['rows'])
        else:
            STATS['fail_count'] += 1
            STATS['fail_details'].append(result)
        if index + 1 < len(stocks):
            time.sleep(config.stock_sleep_seconds)
    observed = {row['stock_code_raw'] for row in rows}
    if observed != expected or STATS['fail_count']:
        detail = ', '.join(f"{item['stock_code']}: {item['error']}" for item in STATS['fail_details'][:5])
        raise FinanceSourceError(f'财报 A 股覆盖不完整，缺少 {sorted(expected - observed)}。{detail}')
    return rows


def _key(row):
    return tuple(row[field] for field in _KEYS)


def _canonical(row):
    result = dict(row)
    if isinstance(result['report_date'], str):
        result['report_date'] = date.fromisoformat(result['report_date'])
    result.update({field: _decimal(row[field], field) for field in _METRICS})
    return result


def _stock_reports(rows):
    grouped = defaultdict(list)
    for row in rows:
        convert_a_stock_code(row['stock_code_raw'])
        if row['market_raw'] != 'A' or row['source'] != 'akshare':
            raise FinanceSourceError(f"财报来源或市场不一致：{row['stock_code_raw']}")
        if type(row['report_date']) is not date:
            raise ValueError('报告日期无效')
        report_type = get_report_type(row['report_date'])
        accepted_types = ('Q4', '年报') if report_type == '年报' else (report_type,)
        if row['report_type'] not in accepted_types:
            raise ValueError(f"报告期与类型不一致：{_key(row)}")
        grouped[row['stock_code_raw']].append(row)
    for stock_rows in grouped.values():
        validate_finance_rows(stock_rows)
    return grouped


def _read_ods(conn, condition, param, **params):
    query = text(f'SELECT id, {", ".join(CONTENT_FIELDS)} FROM {_TABLE} WHERE {condition} FOR UPDATE')
    query = query.bindparams(bindparam(param, expanding=True))
    return [_canonical(row) for row in conn.execute(query, params).mappings()]


def _content(row):
    return tuple(row[field] for field in CONTENT_FIELDS)


def _index_reports(rows):
    indexed = {}
    for row in rows:
        key = _key(row)
        if key in indexed:
            raise FinanceSourceError(f'财报业务键重复：{key}')
        indexed[key] = row
    return indexed


def _recent_quarters(through):
    quarters = [date(year, month, day) for year in (through.year - 1, through.year)
                for month, day in _PERIOD_END]
    return set([period for period in quarters if period <= through][-4:])


def reconcile_finance_rows(rows, through: date, *, tracked_periods=frozenset(), on_verified=None):
    """Verify all observed business keys and return validated report-period ODS slices."""
    if not rows or any(type(row['report_date']) is not date or row['report_date'] > through for row in rows):
        raise FinanceSourceError('财报来源为空或超出请求日期')
    stock_reports = _stock_reports(rows)
    expected = _index_reports(rows)
    observed = {row['report_date'] for row in rows}
    min_dates = {stock: min(row['report_date'] for row in reports) for stock, reports in stock_reports.items()}
    recent = _recent_quarters(through)
    tracked = {period for period in tracked_periods if period <= through}
    protected = recent | tracked
    scope = 'stock_code_raw IN :stocks AND report_date BETWEEN :earliest AND :through'
    params = dict(stocks=list(stock_reports), earliest=min(observed | protected).isoformat(), through=through.isoformat())

    def read_retained(conn):
        # Include retained recent and tracked periods even when the source
        # starts later than its previous response for an active ticker.
        return _index_reports(
            row for row in _read_ods(conn, scope, 'stocks', **params)
            if row['report_date'] in protected or row['report_date'] >= min_dates[row['stock_code_raw']]
        )

    with engine.begin() as conn:
        previous = read_retained(conn)
        missing = previous.keys() - expected.keys()
        if missing:
            raise FinanceSourceError(f'来源缺少已留存财报键：{sorted(missing)[:5]}')
        if any(row['source'] != 'akshare' or row['market_raw'] != 'A' for row in previous.values()):
            raise FinanceSourceError('财报业务键与其他来源或市场冲突')

        changed = [row for key, row in expected.items() if key in previous and _content(row) != _content(previous[key])]
        inserted = [row for key, row in expected.items() if key not in previous]
        _write_ods(conn, changed, inserted, previous)
        actual = read_retained(conn) if changed or inserted else previous
        if {key: _content(row) for key, row in actual.items()} != {key: _content(row) for key, row in expected.items()}:
            raise FinanceSourceError('财报 ODS 回读结果与已验证来源不一致')

        # Initial ODS inserts may contain many years. Track recent quarters,
        # previously tracked periods, and genuine revisions to retained reports.
        revised = {row['report_date'] for row in changed
                   if any(row[field] != previous[_key(row)][field] for field in _METRICS)}
        periods = (recent & observed) | revised | tracked
        reports = defaultdict(list)
        if periods:
            candidates = _read_ods(conn, "report_date IN :periods AND source = 'akshare' AND market_raw = 'A'",
                                   'periods', periods=[period.isoformat() for period in sorted(periods)])
            _stock_reports(candidates)
            for row in candidates:
                reports[row['report_date']].append(row)
        if on_verified is not None:
            # Durable retry intent is saved before the ODS transaction commits.
            on_verified(dict(reports))
    STATS['total_rows'] = len(changed) + len(inserted)
    return dict(reports)


def _write_ods(conn, changed, inserted, previous):
    def payload(row):
        return {**row, 'report_date': row['report_date'].isoformat(),
                **{field: str(row[field]) if row[field] is not None else None for field in _METRICS}}

    fields = (*CONTENT_FIELDS, 'fetch_time')
    if changed:
        assignments = ', '.join(f'{field} = :{field}' for field in fields if field not in _KEYS)
        conn.execute(text(f'UPDATE {_TABLE} SET {assignments} WHERE id = :id'),
                     [{**payload(row), 'id': previous[_key(row)]['id']} for row in changed])
    if inserted:
        conn.execute(text(f'INSERT INTO {_TABLE} ({", ".join(fields)}) VALUES ({", ".join(":" + field for field in fields)})'),
                     [payload(row) for row in inserted])


def insert_finance_data(rows):
    if not rows:
        return 0
    reconcile_finance_rows(rows, max(row['report_date'] for row in rows))
    return STATS['total_rows']


def collect_finance_reports(config: RuntimeConfig, *, tracked_periods=frozenset(), on_verified=None):
    """Probe every expected A share before incrementally writing the ODS."""
    through = config.report_through or date.today()
    rows = _collect_rows(config)
    return reconcile_finance_rows(rows, through, tracked_periods=tracked_periods, on_verified=on_verified)


def run_finance_ingest(config: RuntimeConfig, dry_run=False, stock_code=None) -> bool:
    """Legacy collector entry point, including single-stock dry runs."""
    try:
        rows = _collect_rows(config, stock_code=stock_code)
        if rows and not dry_run:
            reconcile_finance_rows(rows, config.report_through or date.today())
    except (FinanceSourceError, ValueError) as exc:
        logger.error('财报采集失败：%s', exc)
        return False
    return True


def main() -> bool:
    args = parse_args()
    return run_finance_ingest(build_runtime_config(args), dry_run=args.dry_run, stock_code=args.stock)


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
