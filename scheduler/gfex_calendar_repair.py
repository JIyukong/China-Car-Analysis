"""Bounded GFEX calendar from exchange-authored notices reproduced by members."""
import json
from datetime import date,timedelta
from pathlib import Path


def calendar_rows(manifest):
    start,end=map(date.fromisoformat,(manifest['start'],manifest['end']))
    if (start,end)!=(date(2023,7,21),date(2025,12,31)):
        raise ValueError('Only reviewed GFEX historical interval permitted')
    notices=manifest['annual_notices']
    if set(notices)!={'2023','2024','2025'}:
        raise ValueError('All three annual notices required')
    closed=set()
    for year,notice in notices.items():
        if notice['issuer']!='GFEX' or not notice['source_ref'].startswith('https://'):
            raise ValueError('GFEX-authored notice provenance required')
        for first,last in notice['closures']:
            d,stop=date.fromisoformat(first),date.fromisoformat(last)
            if d>stop or d.year!=int(year) or stop.year!=int(year):
                raise ValueError('Invalid closure range')
            while d<=stop:
                closed.add(d);d+=timedelta(days=1)
    rows=[];d=start
    while d<=end:
        ref=notices[str(d.year)]['source_ref']
        if len(ref)>255:
            raise ValueError('Source URL exceeds existing column')
        rows.append({'market':'GFEX','trade_date':d,'is_open':int(d.weekday()<5 and d not in closed),'source_ref':ref})
        d+=timedelta(days=1)
    return rows


def run(manifest,path):
    from sqlalchemy import text
    from base_infra.db import engine
    rows=calendar_rows(manifest)
    byday={r['trade_date']:r for r in rows}
    with engine.begin() as conn:
        user=conn.execute(text('SELECT CURRENT_USER()')).scalar()
        if user.split('@')[0].lower() in ('root','admin','administrator'):
            raise ValueError('Independent ETL account required')
        # Observed rows can validate proposed opens; absence never implies closure.
        observed=conn.execute(text("SELECT DISTINCT trade_date FROM ods_futures_daily WHERE commodity_code_raw='LC' AND source='akshare' AND contract_type='main_continuous' AND trade_date BETWEEN :first AND :last"),{'first':min(byday),'last':max(byday)}).scalars().all()
        if any(not byday[d]['is_open'] for d in observed):
            raise ValueError('Exchange closure contradicts observed LC quote')
        existing=conn.execute(text("SELECT trade_date,is_open FROM etl_market_calendar WHERE market='GFEX' AND trade_date BETWEEN :first AND :last FOR UPDATE"),{'first':min(byday),'last':max(byday)}).all()
        if any(int(v)!=byday[d]['is_open'] for d,v in existing):
            raise ValueError('Reviewed GFEX calendar conflicts with current calendar')
        conn.execute(text('INSERT INTO etl_market_calendar (market,trade_date,is_open,source_ref) VALUES (:market,:trade_date,:is_open,:source_ref) ON DUPLICATE KEY UPDATE source_ref=VALUES(source_ref)'),rows)
        actual=conn.execute(text("SELECT trade_date,is_open,source_ref FROM etl_market_calendar WHERE market='GFEX' AND trade_date BETWEEN :first AND :last ORDER BY trade_date"),{'first':min(byday),'last':max(byday)}).all()
        if [tuple(x) for x in actual]!=[(r['trade_date'],r['is_open'],r['source_ref']) for r in rows]:
            raise RuntimeError('GFEX full calendar readback mismatch')
    result={'market':'GFEX','calendar_rows_verified':len(rows),'inserted':len(rows)-len(existing),'open_sessions':sum(r['is_open'] for r in rows),'observed_lc_dates_validated':len(observed),'first':str(min(byday)),'last':str(max(byday)),'evidence':manifest,'method':'GFEX Article22 Monday-Friday minus explicit annual GFEX-authored closures; faithful mirrors retained; no SSE calendar copied'}
    (Path(path).parent/'futures-gfex-calendar-applied.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    return result
