"""monthly_publish 纯函数离线单测：不触库、不联网，只锁定纯逻辑分支。"""

from datetime import date
from decimal import Decimal

import pytest

from scheduler import monthly_publish as publish


def test_publish_month_result_rejects_unknown_chain_before_transaction():
    # 非法 chain 与 period 应在打开事务前直接 ValueError，避免无效触库。
    with pytest.raises(ValueError, match='无效的月度发布范围'):
        publish.publish_month_result('unknown', '2026-01')


@pytest.mark.parametrize('period', ['2026-1', '202601', 'abc', ''])
def test_publish_month_result_rejects_malformed_period(period):
    with pytest.raises(ValueError, match='无效的月度发布范围'):
        publish.publish_month_result('style', period)


def test_publish_month_result_rejects_calendar_impossible_period():
    # 2026-13 通过格式正则但越界，由 date.fromisoformat 抛原生 ValueError。
    with pytest.raises(ValueError):
        publish.publish_month_result('style', '2026-13')


@pytest.mark.parametrize(('raw', 'low', 'high'), [
    ('10-20', '10', '20'),
    (' 10.5 - 20.25 ', '10.5', '20.25'),
    ('15', '15', '15'),          # 单值时上下界相同
    (' 8 ', '8', '8'),
    ('12.0-', None, None),       # 残缺区间不解析
    ('面议', None, None),
    ('', None, None),
    (None, None, None),
])
def test_price_bounds_parses_ranges_and_rejects_garbage(raw, low, high):
    assert publish._price_bounds(raw) == (low, high)


@pytest.mark.parametrize(('numerator', 'denominator', 'expected'), [
    (1, 4, '25.00'),
    (1, 3, '33.33'),             # ROUND_HALF_UP，两位小数
    (25, 10000, '0.25'),
    (-5, 10, '-50.00'),
    (0, 10, '0.00'),
])
def test_percentage_quantizes_half_up(numerator, denominator, expected):
    assert publish._percentage(numerator, denominator, '0.01') == expected


def test_percentage_returns_none_without_base():
    # 零基或 None 基期无法计算增速/份额，返回 None 而不是抛异常。
    assert publish._percentage(1, 0, '0.01') is None
    assert publish._percentage(1, None, '0.01') is None


def test_percentage_decimal_inputs_avoid_float_artifacts():
    assert publish._percentage(Decimal('1'), Decimal('3'), '0.01') == '33.33'


@pytest.mark.parametrize(('month', 'offset', 'expected'), [
    (date(2026, 1, 1), 0, date(2026, 1, 1)),
    (date(2026, 1, 1), 1, date(2026, 2, 1)),
    (date(2026, 1, 1), -1, date(2025, 12, 1)),    # 跨年回退
    (date(2026, 12, 1), 1, date(2027, 1, 1)),     # 跨年前进
    (date(2026, 5, 1), 12, date(2027, 5, 1)),     # 同比平移
    (date(2026, 5, 1), -12, date(2025, 5, 1)),
])
def test_month_shift_wraps_year_boundaries(month, offset, expected):
    assert publish._month_shift(month, offset) == expected


@pytest.mark.parametrize(('chain', 'row', 'expected'), [
    ('style', {'model_name': ' Model A ', 'manufacturer_name': ' OEM '}, ('model a', 'oem')),
    ('city', {'model_name': 'Model A', 'city_name': ' City '}, ('model a', 'city')),
    ('style', {'model_name': None, 'manufacturer_name': None}, ('', '')),
])
def test_natural_key_strips_and_casefolds(chain, row, expected):
    assert publish._natural_key(chain, row) == expected


def test_index_rejects_duplicate_business_keys():
    rows = [{'id': 1, 'k': 'a'}, {'id': 2, 'k': 'a'}]
    with pytest.raises(publish.ReconciliationError, match='测试业务键重复'):
        publish._index(rows, lambda row: row['k'], '测试')


def test_index_keeps_first_row_for_distinct_keys():
    rows = [{'id': 1, 'k': 'a'}, {'id': 2, 'k': 'b'}]
    indexed = publish._index(rows, lambda row: row['k'], '测试')
    assert indexed == {'a': rows[0], 'b': rows[1]}


def test_gap_merges_fields_without_duplicates_and_preserves_order():
    gaps = {}
    publish._gap(gaps, 'energy', 'Model', 'energy_type')
    publish._gap(gaps, 'energy', 'Model', 'vehicle_type')
    publish._gap(gaps, 'energy', 'Model', 'energy_type')   # 重复字段不追加
    publish._gap(gaps, 'ownership', 'OEM', 'company_code')
    assert list(gaps.values()) == [
        {'kind': 'energy', 'key': 'Model', 'fields': ['energy_type', 'vehicle_type']},
        {'kind': 'ownership', 'key': 'OEM', 'fields': ['company_code']},
    ]


@pytest.mark.parametrize(('value', 'expected'), [(None, None), (1, Decimal('1')), ('2.50', Decimal('2.50'))])
def test_numeric_wraps_none_and_decimal_text(value, expected):
    assert publish._numeric(value) == expected


@pytest.mark.parametrize(('value', 'expected'), [(None, ''), ('  x  ', 'x'), ('', '')])
def test_name_normalizes_blank_and_whitespace(value, expected):
    assert publish._name(value) == expected
