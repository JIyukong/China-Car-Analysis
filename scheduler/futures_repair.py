"""Reviewed SHFE history repairs; no schema changes or inferred holidays."""
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SOURCE = 'https://www.shfe.com.cn/data/config/js/trade-data.js'


def parse_shfe_calendar(raw, *, through_year=2025):
    pairs = re.findall(r"(\d{4}):'([^']*)'", raw)
    if not pairs or len({y for y, _ in pairs}) != len(pairs):
        raise ValueError('Invalid SHFE calendar years')
    rows = []
    for year, values in pairs:
        year = int(year)
        closed = {date.fromisoformat(f'{v[:4]}-{v[4:6]}-{v[6:]}') for v in values.split(',') if v}
        if any(d.year != year for d in closed):
            raise ValueError('SHFE calendar year mismatch')
        if year > through_year:
            continue
        d = date(year, 1, 1)
        while d.year == year:
            rows.append(dict(market='SHFE', trade_date=d, is_open=int(d not in closed), source_ref=SOURCE))
            d += timedelta(days=1)
    return rows


def run(path):
    from sqlalchemy import text
    from base_infra.db import engine
    path = Path(path)
    plan = json.loads(path.read_text(encoding='utf8'))
    if isinstance(plan,dict) and plan.get('kind')=='gfex_reviewed_calendar':
        from scheduler.gfex_calendar_repair import run as apply_gfex
        return apply_gfex(plan,path)
    if isinstance(plan,dict) and plan.get('kind')=='verified_continuous_gaps':
        return apply_gaps(plan,path)
    if len(plan) != 6:
        raise ValueError('Expected exactly six reviewed corrections')
    calendars = parse_shfe_calendar((path.parent/'futures-shfe-trade-data.js').read_text(encoding='utf-8-sig'))
    if len(calendars) != 4018:
        raise ValueError('Expected SHFE 2015-2025 complete-year coverage')
    prepared=[]
    for item in plan:
        if item['code'] not in ('AL','CU') or len(item['shfe_matches']) != 1:
            raise ValueError('Nonunique official contract match')
        official=item['shfe_matches'][0]
        raw=item['sina']
        if (official['VOLUME']!=int(raw['v']) or official['OPENINTEREST']!=int(raw['p']) or
            Decimal(str(official['CLOSEPRICE']))!=Decimal(raw['c'])):
            raise ValueError('Official contract identity mismatch')
        original={k:Decimal(raw[v]) for k,v in [('open','o'),('high','h'),('low','l'),('close','c')]}
        corrected={k:Decimal(str(official[v])) for k,v in [('open','OPENPRICE'),('high','HIGHESTPRICE'),('low','LOWESTPRICE'),('close','CLOSEPRICE')]}
        if not corrected['low']<=min(corrected['open'],corrected['close'])<=max(corrected['open'],corrected['close'])<=corrected['high']:
            raise ValueError('Invalid official OHLC')
        changes={k:v for k,v in corrected.items() if v!=original[k]}
        if len(changes)!=1:
            raise ValueError('Only one reviewed erroneous field permitted')
        prepared.append((item,original,corrected,changes))
    evidence=[]
    with engine.begin() as conn:
        user=conn.execute(text('SELECT CURRENT_USER()')).scalar()
        if user.split('@')[0].lower() in ('root','admin','administrator'):
            raise ValueError('Independent ETL account required')
        for row in calendars:
            existing=conn.execute(text('SELECT is_open FROM etl_market_calendar WHERE market=:market AND trade_date=:trade_date FOR UPDATE'),row).scalar()
            if existing is not None and int(existing)!=row['is_open']:
                raise ValueError(f'Calendar conflict: {row}')
        conn.execute(text('INSERT INTO etl_market_calendar (market,trade_date,is_open,source_ref) VALUES (:market,:trade_date,:is_open,:source_ref) ON DUPLICATE KEY UPDATE source_ref=VALUES(source_ref)'),calendars)
        query=text("SELECT open,high,low,close,volume,open_interest FROM ods_futures_daily WHERE commodity_code_raw=:code AND trade_date=:date AND source='akshare' AND contract_type='main_continuous' FOR UPDATE")
        for item,original,corrected,changes in prepared:
            key={'code':item['code'],'date':date.fromisoformat(item['date'])}
            before=dict(conn.execute(query,key).mappings().one())
            if any(before[k]!=int(item['sina'][v]) for k,v in [('volume','v'),('open_interest','p')]):
                raise ValueError('Current volume/OI conflict')
            prices={k:before[k] for k in original}
            if prices not in (original,corrected):
                raise ValueError('Current OHLC conflict')
            field,value=next(iter(changes.items()))
            conn.execute(text(f"UPDATE ods_futures_daily SET `{field}`=:value WHERE commodity_code_raw=:code AND trade_date=:date AND source='akshare' AND contract_type='main_continuous'"),{**key,'value':value})
            after=dict(conn.execute(query,key).mappings().one())
            if any(after[k]!=v for k,v in corrected.items()) or any(after[k]!=before[k] for k in ('volume','open_interest')):
                raise RuntimeError('Correction readback mismatch')
            evidence.append({'code':item['code'],'date':item['date'],'before':before,'after':after,'source_ref':item['source'],'contract':item['shfe_matches'][0]['DELIVERYMONTH']})
        observed=conn.execute(text("SELECT trade_date,is_open FROM etl_market_calendar WHERE market='SHFE' AND trade_date BETWEEN '2015-01-01' AND '2025-12-31' ORDER BY trade_date")).all()
        if [(r[0],int(r[1])) for r in observed]!=[(r['trade_date'],r['is_open']) for r in sorted(calendars,key=lambda r:r['trade_date'])]:
            raise RuntimeError('Calendar readback mismatch')
    result={'calendar_rows':len(calendars),'corrected_rows':len(evidence),'corrections':evidence,'unresolved':['SHFE 1999-2014 calendar','GFEX historical calendar']}
    (path.parent/'futures-applied.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf8')
    registry=Path('data/market/futures_official_corrections.json');registry.parent.mkdir(parents=True,exist_ok=True)
    registry.write_text(json.dumps(evidence,indent=2,default=str),encoding='utf8')
    print(json.dumps({'calendar_rows':len(calendars),'corrected_rows':len(evidence)}))
    return result

def validate_gap(item):
    code=item['code']
    expected={'CU':('cum','\u6caa\u94dc\u4e3b\u8fde',1),'AL':('alm','\u6caa\u94dd\u4e3b\u8fde',2)}
    if code not in expected or (item['provider_symbol'],item['provider_name'],item['volume_oi_multiplier'])!=expected[code]:
        raise ValueError('Explicit reviewed main-continuous identity required')
    em=item['em_row'];official=item['official']
    if em[0]!=item['date'] or official['PRODUCTID'].strip()!=code.lower()+'_f':
        raise ValueError('Source date/product mismatch')
    for k,i,m in [('OPENPRICE',1,1),('HIGHESTPRICE',3,1),('LOWESTPRICE',4,1),('CLOSEPRICE',2,1),('VOLUME',5,expected[code][2]),('OPENINTEREST',12,expected[code][2])]:
        if Decimal(str(official[k]))!=Decimal(em[i])*m:
            raise ValueError('Official source differs from explicit continuous series')
    row={k:Decimal(str(official[v])) for k,v in [('open','OPENPRICE'),('high','HIGHESTPRICE'),('low','LOWESTPRICE'),('close','CLOSEPRICE'),('settle','SETTLEMENTPRICE')]}
    row.update(volume=int(official['VOLUME']),open_interest=int(official['OPENINTEREST']))
    from pipelines.ingest.common.market_quality import valid_price_rows
    valid_price_rows([{**row,'trade_date':date.fromisoformat(item['date'])}])
    return row


def apply_gaps(plan,path):
    from datetime import datetime
    from sqlalchemy import text
    from base_infra.db import engine
    if len(plan['rows'])!=4 or len({(x['code'],x['date']) for x in plan['rows']})!=4:
        raise ValueError('Expected four distinct reviewed gaps')
    prepared=[(x,validate_gap(x)) for x in plan['rows']]
    evidence=[]
    # Retain reviewed source evidence before any database mutation.
    registry=Path('data/market/futures_continuous_gap_evidence.json');registry.parent.mkdir(parents=True,exist_ok=True)
    registry.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8')
    with engine.begin() as conn:
        user=conn.execute(text('SELECT CURRENT_USER()')).scalar()
        if user.split('@')[0].lower() in ('root','admin','administrator'):
            raise ValueError('Independent ETL account required')
        for item,values in prepared:
            dim=conn.execute(text("SELECT id,commodity_name,exchange,main_symbol FROM dim_commodity WHERE commodity_code=:code AND is_active=1 FOR UPDATE"),{'code':item['code']}).mappings().one()
            if dim['exchange']!='SHFE' or dim['main_symbol']!=item['code']+'0':
                raise ValueError('Unexpected commodity mapping')
            key={'code':item['code'],'date':date.fromisoformat(item['date'])}
            query=text("SELECT commodity_id,main_symbol_raw,exchange_raw,open,high,low,close,volume,open_interest,settle,amount FROM ods_futures_daily WHERE commodity_code_raw=:code AND trade_date=:date AND source='akshare' AND contract_type='main_continuous' FOR UPDATE")
            existing=conn.execute(query,key).mappings().one_or_none()
            wanted={**values,'commodity_id':dim['id'],'main_symbol_raw':dim['main_symbol'],'exchange_raw':'SHFE','amount':None}
            if existing is not None:
                if dict(existing)!=wanted:
                    raise ValueError('Gap repair refuses to replace existing data')
            else:
                row={**key,**values,'commodity_id':dim['id'],'commodity_name':dim['commodity_name'],'symbol':dim['main_symbol'],'fetch_time':datetime.now()}
                conn.execute(text("INSERT INTO ods_futures_daily (commodity_id,commodity_code_raw,commodity_name_raw,main_symbol_raw,exchange_raw,contract_type,trade_date,open,high,low,close,volume,open_interest,settle,amount,source,fetch_time) VALUES (:commodity_id,:code,:commodity_name,:symbol,'SHFE','main_continuous',:date,:open,:high,:low,:close,:volume,:open_interest,:settle,NULL,'akshare',:fetch_time)"),row)
            observed=dict(conn.execute(query,key).mappings().one())
            if observed!=wanted:
                raise RuntimeError('Gap full-row readback mismatch')
            evidence.append({'code':item['code'],'date':item['date'],'inserted':existing is None,'after':observed,'provider_symbol':item['provider_symbol'],'source_ref':item['source_ref'],'official_source_ref':item['official_source_ref'],'contract':item['official']['DELIVERYMONTH']})
    acceptance=reconcile_gap_acceptance(sorted({date.fromisoformat(x['date']) for x in plan['rows']}))
    result={'acceptance':acceptance,'gap_rows_verified':len(evidence),'rows_inserted':sum(x['inserted'] for x in evidence),'rows':evidence}
    (path.parent/'futures-four-gaps-applied.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    return result


def reconcile_gap_acceptance(days,*,state=None):
    from scheduler.market_workflow import expected_sessions,actual_sessions
    from scheduler.workflow import State
    state=state if state is not None else State()
    task='load_futures_daily'
    expected,unknown=expected_sessions(task,days)
    actual=actual_sessions(task,days)
    for day in days:
        if unknown[day] or expected[day]!={'CU','AL'} or not expected[day]<=actual[day]:
            raise ValueError('Verified gap dates still have incomplete expected ODS coverage')
    for day in days:
        state.save(task,str(day),'ods_accepted',message='2 instruments reconciled: reviewed Eastmoney unadjusted main-continuous identity + SHFE official contract evidence; Sina omission retained in provenance',gaps=[])
    return [{'date':str(day),'status':'ods_accepted','expected':sorted(expected[day]),'actual':sorted(actual[day])} for day in days]
