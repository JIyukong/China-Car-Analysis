"""Publish independent city facts, retaining unreviewed sales explicitly."""

from datetime import date

from sqlalchemy import text

from pipelines.dim.city_model import REVIEW_FIELDS, discover_city_models


COLUMNS = (
    'stat_month', 'source', 'model_name', 'model_name_raw', 'city_model_id',
    'sales_volume', 'price_min', 'price_max', 'rank_no', 'row_hash',
    'city_name', 'city_name_raw', 'area_id', 'subtype_name', 'level_raw',
    'vehicle_subtype_id', 'vehicle_type_id',
)
TABLE = 'dwd_sales_monthly_city'


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
    dimensions = shared._read(conn, '''
        SELECT s.id, a.id AS area_id, st.id AS vehicle_subtype_id,
               st.subtype_name, vt.id AS vehicle_type_id
        FROM ods_sales_monthly_model_city s
        LEFT JOIN dim_area a ON a.city_short_name=TRIM(s.city_name_raw)
        LEFT JOIN dim_vehicle_subtype st ON st.subtype_name=TRIM(s.level_raw)
        LEFT JOIN dim_vehicle_type vt ON vt.id=st.vehicle_type_id
        WHERE s.stat_month=:month AND s.source='16888_city' AND s.sales_volume>0
    ''', month=period + '-01')
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
    spans = shared._read(conn, f'''SELECT city_model_id, MIN(stat_month) AS first_month,
        MAX(stat_month) AS last_month FROM {TABLE} GROUP BY city_model_id''')
    for span in spans:
        first, last = span['first_month'] + '-01', span['last_month'] + '-01'
        conn.execute(text('''UPDATE dim_city_model SET first_seen_month=:first,
            last_seen_month=:last, is_active=:active WHERE id=:id'''),
            {'first': first, 'last': last, 'active': int(last >= threshold), 'id': span['city_model_id']})
    return facts, list(optional.values())
