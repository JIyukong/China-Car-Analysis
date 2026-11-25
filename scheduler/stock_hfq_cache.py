"""Free, single-vendor HFQ preparation using AKShare's Sina raw/factor decoder.

Only effective factors are carried forward. Quotes are never filled or removed
because a later response omitted them. Accepted factor versions live in the
warehouse commit manifest, independently of this retryable observation cache.
"""
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from uuid import uuid4

from scheduler.stock_history import FIELDS, decimal, price_hash, read_json, save_json, valid

VENDOR='sina'
BASIS='sina_hfq_v1'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def canonical_factors(records,market):
    if market not in ('A','HK') or not records:
        raise ValueError('Missing explicit HFQ factor evidence')
    result=[]
    for record in records:
        day=date.fromisoformat(str(record['date'])[:10])
        factor=decimal(record['hfq_factor'])
        cash=decimal(record['cash']) if market=='HK' else Decimal(0)
        if factor<=0:
            raise ValueError('Invalid HFQ factor')
        result.append({'date':day.isoformat(),'hfq_factor':format(factor.normalize(),'f'),
                       **({'cash':format(cash.normalize(),'f')} if market=='HK' else {})})
    result.sort(key=lambda r:r['date'])
    if len({r['date'] for r in result})!=len(result):
        raise ValueError('Duplicate HFQ factor effective date')
    return result


def effective(records,day,market):
    offset=bisect_right([r['date'] for r in records],day.isoformat())-1
    if offset<0:
        raise ValueError(f'Missing baseline factor at {day}')
    record=records[offset]
    return decimal(record['hfq_factor']), decimal(record['cash']) if market=='HK' else Decimal(0)


def changed_factor_dates(old,new,expected,market):
    new=canonical_factors(new,market)
    old=canonical_factors(old,market) if old else []
    return {day for day in expected if effective(new,day,market)!=
            (effective(old,day,market) if old else None)}


def fetch_frame(stock,directory,*,factors):
    """Keep native HTTP evidence, with scoped direct access and bounded retries."""
    import requests
    from scheduler.jobs.common import build_scheduler_config
    from pipelines.ingest.stock.network_runtime import patched_requests_runtime
    from scheduler.stock_history_sources import sina_frame
    config=build_scheduler_config()
    config.request_timeout=15
    config.direct_domains=(*config.direct_domains,'sina.com.cn','sina.cn')
    symbol=(stock['stock_code'][-2:].lower()+stock['stock_code'][:6] if stock['market']=='A'
            else stock['stock_code'].split('.')[0].zfill(5))
    original_send=requests.sessions.Session.send
    def capture(session,request,**kwargs):
        response=original_send(session,request,**kwargs)
        save_json(directory/f'http-{uuid4().hex}.json',{'vendor':VENDOR,'url':request.url,
                  'status':response.status_code,'body':response.text})
        return response
    with patched_requests_runtime(config):
        requests.sessions.Session.send=capture
        try:
            for attempt in range(3):
                try:
                    return sina_frame(symbol,stock['market'],factors=factors)
                except (requests.ConnectionError,requests.Timeout):
                    if attempt==2:
                        raise
        finally:
            requests.sessions.Session.send=original_send


@dataclass
class Preparation:
    rows:list
    issues:dict
    factors:list
    affected:set
    raw_changed:set
    fetched_raw:bool
    factor_version:str


def load_raw(path,stock):
    try:
        cache=read_json(path,{})
        if (cache.get('vendor')!=VENDOR or cache.get('stock_code')!=stock['stock_code'] or
                cache.get('market')!=stock['market'] or cache.get('checksum')!=digest(cache.get('rows'))):
            return {},None
        rows={}
        for original in cache['rows']:
            row={k:(decimal(v) if v is not None and k in FIELDS else v) for k,v in original.items()}
            row['trade_date']=date.fromisoformat(row['trade_date'])
            if row['trade_date'] in rows:
                return {},None
            rows[row['trade_date']]=row
        return rows,cache.get('checked_through')
    except (ValueError,TypeError,KeyError,ArithmeticError):
        return {},None


def prepare(stock,expected,requested,directory,cache_root,previous_factors,*,review=False,previous_raw_hashes=None):
    from scheduler.stock_history_sources import normalize
    if stock['market'] not in ('A','HK'):
        raise ValueError('Only A/H stocks allowed')
    if not expected or not requested<=expected:
        raise ValueError('Invalid requested trading dates')
    target=directory/stock['stock_code']
    target.mkdir(parents=True,exist_ok=True)
    cache_dir=cache_root/stock['stock_code']
    factor_frame=fetch_frame(stock,target,factors=True)
    factors=canonical_factors(factor_frame.to_dict('records'),stock['market'])
    version=digest(factors)
    affected=changed_factor_dates(previous_factors,factors,expected,stock['market'])
    save_json(cache_dir/'factors'/f'{version}.json',{'vendor':VENDOR,'factors':factors,'checksum':version})
    save_json(target/'factors.json',factors)
    old,checked=load_raw(cache_dir/'raw.json',stock)
    raw=dict(old)
    requested=set(requested)|affected
    if previous_raw_hashes is not None:
        requested|={day for day in expected & raw.keys()
                    if previous_raw_hashes.get(day)!=price_hash(raw[day])}
    def usable(row):
        return valid(row) and not (row.get('volume')==0 and len({row[k] for k in FIELDS[:4]})==1)
    fetched=(review or checked!=max(expected).isoformat() or not requested<=raw.keys() or
             any(not usable(raw[day]) for day in requested & raw.keys()))
    changed=set()
    if fetched:
        frame=fetch_frame(stock,target,factors=False)
        save_json(target/'raw-response.json',frame.to_dict('records'))
        fresh=normalize(frame,VENDOR,stock['market'])
        if not fresh:
            raise ValueError('Empty raw response cannot prove trading or suspension')
        for row in fresh:
            day=row['trade_date']
            if day in expected and (day not in old or price_hash(row)!=price_hash(old[day])):
                changed.add(day)
            raw[day]=row
        records=[raw[day] for day in sorted(raw)]
        # Hash the serialized representation, so dates/Decimals survive JSON roundtrips.
        serialized=json.loads(json.dumps(records,default=str))
        save_json(cache_dir/'raw.json',{'vendor':VENDOR,'stock_code':stock['stock_code'],
                  'market':stock['market'],'checked_through':max(expected).isoformat(),
                  'rows':serialized,'checksum':digest(serialized)})
    if previous_raw_hashes is not None:
        # Observations may be newer than the last committed warehouse revision.
        # Never consume an unpublished correction merely by refreshing the cache.
        changed={day for day in expected & raw.keys()
                 if previous_raw_hashes.get(day)!=price_hash(raw[day])}
    requested|=changed
    rows,issues=[],{}
    for day in sorted(requested):
        original=raw.get(day)
        if original is None:
            issues[day]='waiting_source'
            continue
        if not usable(original):
            issues[day]='invalid_source'
            continue
        multiplier,cash=effective(factors,day,stock['market'])
        row={**original,**{k:(decimal(original[k])*multiplier+cash).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
                          for k in FIELDS[:4]},'pct_change':None,'vendors':[VENDOR],
             'field_vendors':{k:[VENDOR] for k in ('volume','amount') if original.get(k) is not None},
             'factor_version':version,'raw_hash':price_hash(original)}
        if not valid(row):
            issues[day]='invalid_source'
            continue
        rows.append(row)
    save_json(target/'request.json',{'vendor':VENDOR,'basis':BASIS,'adjust':'hfq','factor_version':version,
              'fetched_raw':fetched,'cache':str(cache_dir/'raw.json'),'expected':len(expected),
              'requested':sorted(requested),'factor_affected':sorted(affected),'raw_changed':sorted(changed),
              'issues':issues})
    return Preparation(rows,issues,factors,affected,changed,fetched,version)
