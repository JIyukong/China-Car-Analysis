"""All six tiers from the last published Yicai classification (2025)."""
import json
from pathlib import Path
from sqlalchemy import text

SOURCE='https://www.yicai.com/news/102638963.html'
POLICY='https://m.yicai.com/news/103204902.html'
TIERS={k:'一线城市' for k in ('310100','110100','440300','440100')}
TIERS.update({k:'新一线城市' for k in ('510100','330100','500100','420100','320500',
    '610100','320100','430100','410100','120100','340100','370200','441900','330200','440600')})


def apply(conn):
    from scheduler.yicai_tiers_2025 import classifications
    source=classifications()
    rows=conn.execute(text('SELECT city_code,city_name,city_short_name,city_tier FROM dim_area')).mappings().all()
    existing={r['city_code']:r['city_tier'] for r in rows}
    desired={}; matched=set()
    for row in rows:
        name=row['city_short_name']
        if row['city_code']=='632500' and row['city_name']=='海南藏族自治州': name='海南'
        if name in source:
            if name in matched: raise ValueError('Ambiguous city classification')
            matched.add(name)
        desired[row['city_code']]=source.get(name,'其他')
    if matched!=source.keys(): raise ValueError('Required chart cities missing from dim_area')
    changes=[{'code':k,'tier':v} for k,v in desired.items() if existing[k]!=v]
    if changes:
        conn.execute(text('UPDATE dim_area SET city_tier=:tier WHERE city_code=:code'),changes)
    actual=dict(conn.execute(text('SELECT city_code,city_tier FROM dim_area')).tuples().all())
    if actual!=desired:
        raise RuntimeError('City tier readback mismatch')
    return {'year':2025,'source':SOURCE,'no_2026_classification_source':POLICY,
            'updated':len(changes),'verified':len(desired),'before':existing,'after':actual,
            'chart_cities':len(matched),'scope':'All six 2025 tiers; cities outside source chart are other'}


def run(output):
    from base_infra.config import get_settings
    from base_infra.db import engine
    from scheduler.workflow import State
    if get_settings().DB_USER.casefold() in ('root','admin','administrator',''):
        raise ValueError('Dedicated ETL account required')
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'status':'prepared','source':SOURCE}),encoding='utf-8')
    with State().lock('warehouse',timeout=120),engine.begin() as conn:
        report=apply(conn)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
