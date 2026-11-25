"""Insert reviewed county cities after separately administered enum extension."""
import csv,json
from pathlib import Path
from sqlalchemy import text


def apply(conn,rows):
    inserted=0
    observed=[]
    for row in rows:
        if (row['city_code'],row['province_code']) not in {('659001','650000'),('469002','460000')} or row['city_level']!='县级市':
            raise ValueError('Unreviewed county identity')
        existing=conn.execute(text('SELECT * FROM dim_area WHERE city_code=:city_code OR city_short_name=:city_short_name'),row).mappings().all()
        if existing:
            if len(existing)!=1 or any(existing[0][k]!=v for k,v in row.items() if k!='city_tier'):
                raise ValueError('Existing county identity conflict')
        else:
            payload={**row,'source_file':'reviewed-county-areas-20261125'}
            columns=','.join(payload)
            conn.execute(text(f"INSERT INTO dim_area ({columns}) VALUES ({','.join(':'+k for k in payload)})"),payload)
            inserted+=1
        after=dict(conn.execute(text('SELECT * FROM dim_area WHERE city_code=:city_code'),row).mappings().one())
        if any(after[k]!=v for k,v in row.items() if k!='city_tier'):
            raise RuntimeError('County readback mismatch')
        observed.append(after)
    return {'inserted':inserted,'after':observed}


def run(output):
    from base_infra.config import get_settings
    from base_infra.db import engine
    from scheduler.workflow import State
    if get_settings().DB_USER.casefold() in ('root','admin','administrator',''):
        raise ValueError('Dedicated ETL account required')
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'status':'prepared'}),encoding='utf-8')
    source=Path('logs/etl/area-enrichment-20261125/county-areas-prepared.csv')
    rows=list(csv.DictReader(source.open(encoding='utf-8-sig')))
    if {r['city_code'] for r in rows}!={'659001','469002'} or len(rows)!=2:
        raise ValueError('Expected two reviewed counties')
    for row in rows: row['city_tier']=None
    with State().lock('warehouse',timeout=120),engine.begin() as conn:
        kind=conn.execute(text("SHOW COLUMNS FROM dim_area WHERE Field='city_level'")).mappings().one()['Type']
        if "'县级市'" not in kind: raise ValueError('Administrator enum extension not deployed')
        report=apply(conn,rows)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return report
