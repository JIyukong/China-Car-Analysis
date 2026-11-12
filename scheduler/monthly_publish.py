"""Publish validated monthly ODS data with one transaction for the warehouse facts."""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import bindparam, text

from base_infra.db import engine


class ReconciliationError(RuntimeError):
    """Source and publication disagree; preserve the last good warehouse data."""


class ManualRequired(RuntimeError):
    def __init__(self, gaps: list[dict]):
        self.gaps = gaps
        super().__init__(f"月度发布等待人工补维（{len(gaps)} 项）")


@dataclass
class Publication:
    gaps: list[dict]
    dwd_rows: int
    dws_rows: int


_ODS = {'style': 'ods_sales_monthly_model', 'city': 'ods_sales_monthly_model_city'}
_DWD = {'style': 'dwd_sales_monthly_model', 'city': 'dwd_sales_monthly_model_city'}
_ADS_FIELDS = ('ads_proportion', 'ads_supplier', 'ads_supplier_mode', 'listed_at')
_COMMON_COLUMNS = (
    'stat_month', 'source', 'model_name', 'model_name_raw', 'model_id', 'oem_id',
    'brand_id', 'company_id', 'ads_id', 'ads_supplier_name', 'sales_volume',
    'price_min', 'price_max', 'rank_no', 'row_hash',
)
_COLUMNS = {
    'style': _COMMON_COLUMNS + ('manufacturer_name',),
    'city': _COMMON_COLUMNS + (
        'city_name', 'city_name_raw', 'area_id', 'subtype_name', 'level_raw',
        'vehicle_subtype_id', 'vehicle_type_id',
    ),
}


@contextmanager
def _publication_transaction():
    # Adjacent style months can update the same DWS rates. Release the session
    # lock only after commit/rollback, and discard a pooled connection if release fails.
    lock = {'name': 'carapp:monthly_publication', 'timeout': 30}
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
        acquired = conn.execute(text('SELECT GET_LOCK(:name, :timeout)'), lock).scalar_one()
        if acquired != 1:
            raise RuntimeError('未能取得月度发布锁')
        try:
            conn.commit()
            with conn.begin():
                yield conn
        finally:
            try:
                released = conn.execute(text('SELECT RELEASE_LOCK(:name)'), lock).scalar_one()
                if released != 1:
                    conn.invalidate()
            except Exception:
                conn.invalidate()
                raise


def _read(conn, sql, **params):
    return [dict(row) for row in conn.execute(text(sql), params).mappings()]


def _name(value):
    return (value or '').strip()


def _month_shift(month, offset):
    year, index = divmod(month.year * 12 + month.month - 1 + offset, 12)
    return date(year, index + 1, 1)


def _natural_key(chain, row):
    other = 'manufacturer_name' if chain == 'style' else 'city_name'
    return _name(row['model_name']).casefold(), _name(row[other]).casefold()


def _index(rows, key, description):
    indexed = {}
    for row in rows:
        value = key(row)
        if value in indexed:
            raise ReconciliationError(f'{description}业务键重复：{value}')
        indexed[value] = row
    return indexed


def _source(conn, chain, month):
    if chain == 'style':
        extra = 'TRIM(manufacturer_name_raw) AS manufacturer_name'
    else:
        extra = 'TRIM(city_name_raw) AS city_name, city_name_raw, level_raw'
    rows = _read(conn, f'''
        SELECT id, source, TRIM(model_name_raw) AS model_name, model_name_raw,
               sales_volume, price_range_raw, rank_no, row_hash, {extra}
          FROM {_ODS[chain]}
         WHERE stat_month = :month AND source = :source
    ''', month=month.isoformat(), source=f'16888_{chain}')
    if not rows:
        raise ReconciliationError(f'{chain} {month:%Y-%m} 没有已验证的 ODS 数据')
    for row in rows:
        key = _natural_key(chain, row)
        if not all(key) or row['sales_volume'] is None or row['sales_volume'] < 0:
            raise ReconciliationError(f'{chain} ODS 业务键或销量无效：{key}')
    _index(rows, lambda row: _natural_key(chain, row), 'ODS')
    eligible = [row for row in rows if row['sales_volume'] > 0]
    if not eligible:
        raise ReconciliationError(f'{chain} {month:%Y-%m} 没有正销量明细')
    return eligible


def _dimensions(conn, chain, month):
    if chain == 'style':
        ownership = '''
            LEFT JOIN dim_oem o ON o.oem_name = TRIM(s.manufacturer_name_raw)
            LEFT JOIN dim_model m ON m.oem_id = o.id AND m.model_name = TRIM(s.model_name_raw)
            LEFT JOIN (
                SELECT oem_id, MIN(brand_id) AS brand_id
                  FROM bridge_brand_oem
                 WHERE is_current = 1 AND effective_end_date = '9999-12-31'
                 GROUP BY oem_id HAVING COUNT(DISTINCT brand_id) = 1
            ) bridge ON bridge.oem_id = o.id
            LEFT JOIN dim_brand derived ON derived.id = bridge.brand_id AND derived.brand_code <> 'OTHER'
        '''
        extra = 'derived.id AS derived_brand_id, derived.brand_name AS derived_brand_name'
    else:
        ownership = '''
            LEFT JOIN dim_model m ON m.model_name = TRIM(s.model_name_raw)
            LEFT JOIN dim_oem o ON o.id = m.oem_id
            LEFT JOIN dim_area a ON a.city_short_name = TRIM(s.city_name_raw)
            LEFT JOIN dim_vehicle_subtype st ON st.subtype_name = TRIM(s.level_raw)
            LEFT JOIN dim_vehicle_type vt ON vt.id = st.vehicle_type_id
        '''
        extra = 'a.id AS area_id, st.id AS vehicle_subtype_id, st.subtype_name, vt.id AS vehicle_type_id'
    as_of = (_month_shift(month, 1) - timedelta(days=1)).isoformat()
    rows = _read(conn, f'''
        SELECT s.id, m.id AS model_id, o.id AS oem_id, o.oem_name,
               b.id AS brand_id, b.brand_code, b.brand_name,
               c.id AS company_id, c.company_code,
               z.id AS ads_id, z.ads_supplier AS ads_supplier_name, e.energy_type, e.vehicle_type,
               CASE WHEN z.id IS NULL THEN ads.ads_proportion_pct ELSE z.ads_proportion_pct END AS ads_proportion_pct,
               CASE WHEN z.id IS NULL THEN ads.ads_supplier ELSE z.ads_supplier END AS ads_supplier,
               CASE WHEN z.id IS NULL THEN ads.ads_supplier_mode ELSE z.ads_supplier_mode END AS ads_supplier_mode,
               CASE WHEN z.id IS NULL THEN ads.listed_at_raw ELSE z.listed_at_raw END AS listed_at_raw,
               ads_version.first_start, {extra}
          FROM {_ODS[chain]} s
          {ownership}
          LEFT JOIN dim_brand b ON b.id = m.brand_id
          LEFT JOIN dim_company c ON c.id = o.company_id
          LEFT JOIN dim_energy_vehicle e ON e.model_name = TRIM(s.model_name_raw)
          LEFT JOIN dim_model_ads_manual ads ON ads.model_name = TRIM(s.model_name_raw)
          LEFT JOIN (
              SELECT model_id, MIN(effective_start_date) AS first_start,
                     MIN(CASE WHEN :as_of BETWEEN effective_start_date AND effective_end_date THEN id END) AS active_id
                FROM dim_model_ads_zipper
               GROUP BY model_id
              HAVING COUNT(CASE WHEN :as_of BETWEEN effective_start_date AND effective_end_date THEN 1 END) <= 1
          ) ads_version ON ads_version.model_id = m.id
          LEFT JOIN dim_model_ads_zipper z ON z.id = ads_version.active_id
         WHERE s.stat_month = :month AND s.source = :source
    ''', month=month.isoformat(), source=f'16888_{chain}', as_of=as_of)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['id']].append(row)
    return grouped


def _gap(gaps, kind, key, *fields):
    gap = gaps.setdefault((kind, key), {'kind': kind, 'key': key, 'fields': []})
    for field in fields:
        if field not in gap['fields']:
            gap['fields'].append(field)


def _derive_models(conn, rows, dimensions):
    inserts = []
    updates = []
    for row in rows:
        matches = dimensions[row['id']]
        if len(matches) != 1:
            continue
        dim = matches[0]
        if dim['derived_brand_id'] is None or dim['company_code'] in (None, 'OTHER'):
            continue
        model, oem = _name(row['model_name']), dim['oem_name']
        if dim['model_id'] is None:
            # The existing DIM business key is (model_name, oem_id).
            code = hashlib.md5(f'{model}|{oem}'.encode('utf-8')).hexdigest()[:10].upper()
            inserts.append({
                'model_code': f'MODEL_{code}', 'model_name': model,
                'brand_id': dim['derived_brand_id'], 'brand_name': dim['derived_brand_name'],
                'oem_id': dim['oem_id'], 'oem_name': oem,
                'energy_type': dim['energy_type'] or '占位',
                'vehicle_type': dim['vehicle_type'] or '占位',
            })
        elif dim['brand_code'] == 'OTHER':
            updates.append({'id': dim['model_id'], 'brand_id': dim['derived_brand_id'],
                            'brand_name': dim['derived_brand_name']})
    _insert(conn, 'dim_model', inserts)
    _update(conn, 'dim_model', updates, ('brand_id', 'brand_name'))
    return bool(inserts or updates)


def _price_bounds(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*', value or '')
    if not match:
        return None, None
    low, high = match.groups()
    return low, high or low


def _candidates(chain, period, rows, dimensions):
    required, optional = {}, {}
    candidates = []
    for row in rows:
        model = _name(row['model_name'])
        matches = dimensions[row['id']]
        if len(matches) != 1:
            _gap(required, 'ownership', model, 'model_id', 'oem_id')
            continue
        dim = matches[0]
        if chain == 'style':
            oem = _name(row['manufacturer_name'])
            if dim['company_code'] in (None, 'OTHER'):
                fields = ('company_code', 'brand_code') if dim['oem_id'] is None else ('company_code',)
                _gap(required, 'ownership', oem, *fields)
            if dim['brand_code'] in (None, 'OTHER') and dim['oem_id'] is not None:
                _gap(required, 'ownership', oem, 'brand_code')
            missing_energy = [field for field in ('energy_type', 'vehicle_type') if dim[field] in (None, '', '占位')]
            if missing_energy:
                _gap(optional, 'energy', model, *missing_energy)
        else:
            if any(dim[field] is None for field in ('model_id', 'oem_id', 'brand_id', 'company_id')):
                _gap(required, 'ownership', model, 'model_id', 'oem_id')
            elif dim['brand_code'] == 'OTHER' or dim['company_code'] == 'OTHER':
                _gap(required, 'ownership', dim['oem_name'], 'brand_code', 'company_code')
            if dim['area_id'] is None:
                _gap(required, 'area', _name(row['city_name']), 'city_code', 'city_name', 'city_level',
                     'province_code', 'province_name', 'province_short_name', 'province_type', 'region_code', 'region_name')
            if dim['vehicle_type_id'] is None:
                _gap(required, 'subtype', _name(row['level_raw']) or '(空)', 'subtype_code', 'type_name')

        ads_values = (dim['ads_proportion_pct'], dim['ads_supplier'], dim['ads_supplier_mode'], dim['listed_at_raw'])
        missing_ads = [field for field, value in zip(_ADS_FIELDS, ads_values) if value in (None, '', '未知', '占位')]
        if ads_values[0] is not None and not 0 <= _numeric(ads_values[0]) <= 100:
            missing_ads.append('ads_proportion')
        before_first_ads = dim['first_start'] is not None and str(dim['first_start'])[:7] > period
        if missing_ads or dim['ads_id'] is None or not _name(dim['ads_supplier_name']):
            if not before_first_ads:
                _gap(optional, 'ads', model, *(missing_ads or _ADS_FIELDS))
            dim['ads_id'], dim['ads_supplier_name'] = None, None

        price_min, price_max = _price_bounds(row['price_range_raw'])
        candidate = {**row, **{key: dim[key] for key in ('model_id', 'oem_id', 'brand_id', 'company_id', 'ads_id', 'ads_supplier_name')}}
        candidate.update(stat_month=period, model_name=model, price_min=price_min, price_max=price_max)
        if not candidate['row_hash']:
            labels = (model, _name(row['manufacturer_name' if chain == 'style' else 'city_name']))
            payload = '|'.join((row['source'], period, *labels))
            candidate['row_hash'] = hashlib.md5(payload.encode('utf-8')).hexdigest()
        if chain == 'city':
            candidate.update({field: dim[field] for field in ('area_id', 'subtype_name', 'vehicle_subtype_id', 'vehicle_type_id')})
        candidates.append(candidate)
    return candidates, list(required.values()), list(optional.values())


def _insert(conn, table, rows):
    if rows:
        columns = ', '.join(rows[0])
        params = ', '.join(f':{key}' for key in rows[0])
        conn.execute(text(f'INSERT INTO {table} ({columns}) VALUES ({params})'), rows)


def _update(conn, table, rows, columns):
    if rows:
        assignments = ', '.join(f'{key} = :{key}' for key in columns)
        conn.execute(text(f'UPDATE {table} SET {assignments} WHERE id = :id'), rows)


def _dwd(conn, chain, period):
    return _read(conn, f'SELECT id, {", ".join(_COLUMNS[chain])} FROM {_DWD[chain]} WHERE stat_month = :period', period=period)


def _numeric(value):
    return None if value is None else Decimal(str(value))


def _write_dwd(conn, chain, period, source, candidates):
    key = lambda row: _natural_key(chain, row)
    expected = _index(candidates, key, 'DWD 候选')
    if {key(row): row['sales_volume'] for row in source} != {key(row): row['sales_volume'] for row in candidates}:
        raise ReconciliationError('ODS 与 DWD 候选业务键或销量不一致')
    _index(candidates, lambda row: row['row_hash'], 'DWD 候选哈希')
    if chain == 'style':
        _index(candidates, lambda row: row['model_id'], 'DWD 候选车型')
    else:
        _index(candidates, lambda row: (row['model_id'], row['area_id']), 'DWD 候选车型城市')
    previous = _index(_dwd(conn, chain, period), key, '已发布 DWD')
    if any(row['source'] != f'16888_{chain}' for row in previous.values()):
        raise ReconciliationError('目标月 DWD 含其他数据来源，需人工核查后发布')
    missing = previous.keys() - expected.keys()
    if missing:
        raise ReconciliationError(f'已发布 DWD 有 {len(missing)} 个业务键不在目标 ODS，需核查：{sorted(missing)[:10]}')

    columns = _COLUMNS[chain]
    inserts, updates = [], []
    for business_key, row in expected.items():
        payload = {column: row[column] for column in columns}
        if business_key in previous:
            updates.append({**payload, 'id': previous[business_key]['id']})
        else:
            inserts.append(payload)
    _update(conn, _DWD[chain], updates, columns)
    _insert(conn, _DWD[chain], inserts)

    actual = _index(_dwd(conn, chain, period), key, '发布后 DWD')
    if actual.keys() != expected.keys():
        raise ReconciliationError('DWD 落库后业务键集合不一致')
    for business_key, row in actual.items():
        target = expected[business_key]
        for field in columns:
            value, expected_value = row[field], target[field]
            if field in ('price_min', 'price_max'):
                value, expected_value = _numeric(value), _numeric(expected_value)
                if expected_value is not None:
                    expected_value = expected_value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if value != expected_value:
                raise ReconciliationError(f'DWD 落库校验失败：{business_key} {field}')
    return list(actual.values())


def _percentage(numerator, denominator, places):
    if not denominator:
        return None
    result = Decimal(numerator) * 100 / Decimal(denominator)
    return str(result.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _refresh_lifecycle(conn):
    latest = conn.execute(text("SELECT MAX(stat_month) FROM dwd_sales_monthly_model")).scalar_one()
    threshold = _month_shift(date.fromisoformat(f"{latest}-01"), -3).isoformat()
    for entity in ("model", "oem"):
        rows = _read(conn, f"""
            SELECT d.id, d.first_seen_month, d.last_seen_month, d.is_active, s.first_month, s.last_month
            FROM dim_{entity} d
            LEFT JOIN (
                SELECT {entity}_id, MIN(stat_month) AS first_month, MAX(stat_month) AS last_month
                FROM dwd_sales_monthly_model GROUP BY {entity}_id
            ) s ON s.{entity}_id = d.id
        """)
        updates = []
        for row in rows:
            first = f"{row['first_month']}-01" if row["first_month"] else None
            last = f"{row['last_month']}-01" if row["last_month"] else None
            active = int(last is not None and last >= threshold)
            if (first, last, active) != (
                str(row["first_seen_month"]) if row["first_seen_month"] else None,
                str(row["last_seen_month"]) if row["last_seen_month"] else None,
                row["is_active"],
            ):
                updates.append({"id": row["id"], "first_seen_month": first, "last_seen_month": last, "is_active": active})
        _update(conn, f"dim_{entity}", updates, ("first_seen_month", "last_seen_month", "is_active"))


def _write_groups(conn, group, month, dwd):
    table = f'dws_sales_monthly_{group}'
    group_id, group_name = f'{group}_id', f'{group}_name'
    previous_month, previous_year = _month_shift(month, -1), _month_shift(month, -12)
    next_month, next_year = _month_shift(month, 1), _month_shift(month, 12)
    totals = Counter()
    for row in dwd:
        totals[row[group_id]] += row['sales_volume']
    total_sales = sum(totals.values())

    def read(months):
        return _read(conn, f'''
            SELECT id, stat_month, {group_id}, {group_name}, total_sales,
                   market_share, yoy_growth, mom_growth, is_current
              FROM {table} WHERE stat_month IN ({', '.join(f':m{i}' for i in range(len(months)))})
        ''', **{f'm{i}': value.isoformat() for i, value in enumerate(months)})

    stored = _index(read((previous_year, previous_month, month, next_month, next_year)),
                    lambda row: (str(row['stat_month']), row[group_id]), table)

    def baseline(period, identifier):
        row = stored.get((period.isoformat(), identifier))
        return row['total_sales'] if row and row['is_current'] else None

    def growth(current, previous):
        return _percentage(current - previous, previous, '0.01') if previous else None

    # Load names for the validated groups only.
    names = dict(conn.execute(text(f'SELECT id, {group_name} FROM dim_{group} WHERE id IN :ids')
                             .bindparams(bindparam('ids', expanding=True)), {'ids': list(totals)}).tuples().all())
    if names.keys() != totals.keys():
        raise ReconciliationError(f'{table} 存在未匹配分组')

    inserts, updates, retire, future = [], [], [], []
    expected = {}
    fields = (group_name, 'total_sales', 'market_share', 'yoy_growth', 'mom_growth', 'is_current')
    for identifier, current in totals.items():
        row = {group_name: names[identifier], 'total_sales': current,
               'market_share': _percentage(current, total_sales, '0.0001'),
               'yoy_growth': growth(current, baseline(previous_year, identifier)),
               'mom_growth': growth(current, baseline(previous_month, identifier)), 'is_current': 1}
        existing = stored.get((month.isoformat(), identifier))
        if existing:
            updates.append({**row, 'id': existing['id']})
        else:
            inserts.append({**row, 'stat_month': month.isoformat(), group_id: identifier})
        expected[(month.isoformat(), identifier)] = row

    for (period, identifier), row in stored.items():
        if not row['is_current']:
            continue
        if period == month.isoformat() and identifier not in totals:
            retire.append({'id': row['id'], 'is_current': 0})
        elif period in (next_month.isoformat(), next_year.isoformat()):
            rate = 'mom_growth' if period == next_month.isoformat() else 'yoy_growth'
            corrected = {**row, rate: growth(row['total_sales'], totals.get(identifier))}
            future.append(corrected)
            expected[(period, identifier)] = corrected
    _insert(conn, table, inserts)
    _update(conn, table, updates, fields)
    _update(conn, table, retire, ('is_current',))
    _update(conn, table, future, ('mom_growth', 'yoy_growth'))

    actual = _index((row for row in read((month, next_month, next_year)) if row['is_current']),
                    lambda row: (str(row['stat_month']), row[group_id]), table)
    if actual.keys() != expected.keys():
        raise ReconciliationError(f'{table} 发布分组不一致')
    metrics = ('total_sales', 'market_share', 'yoy_growth', 'mom_growth')
    for key, row in actual.items():
        if row[group_name] != expected[key][group_name] or any(
            _numeric(row[field]) != _numeric(expected[key][field]) for field in metrics
        ):
            raise ReconciliationError(f'{table} 发布指标校验失败：{key}')
    if sum(row['total_sales'] for key, row in actual.items() if key[0] == month.isoformat()) != total_sales:
        raise ReconciliationError(f'{table} 发布总销量与 DWD 不一致')
    return len(totals)


def publish_month_result(chain: str, period: str, *, source_digest: str | None = None) -> Publication:
    """Publish one validated period, preserving last good facts on any failure."""
    if chain not in _ODS or not re.fullmatch(r'\d{4}-\d{2}', period):
        raise ValueError(f'无效的月度发布范围：{chain} {period}')
    month = date.fromisoformat(period + '-01')
    with _publication_transaction() as conn:
        if source_digest is not None:
            from scheduler.monthly_source import verify_ods_revision

            verify_ods_revision(chain, period, source_digest, connection=conn)
        source = _source(conn, chain, month)
        dimensions = _dimensions(conn, chain, month)
        if chain == 'style' and _derive_models(conn, source, dimensions):
            dimensions = _dimensions(conn, chain, month)
        candidates, required, optional = _candidates(chain, period, source, dimensions)
        if not required:
            dwd = _write_dwd(conn, chain, period, source, candidates)
            dws_rows = sum(_write_groups(conn, group, month, dwd) for group in ('company', 'brand')) if chain == 'style' else 0
            if chain == 'style':
                _refresh_lifecycle(conn)
    if required:
        # Identity discovery can persist so a manual mapping can be applied to a
        # new model. The DWD and DWS remain untouched until all required fields exist.
        raise ManualRequired(required)
    return Publication(optional, len(dwd), dws_rows)


def publish_month(chain: str, period: str, *, source_digest: str | None = None) -> list[dict]:
    """Return optional gaps after a successful warehouse publication."""
    return publish_month_result(chain, period, source_digest=source_digest).gaps


def resume_month_cli(chain: str) -> int:
    """Route standalone compatibility commands through validated batch state."""
    from scheduler.runner import main

    parser = argparse.ArgumentParser(description='发布已通过来源验证的月度批次')
    parser.add_argument('period', metavar='YYYY-MM')
    args = parser.parse_args()
    return main(['--chain', chain, '--period', args.period, '--resume'])


def affected_months(kind: str, key: str) -> list[tuple[str, str]]:
    """Resolve exact source-key matches for a manual edit, independently of DWD."""
    filters = {
        'energy': {'style': 'TRIM(s.model_name_raw) = :key'},
        'ads': {chain: 'TRIM(s.model_name_raw) = :key' for chain in _ODS},
        'area': {'city': 'TRIM(s.city_name_raw) = :key'},
        'subtype': {'city': 'TRIM(s.level_raw) = :key'},
        'ownership': {
            'style': '(TRIM(s.manufacturer_name_raw) = :key OR TRIM(s.model_name_raw) = :key)',
        },
    }
    found = []
    with engine.connect() as conn:
        for chain, condition in filters.get(kind, {}).items():
            rows = _read(conn, f'''
                SELECT DISTINCT s.stat_month FROM {_ODS[chain]} s
                 WHERE s.source = :source AND {condition}
            ''', key=key, source=f'16888_{chain}')
            found.extend((chain, str(row['stat_month'])[:7]) for row in rows)
        if kind == 'ownership':
            # Resolve the small set of model names once before scanning city sales.
            names = {key}
            names.update(conn.execute(text("""
                SELECT DISTINCT m.model_name FROM dim_model m
                JOIN dim_oem o ON o.id = m.oem_id WHERE o.oem_name = :key
            """), {'key': key}).scalars())
            names.update(conn.execute(text("""
                SELECT DISTINCT TRIM(model_name_raw) FROM ods_sales_monthly_model
                WHERE source = '16888_style' AND TRIM(manufacturer_name_raw) = :key
            """), {'key': key}).scalars())
            query = text("""
                SELECT DISTINCT stat_month FROM ods_sales_monthly_model_city
                WHERE source = '16888_city' AND TRIM(model_name_raw) IN :names
            """).bindparams(bindparam('names', expanding=True))
            periods = conn.execute(query, {'names': sorted(name for name in names if name is not None)}).scalars()
            found.extend(('city', str(period)[:7]) for period in periods)
    return sorted(found)
