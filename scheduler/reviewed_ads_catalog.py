"""Exact production identities verified against the official MIIT product catalog.

These explicit model brands override an OEM's default sales-brand fallback;
they do not change that fallback or create sales facts.
"""
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

MIIT = 'https://service.miit-eidc.org.cn/miitxxgk/gonggao/xxgk/index'
JLR = 'https://media.jlr.com/corporate/news/2024/06/jlr-and-chery-announce-new-value-creating-model-collaboration-next-era-electrification'
EVIDENCE = Path(__file__).resolve().parents[1] / 'logs/etl/all-source-repair-20261125'
BRANDS = (
    ('启境', 'CN', 0, 'https://www.aistalandauto.com/about/'),
    ('奕境', 'CN', 0, 'https://www.dfmc.com.cn/news/company/news_20260721_1142.html'),
    ('神行者', 'GB', 1, JLR),
)
MODELS = (
    ('启境GT7', '启境', 80, '广汽埃安', 7, '轿车'),
    ('启境GX7', '启境', 80, '广汽埃安', 7, 'SUV'),
    ('奕境X9', '奕境', 76, '东风汽车', 14, 'SUV'),
    ('神行者8', '神行者', 39, '奇瑞捷豹路虎', 12, 'SUV'),
)


def verified_identity(model, brand, oem):
    return any((model, brand, oem) == (m, b, o) for m, b, _, o, _, _ in MODELS)


def verify_evidence(directory=EVIDENCE):
    directory = Path(directory)
    specifications = (
        ('qijing', '广汽埃安新能源汽车股份有限公司', '启境牌', None),
        ('CJL6510AF1REEV', '奇瑞捷豹路虎汽车有限公司', '神行者(FREELANDER)牌', 'CJL6510AF1REEV'),
        ('DFM6531S5F4REEV', '东风汽车集团有限公司', '奕境牌', 'DFM6531S5F4REEV'),
        ('AHC6510SHEVX0C', '广汽埃安新能源汽车股份有限公司', '启境牌', 'AHC6510SHEVX0C'),
    )
    digests = {}
    for suffix, producer, trademark, model in specifications:
        path = directory / f'ads-miit-{suffix}.json'
        raw = path.read_bytes()
        data = json.loads(raw)
        rows = data.get('cpList', [])
        if (data.get('handleResult', {}).get('respCode') != 200 or not rows
                or data.get('countResult', {}).get('total') != len(rows)):
            raise ValueError('Official evidence is not complete')
        if any(r.get('qymc') != producer or r.get('cpsb') != trademark for r in rows):
            raise ValueError('Official producer/trademark evidence conflict')
        if model is not None and (len(rows) != 1 or rows[0].get('clxh') != model):
            raise ValueError('Official product identity conflict')
        if model is None and not {'AHC7000BEVW0A', 'AHC6510SHEVX0C'} <= {r.get('clxh') for r in rows}:
            raise ValueError('Missing reviewed sedan/SUV production identities')
        digests[path.name] = hashlib.sha256(raw).hexdigest()
    return digests


def seed(conn):
    """Apply fixed reviewed identities; caller owns transaction/account/locking."""
    from pipelines.dim.seed_dim_oem_brand import make_brand_code

    report = {'inserted_brands': 0, 'inserted_models': 0, 'inserted_classifications': 0,
              'before': [], 'after': []}
    brands = {}
    for name, country, joint, _ in BRANDS:
        desired = dict(brand_code=make_brand_code(name), brand_name=name,
                       brand_origin_country=country, is_joint_venture_brand=joint)
        rows = conn.execute(text('SELECT * FROM dim_brand WHERE brand_name=:brand_name OR brand_code=:brand_code'), desired).mappings().all()
        if rows:
            if len(rows) != 1 or any(rows[0][k] != v for k, v in desired.items()):
                raise ValueError(f'Existing brand conflict: {name}')
            identifier = rows[0]['id']
        else:
            result = conn.execute(text('''INSERT INTO dim_brand
                (brand_code,brand_name,brand_origin_country,is_joint_venture_brand)
                VALUES (:brand_code,:brand_name,:brand_origin_country,:is_joint_venture_brand)'''), desired)
            identifier = result.lastrowid
            report['inserted_brands'] += 1
        observed = dict(conn.execute(text('SELECT * FROM dim_brand WHERE id=:id'), {'id': identifier}).mappings().one())
        if any(observed[k] != v for k, v in desired.items()):
            raise RuntimeError('Brand readback mismatch')
        brands[name] = identifier
    for model, brand, oem_id, oem, company, vehicle in MODELS:
        stored_oem = conn.execute(text('SELECT oem_name,company_id FROM dim_oem WHERE id=:id'), {'id': oem_id}).mappings().one()
        if stored_oem['oem_name'] != oem or stored_oem['company_id'] != company:
            raise ValueError(f'Existing OEM identity conflict: {oem}')
        classification = dict(model_name=model, energy_type='新能源', vehicle_type=vehicle,
                              source_file='scheduler/reviewed_ads_catalog.py#MIIT-20261125')
        stored_class = conn.execute(text('SELECT * FROM dim_energy_vehicle WHERE model_name=:model_name'), classification).mappings().one_or_none()
        if stored_class is not None:
            if any(stored_class[k] != classification[k] for k in ('model_name', 'energy_type', 'vehicle_type')):
                raise ValueError(f'Existing classification conflict: {model}')
        else:
            conn.execute(text('''INSERT INTO dim_energy_vehicle (model_name,energy_type,vehicle_type,source_file)
                VALUES (:model_name,:energy_type,:vehicle_type,:source_file)'''), classification)
            report['inserted_classifications'] += 1
        observed_class = conn.execute(text('SELECT * FROM dim_energy_vehicle WHERE model_name=:model_name'), classification).mappings().one()
        if any(observed_class[k] != classification[k] for k in ('model_name', 'energy_type', 'vehicle_type')):
            raise RuntimeError('Classification readback mismatch')
        code = hashlib.md5(f'{model}|{oem}'.encode()).hexdigest()[:10].upper()
        desired = dict(model_code=f'MODEL_{code}', model_name=model, brand_id=brands[brand],
                       brand_name=brand, oem_id=oem_id, oem_name=oem,
                       energy_type='新能源', vehicle_type=vehicle)
        rows = conn.execute(text('SELECT * FROM dim_model WHERE model_name=:model_name OR model_code=:model_code'), desired).mappings().all()
        report['before'].extend(dict(r) for r in rows)
        if rows:
            if len(rows) != 1 or any(rows[0][k] != v for k, v in desired.items()):
                raise ValueError(f'Existing model identity conflict: {model}')
        else:
            conn.execute(text('''INSERT INTO dim_model
                (model_code,model_name,brand_id,brand_name,oem_id,oem_name,energy_type,vehicle_type)
                VALUES (:model_code,:model_name,:brand_id,:brand_name,:oem_id,:oem_name,:energy_type,:vehicle_type)'''), desired)
            report['inserted_models'] += 1
        after = dict(conn.execute(text('SELECT * FROM dim_model WHERE model_code=:model_code'), desired).mappings().one())
        if any(after[k] != v for k, v in desired.items()):
            raise RuntimeError('Model readback mismatch')
        report['after'].append(after)
    return report


def run(report_path):
    from base_infra.db import engine
    from scheduler.workflow import State

    proof = verify_evidence()
    output = Path(report_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preflight local artifact writability before any warehouse mutation.
    output.write_text(json.dumps({'status': 'prepared', 'official_source': MIIT,
                                  'evidence_sha256': proof}), encoding='utf8')
    with State().lock('warehouse', timeout=120), engine.begin() as conn:
        user = conn.execute(text('SELECT CURRENT_USER()')).scalar_one().split('@')[0].casefold()
        if 'etl' not in user or user in {'root', 'admin', 'administrator'}:
            raise ValueError('Independent ETL account required')
        report = seed(conn)
    report.update(status='committed', official_source=MIIT, evidence_sha256=proof)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf8')
    return report
