"""City-only model discovery and reviewed wide attributes. No company ownership."""

import hashlib
import json
from collections import defaultdict

from sqlalchemy import bindparam, text


REVIEW_FIELDS = (
    'brand_code', 'brand_name', 'oem_code', 'oem_name', 'production_type',
    'import_channel', 'energy_type', 'vehicle_type', 'evidence',
)


def is_imported(name):
    return '进口' in name


def discover_city_models(conn, period, rows):
    """Source names are identities; national ODS supplies unconfirmed hints only."""
    names = sorted({row['model_name'].strip() for row in rows})
    # Only read national models actually present in this city batch. This does
    # not require the national DWD or a successful national publication.
    query = text('''
        SELECT DISTINCT TRIM(s.model_name_raw) AS model_name,
               TRIM(s.manufacturer_name_raw) AS oem_name, b.brand_code, b.brand_name
        FROM ods_sales_monthly_model s
        LEFT JOIN dim_oem o ON o.oem_name = TRIM(s.manufacturer_name_raw)
        LEFT JOIN bridge_brand_oem bridge ON bridge.oem_id = o.id
             AND bridge.is_current = 1 AND bridge.effective_end_date = '9999-12-31'
        LEFT JOIN dim_brand b ON b.id = bridge.brand_id
        WHERE s.source = '16888_style' AND s.stat_month <= :month
          AND TRIM(s.model_name_raw) IN :names
    ''')
    query = query.bindparams(bindparam('names', expanding=True))
    hints = defaultdict(list)
    for row in conn.execute(query, {'month': period + '-01', 'names': names}).mappings():
        hints[row['model_name']].append({key: row[key] for key in ('oem_name', 'brand_code', 'brand_name')})
    existing = {
        row['source_model_name']: dict(row) for row in conn.execute(text(
            "SELECT * FROM dim_city_model WHERE source = '16888_city'"
        )).mappings()
    }
    for name in names:
        candidates = sorted(hints[name], key=lambda x: (x['oem_name'] or '', x['brand_code'] or '', x['brand_name'] or ''))
        payload = dict(source='16888_city', source_model_name=name,
                       national_candidates=json.dumps(candidates, ensure_ascii=False, sort_keys=True))
        if name not in existing:
            digest = hashlib.sha256(f'16888_city|{name}'.encode()).hexdigest()[:32].upper()
            payload.update(model_code='CITY_' + digest, model_name=name,
                           production_type='imported' if is_imported(name) else 'unknown',
                           import_channel='unknown', review_status='conflict' if len(candidates) > 1 else 'pending',
                           first_seen_month=None, last_seen_month=None, is_active=0)
            columns = ', '.join(payload)
            conn.execute(text(f"INSERT INTO dim_city_model ({columns}) VALUES ({', '.join(':' + k for k in payload)})"), payload)
        else:
            # Never overwrite reviewed attributes, including import status.
            conn.execute(text('''UPDATE dim_city_model SET national_candidates=:national_candidates
                WHERE source=:source AND source_model_name=:source_model_name'''), payload)
    return {
        row['source_model_name']: dict(row) for row in conn.execute(text(
            "SELECT * FROM dim_city_model WHERE source = '16888_city'"
        )).mappings()
    }


def validate_city_model_review(key, values):
    if set(values) != set(REVIEW_FIELDS):
        raise ValueError('城市车型审核须完整填写品牌、主机厂、进口属性、产品属性及证据')
    result = {}
    for field in REVIEW_FIELDS:
        value = values[field]
        limit = 1000 if field == 'evidence' else 64 if field.endswith('_code') else 128
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f'{field} 须为有效文本，最多 {limit} 字')
        result[field] = value.strip()
    for field in ('brand_code', 'brand_name', 'oem_code', 'oem_name', 'evidence'):
        if result[field].casefold() in {'other', 'pending', '未知', '待确认', '占位', '不适用'}:
            raise ValueError(f'{field} 须填写已核实的实际值')
    allowed = {
        'production_type': {'domestic', 'imported', 'unknown'},
        'import_channel': {'official', 'parallel', 'unknown'},
        'energy_type': {'燃油', '新能源', '其他', '占位'},
        'vehicle_type': {'轿车', 'SUV', 'MPV', '其他', '占位'},
    }
    if any(result[field] not in choices for field, choices in allowed.items()):
        raise ValueError('城市车型属性值无效')
    if is_imported(key) and result['production_type'] != 'imported':
        raise ValueError('来源车型含进口标记，不能审核为国产或未知')
    if result['production_type'] != 'imported' and result['import_channel'] != 'unknown':
        raise ValueError('仅已确认进口车型可以填写进口渠道')
    return result


def apply_city_model_review(conn, key, values):
    values = validate_city_model_review(key, values)
    row = conn.execute(text('''SELECT id FROM dim_city_model
        WHERE source='16888_city' AND source_model_name=:key'''), {'key': key}).first()
    if row is None:
        raise ValueError('城市车型尚未建档，请先验收该月份来源')
    # Codes group attributes within this wide table; they are not national IDs.
    for entity in ('brand', 'oem'):
        collision = conn.execute(text(f'''SELECT id FROM dim_city_model WHERE id<>:id
            AND review_status='confirmed' AND
            (({entity}_code=:{entity}_code AND {entity}_name<>:{entity}_name)
             OR ({entity}_name=:{entity}_name AND {entity}_code<>:{entity}_code))'''),
            {**values, 'id': row[0]}).first()
        if collision:
            raise ValueError(f'{entity} 编码与名称冲突')
    conn.execute(text(f"UPDATE dim_city_model SET {', '.join(f'{k}=:{k}' for k in REVIEW_FIELDS)}, review_status='confirmed' WHERE id=:id"),
                 {**values, 'id': row[0]})
