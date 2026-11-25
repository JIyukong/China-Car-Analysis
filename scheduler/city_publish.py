"""Publish independent city facts, retaining unreviewed sales explicitly."""

from datetime import date
from collections import defaultdict

from sqlalchemy import text

from pipelines.dim.city_model import REVIEW_FIELDS, discover_city_models


COLUMNS = (
    'stat_month', 'source', 'model_name', 'model_name_raw', 'city_model_id',
    'sales_volume', 'price_min', 'price_max', 'rank_no', 'row_hash',
    'city_name', 'city_name_raw', 'area_id', 'subtype_name', 'level_raw',
    'vehicle_subtype_id', 'vehicle_type_id',
)
TABLE = 'dwd_sales_monthly_city'


def area_ids(conn):
    """Resolve exact short/full labels only when they identify one area."""
    from scheduler import monthly_publish as shared
    matches = defaultdict(set)
    for row in shared._read(conn, 'SELECT * FROM dim_area'):
        for label in (row.get('city_short_name'), row.get('city_name')):
            if label:
                matches[label.strip()].add(row['id'])
        # Source city279 under province21 (青海), verified on the historical row
        # https://xl.16888.com/city-2023-04-21-279-0-0-0-1.html
        # Evidence: logs/etl/all-source-repair-20261125/city-hainan-source-evidence.json
        if row.get('city_code') == '632500' and row.get('province_code') == '630000':
            matches['海南'].add(row['id'])
    return {label: next(iter(ids)) for label, ids in matches.items() if len(ids) == 1}


def publish_city(conn, period, rows):
    from scheduler import monthly_publish as shared

    models = discover_city_models(conn, period, rows)
    required, optional, candidates = {}, {}, []
    for name in sorted({row['model_name'] for row in rows}):
        model = models[name]
        if model['review_status'] != 'confirmed':
            shared._gap(optional, 'city_model', name, *REVIEW_FIELDS)
            optional[('city_model', name)]['context'] = (
                f"归属未经确认；状态={model['review_status']}；production_type={model['production_type']}；"
                f"national_candidates={model['national_candidates']}"
            )
    rows = [row for row in rows if row['sales_volume'] > 0]
    areas = area_ids(conn)
    dimensions = shared._read(conn, '''
        SELECT s.id, s.city_name_raw, st.id AS vehicle_subtype_id,
               st.subtype_name, vt.id AS vehicle_type_id
        FROM ods_sales_monthly_model_city s
        LEFT JOIN dim_vehicle_subtype st ON st.subtype_name=TRIM(s.level_raw)
        LEFT JOIN dim_vehicle_type vt ON vt.id=st.vehicle_type_id
        WHERE s.stat_month=:month AND s.source='16888_city' AND s.sales_volume>0
    ''', month=period + '-01')
    for dim in dimensions:
        dim['area_id'] = areas.get(shared._name(dim['city_name_raw']))
    dimensions = shared._index(dimensions, lambda row: row['id'], '城市地区/级别映射')
    for row in rows:
        model = models[row['model_name']]
        dim = dimensions[row['id']]
        if dim['area_id'] is None:
            shared._gap(required, 'area', row['city_name'], 'city_code', 'city_name', 'city_level',
                        'province_code', 'province_name', 'province_short_name', 'province_type', 'region_code', 'region_name')
        if dim['vehicle_type_id'] is None:
            shared._gap(required, 'subtype', shared._name(row['level_raw']) or '(空)', 'subtype_code', 'type_name')
        low, high = shared._price_bounds(row['price_range_raw'])
        candidates.append({**row, **dim, 'id': row['id'], 'stat_month': period,
                           'model_name': model['model_name'], 'city_model_id': model['id'],
                           'price_min': low, 'price_max': high})
    if required:
        return None, list(required.values()) + list(optional.values())
    facts = shared._write_dwd(conn, 'city', period, rows, candidates, wide_city=True)
    # City lifecycle derives exclusively from successfully published city facts.
    latest = conn.execute(text(f'SELECT MAX(stat_month) FROM {TABLE}')).scalar_one()
    if latest is None:
        return facts, list(optional.values())
    threshold = shared._month_shift(date.fromisoformat(latest + '-01'), -3).isoformat()
    spans = shared._read(conn, f'''SELECT s.*, m.first_seen_month, m.last_seen_month, m.is_active
        FROM (SELECT city_model_id, MIN(stat_month) AS first_month,
              MAX(stat_month) AS last_month FROM {TABLE} GROUP BY city_model_id) s
        JOIN dim_city_model m ON m.id=s.city_model_id''')
    for span in spans:
        first, last = span['first_month'] + '-01', span['last_month'] + '-01'
        active = int(last >= threshold)
        if (str(span['first_seen_month']), str(span['last_seen_month']), span['is_active']) == (first, last, active):
            continue
        conn.execute(text('''UPDATE dim_city_model SET first_seen_month=:first,
            last_seen_month=:last, is_active=:active WHERE id=:id'''),
            {'first': first, 'last': last, 'active': active, 'id': span['city_model_id']})
    return facts, list(optional.values())
