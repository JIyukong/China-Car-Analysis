"""Database-driven A/H history audit and evidence-gated HFQ publication.

Formal schemas are never changed. Local manifests are evidence, not a substitute
for reading the warehouse. An absent manifest makes existing prices unverified.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
import logging

START = date(2017, 1, 1)
BASIS = 'sina_hfq_v1'
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'amount', 'pct_change')
ROOT = Path(__file__).resolve().parents[1] / 'logs' / 'etl' / 'stock_history'
logger = logging.getLogger(__name__)


def days(first, last):
    while first <= last:
        yield first
        first += timedelta(days=1)


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite market value')
    return result


def valid(row):
    try:
        o, h, l, c = (decimal(row[k]) for k in FIELDS[:4])
        return (0 < l <= min(o, c) <= max(o, c) <= h <= Decimal('99999999.9999')
                and (row.get('volume') is None or (0 <= decimal(row['volume']) <= 2**63-1
                     and decimal(row['volume']) == decimal(row['volume']).to_integral_value()))
                and (row.get('amount') is None or 0 <= decimal(row['amount']) <= Decimal('9999999999999999.99')))
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return False


def price_hash(row):
    values = [None if row.get(k) is None else format(decimal(row[k]).normalize(), 'f') for k in FIELDS]
    return sha256(json.dumps(values).encode()).hexdigest()


def key(code, day):
    return f'{code}|{day.isoformat()}'


def audit_snapshot(snapshot, first, last, accepted):
    expected, missing, unverified = defaultdict(set), defaultdict(set), defaultdict(set)
    references, conflicts, publish_pending = [], [], defaultdict(set)
    retained_invalid=[]
    eligible = [s for s in snapshot['stocks'] if s['market'] in ('A','HK')]
    if not eligible:
        references.append({'reason':'empty_stock_universe'})
    for market in {s['market'] for s in eligible}:
        market_first=max(first,min(s.get('listed_date') or first for s in eligible if s['market']==market))
        unknown=defaultdict(list)
        for day in days(market_first,last):
            if snapshot['calendar'].get((market,day)) is None:
                unknown[day.year].append(day)
        references.extend({'market':market,'year':year,'first':ds[0],'last':ds[-1],
                           'missing_days':len(ds),'reason':'calendar_unknown'} for year,ds in sorted(unknown.items()))
    dwd_index = {(r['stock_code'], r['trade_date']): r for r in snapshot.get('dwd', [])}
    indexed = defaultdict(list)
    for row in snapshot['ods']:
        if row.get('source') == 'akshare':
            indexed[row['stock_code_raw'], row['trade_date']].append(row)
    for stock in snapshot['stocks']:
        if stock['market'] not in ('A', 'HK'):
            continue
        code, listed, end = stock['stock_code'], stock.get('listed_date'), stock.get('delisted_date')
        if listed is None or (not stock.get('is_listed', True) and end is None):
            references.append({'code': code, 'reason': 'listing_dates_unknown'})
            continue
        for day in days(max(first, listed), min(last, end or last)):
            opened = snapshot['calendar'].get((stock['market'], day))
            if opened is None:
                continue
            if not opened or (code, day) in snapshot['suspended']:
                continue
            expected[code].add(day)
            rows = indexed[code, day]
            if (len(rows) != 1 or not valid(rows[0]) or
                    rows[0].get('stock_id', stock['id']) not in (None,stock['id']) or
                    rows[0].get('market_raw',stock['market']) != stock['market']):
                missing[code].add(day)
            elif accepted.get(key(code, day)) != price_hash(rows[0]):
                unverified[code].add(day)
            elif ((code, day) not in dwd_index or price_hash(dwd_index[code, day]) != price_hash(rows[0]) or
                  dwd_index[code,day].get('stock_id',stock['id']) != stock['id'] or
                  dwd_index[code,day].get('is_valid_ohlc',1)!=1):
                publish_pending[code].add(day)
    # Existing downstream records on excluded sessions must be reviewed, never deleted.
    stock_index = {s['stock_code']:s for s in snapshot['stocks'] if s['market'] in ('A','HK')}
    seen_dwd = set()
    for row in snapshot.get('dwd', []):
        code, day = row['stock_code'], row['trade_date']
        stock = stock_index.get(code)
        excluded = (stock is None or (code,day) in snapshot['suspended'] or
                    (stock.get('listed_date') is not None and day < stock['listed_date']) or
                    (stock.get('delisted_date') is not None and day > stock['delisted_date']) or
                    snapshot['calendar'].get((stock['market'],day)) is False) if stock else True
        if (code,day) in seen_dwd:
            conflicts.append({'code':code,'date':day,'reason':'duplicate_dwd_key'})
        seen_dwd.add((code,day))
        if first <= day <= last and excluded:
            if (code,day) in snapshot['suspended'] and row.get('is_valid_ohlc')==0:
                retained_invalid.append({'code':code,'date':day,'reason':'quarantined_suspension_snapshot'})
            else:
                conflicts.append({'code': code, 'date': day, 'reason': 'excluded_dwd_key'})
    return dict(expected=dict(expected), missing=dict(missing), unverified=dict(unverified),
                references=references, conflicts=conflicts, retained_invalid=retained_invalid,
                publish_pending=dict(publish_pending),
                complete=not (missing or unverified or references or conflicts or publish_pending))


def calendar_index(records):
    result = {}
    for market, day, opened in records:
        if (market,day) in result:
            raise ValueError('Duplicate calendar reference')
        if opened not in (0,1,None):
            raise ValueError('Invalid calendar flag')
        result[market,day] = None if opened is None else bool(opened)
    return result


def near(a, b, absolute):
    a, b = decimal(a), decimal(b)
    return abs(a - b) <= max(Decimal(absolute), max(abs(a), abs(b)) * Decimal('0.000001'))


def reconcile(vendors, requested):
    """All responding independent vendors must agree; no majority override."""
    indexed = {}
    for vendor, rows in vendors.items():
        if vendor not in ('em', 'tx', 'sina', 'ths'):
            raise ValueError('Unknown independent vendor')
        table = {}
        for row in rows:
            day = row['trade_date']
            if day in table:
                raise ValueError('Duplicate source date')
            table[day] = row
        indexed[vendor] = table
    accepted, issues = [], {}
    for day in sorted(requested):
        evidence = [(vendor, rows[day]) for vendor, rows in indexed.items() if day in rows]
        candidates = [(vendor,row) for vendor,row in evidence if row.get('_price_eligible',True)]
        if len(candidates) < 2:
            issues[day] = 'waiting_source'
            continue
        if (any(not valid(row) for _,row in evidence) or
                any(row.get('volume') == 0 and len({row[k] for k in FIELDS[:4]}) == 1 for _,row in candidates)):
            issues[day] = 'invalid_source'
            continue
        base = candidates[0][1]
        if any(not near(left[k],right[k],'0.0002')
               for (_,left),(_,right) in combinations(candidates,2) for k in FIELDS[:4]):
            issues[day] = 'source_conflict'
            continue
        result = dict(base)
        result.pop('_price_eligible',None)
        result['field_vendors'] = {}
        conflict = False
        for field in ('volume', 'amount'):
            supporters = [(vendor,row[field]) for vendor,row in evidence if row.get(field) is not None]
            values = [value for _,value in supporters]
            if len(values) < 2:
                result[field] = None
            elif any((decimal(left) != decimal(right) if field == 'volume'
                      else not near(left,right,'0.01')) for left,right in combinations(values,2)):
                conflict = True
            else:
                result[field] = values[0]
                result['field_vendors'][field] = [vendor for vendor,_ in supporters]
        if conflict:
            issues[day] = 'source_conflict'
            continue
        for field in FIELDS[:4]:
            result[field] = decimal(result[field]).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        result['pct_change'] = None  # Recomputed from the accepted same-basis series.
        result['vendors'] = [vendor for vendor, _ in candidates]
        accepted.append(result)
    return accepted, issues


def load_snapshot(first, last):
    from sqlalchemy import text
    from base_infra.db import engine
    params = {'first': first, 'last': last}
    with engine.connect() as conn:
        conn.execute(text('START TRANSACTION READ ONLY'))
        def query(sql):
            return [dict(row) for row in conn.execute(text(sql), params).mappings()]
        stocks = query("SELECT * FROM dim_stock WHERE market IN ('A','HK')")
        ods = query("SELECT * FROM ods_stock_daily WHERE market_raw IN ('A','HK') AND trade_date BETWEEN :first AND :last")
        dwd = query("SELECT * FROM dwd_stock_daily WHERE market IN ('A','HK') AND trade_date BETWEEN :first AND :last")
        calendar_rows = []
        for market, table, identifier in [('A', 'dim_trade_calendar', 'SSE'), ('HK', 'etl_market_calendar', 'HKEX')]:
            for row in query(f"SELECT trade_date,is_open FROM {table} WHERE market='{identifier}' AND trade_date BETWEEN :first AND :last"):
                calendar_rows.append((market,row['trade_date'],row['is_open']))
        calendar = calendar_index(calendar_rows)
        suspended = {(r['stock_code'], r['trade_date']) for r in query(
            'SELECT stock_code,trade_date FROM etl_stock_suspension WHERE trade_date BETWEEN :first AND :last')}
        conn.rollback()
    return dict(stocks=stocks, ods=ods, dwd=dwd, calendar=calendar, suspended=suspended)


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, value):
    def serializable(item):
        if isinstance(item, dict):
            return {str(k): serializable(v) for k, v in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [serializable(v) for v in (sorted(item) if isinstance(item, set) else item)]
        return item
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(serializable(value), default=str,
                                    ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def audit(first=START, last=None, directory=ROOT):
    last = last or datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=1)
    if first < START or first > last:
        raise ValueError('Invalid history range')
    snapshot = load_snapshot(first, last)
    manifest = read_json(directory / 'active.json', {})
    result = audit_snapshot(snapshot, first, last, manifest.get('hashes', {}) if manifest.get('basis')==BASIS else {})
    save_json(directory / 'audit.json', result)
    return snapshot, result, manifest


def summarize(report):
    return {**{name: sum(map(len, report.get(name, {}).values()))
               for name in ('expected','missing','unverified','publish_pending')},
            'references':len(report.get('references', [])), 'conflicts':len(report.get('conflicts', [])),
            'retained_invalid':len(report.get('retained_invalid', [])),
            'complete': report.get('complete', False), 'detail':'logs/etl/stock_history/audit.json'}


def publish(snapshot, payload, directory, *, rebase, evidence_dir=None, factor_snapshots=None):
    """One MySQL transaction for ODS and DWD; staged evidence enables recovery."""
    from sqlalchemy import text
    from base_infra.db import engine
    from pipelines.staging.clean_stock_daily_dwd import publish_stock_day
    stocks = {r['stock_code']: r for r in snapshot['stocks']}
    existing = {(r['stock_code_raw'], r['trade_date']): r for r in snapshot['ods'] if r['source'] == 'akshare'}
    existing_dwd = {(r['stock_code'], r['trade_date']): r for r in snapshot['dwd']}
    rows, expected_by_day = [], defaultdict(set)
    for code, series in payload.items():
        previous = None
        # The caller supplies the complete verified series, including prior closes.
        for raw in sorted(series, key=lambda r: r['trade_date']):
            row = dict(raw)
            row['pct_change'] = ((decimal(row['close']) / previous - 1) * 100).quantize(Decimal('0.0001')) if previous else None
            previous = decimal(row['close'])
            old = existing.get((code, row['trade_date']), {})
            for field in ('volume', 'amount'):
                if row.get(field) is None:
                    row[field] = old.get(field)
            stock = stocks[code]
            row.update(stock_id=stock['id'], stock_code_raw=code, stock_name_raw=stock['stock_name'],
                       market_raw=stock['market'], exchange_raw=stock['exchange'], source_symbol=code,
                       source='akshare', fetch_time=datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None))
            if not valid(row) or (row['pct_change'] is not None and abs(row['pct_change']) > Decimal('9999.9999')):
                raise ValueError('Published values exceed formal schema')
            rows.append(row)
            expected_by_day[row['trade_date']].add(code)
    previous_evidence = read_json(directory/'active.json',{}).get('evidence',{})
    evidence = {key(r['stock_code_raw'],r['trade_date']):
                ({'vendors':r['vendors'],'field_vendors':r.get('field_vendors',{}),
                  'factor_version':r.get('factor_version'),'raw_hash':r.get('raw_hash'),
                  'run':str(evidence_dir)} if r.get('vendors') else
                 previous_evidence.get(key(r['stock_code_raw'],r['trade_date']),{})) for r in rows}
    prepared = dict(adjust='hfq', basis=BASIS, hashes={key(r['stock_code_raw'], r['trade_date']): price_hash(r) for r in rows},
                    evidence=evidence, rows=rows, rebase=rebase,
                    factors=factor_snapshots if factor_snapshots is not None else
                    read_json(directory/'active.json',{}).get('factors',{}))
    save_json(directory / 'prepared.json', prepared)
    def same_mapping(row, other, *, dwd=False):
        names = ('stock_id','market','exchange') if dwd else ('stock_id','market_raw','exchange_raw')
        return (all(other.get(name)==row.get({'market':'market_raw','exchange':'exchange_raw'}.get(name,name)) for name in names)
                and (not dwd or other.get('is_valid_ohlc',1)==1))
    changed = [r for r in rows if price_hash(r) != price_hash(existing.get((r['stock_code_raw'],r['trade_date']),{})) or
               not same_mapping(r, existing.get((r['stock_code_raw'],r['trade_date']),{}))]
    changed_keys = {(r['stock_code_raw'],r['trade_date']) for r in changed}
    touched = {r['trade_date'] for r in rows if (r['stock_code_raw'],r['trade_date']) in changed_keys or
               price_hash(r) != price_hash(existing_dwd.get((r['stock_code_raw'],r['trade_date']),{})) or
               not same_mapping(r,existing_dwd.get((r['stock_code_raw'],r['trade_date']),{}),dwd=True)}
    if not touched:
        save_json(directory / 'active.json', {k:v for k,v in prepared.items() if k!='rows'})
        return {'added':0,'corrected':0}
    columns = ('stock_id', 'stock_code_raw', 'stock_name_raw', 'market_raw', 'exchange_raw', 'source_symbol',
               'trade_date', *FIELDS, 'source', 'fetch_time')
    names = ','.join(f'`{c}`' for c in columns)
    bindings = ','.join(':'+c for c in columns)
    updates = ','.join(f'`{c}`=VALUES(`{c}`)' for c in columns if c not in ('stock_code_raw', 'trade_date', 'source'))
    with engine.begin() as conn:
        for offset in range(0, len(changed), 500):
            conn.execute(text(f'INSERT INTO ods_stock_daily ({names}) VALUES ({bindings}) ON DUPLICATE KEY UPDATE {updates}'),
                         [{c: r[c] for c in columns} for r in changed[offset:offset+500]])
        for day, expected in sorted(expected_by_day.items()):
            if day in touched:
                for code in expected:
                    old = existing_dwd.get((code,day))
                    if old and old['stock_id'] != stocks[code]['id']:
                        conn.execute(text('UPDATE dwd_stock_daily SET stock_id=:stock_id '
                                          'WHERE stock_code=:code AND trade_date=:day AND stock_id=:old_id'),
                                     {'stock_id':stocks[code]['id'],'code':code,'day':day,'old_id':old['stock_id']})
                publish_stock_day(day, expected, connection=conn, calculate_returns=False)
        observed = {(r['stock_code_raw'], r['trade_date']): dict(r) for r in conn.execute(text(
            "SELECT * FROM ods_stock_daily WHERE source='akshare' AND market_raw IN ('A','HK') "
            'AND trade_date BETWEEN :first AND :last'),
            {'first': min(expected_by_day), 'last': max(expected_by_day)}).mappings()}
        if any(price_hash(observed.get((r['stock_code_raw'], r['trade_date']), {})) != price_hash(r) or
               not same_mapping(r,observed.get((r['stock_code_raw'],r['trade_date']),{})) for r in rows):
            raise RuntimeError('ODS readback mismatch; transaction rolled back')
    save_json(directory / 'active.json', {k: v for k, v in prepared.items() if k != 'rows'})
    added=sum((r['stock_code_raw'],r['trade_date']) not in existing for r in changed)
    return {'added':added,'corrected':len(changed)-added}


def recover(snapshot, directory):
    """A crash after MySQL commit cannot turn verified HFQ data into legacy data."""
    prepared = read_json(directory / 'prepared.json', {})
    if prepared.get('basis')!=BASIS or not prepared.get('hashes') or not prepared.get('rows'):
        return
    ods = {key(r['stock_code_raw'], r['trade_date']): price_hash(r) for r in snapshot['ods'] if r['source']=='akshare'}
    dwd = {key(r['stock_code'], r['trade_date']): price_hash(r) for r in snapshot['dwd']}
    ods_rows = {key(r['stock_code_raw'],r['trade_date']):r for r in snapshot['ods'] if r['source']=='akshare'}
    dwd_rows = {key(r['stock_code'],r['trade_date']):r for r in snapshot['dwd']}
    mappings_match = all(ods_rows.get(key(r['stock_code_raw'],date.fromisoformat(r['trade_date'])),{}).get('stock_id')==r['stock_id'] and
                         dwd_rows.get(key(r['stock_code_raw'],date.fromisoformat(r['trade_date'])),{}).get('stock_id')==r['stock_id'] and
                         dwd_rows.get(key(r['stock_code_raw'],date.fromisoformat(r['trade_date'])),{}).get('is_valid_ohlc',1)==1
                         for r in prepared['rows'])
    if mappings_match and all(ods.get(k) == value and dwd.get(k) == value for k, value in prepared['hashes'].items()):
        save_json(directory / 'active.json', {k: v for k, v in prepared.items() if k != 'rows'})


def run(*, last, rebase=False, requested=None, directory=ROOT, state=None):
    from scheduler.status import BatchStatus
    from scheduler.workflow import State
    from scheduler.stock_hfq_cache import prepare
    state = state or State()
    with state.lock('load_stock_daily'), state.lock('warehouse'):
        snapshot, report, manifest = audit(START, last, directory)
        recover(snapshot, directory)
        manifest = read_json(directory / 'active.json', {})
        report = audit_snapshot(snapshot, START, last, manifest.get('hashes', {}) if manifest.get('basis')==BASIS else {})
        if report['references'] or report['conflicts']:
            save_json(directory / 'result.json', report)
            state.save('load_stock_daily', last.isoformat(), 'waiting_reference', message='历史参考数据不足或下游排除项冲突，详见 stock_history/result.json')
            state.service('load_stock_daily', 'incomplete')
            logger.error('Stock history blocked: %s',summarize(report))
            return False
        if not rebase and (manifest.get('adjust') != 'hfq' or manifest.get('basis')!=BASIS):
            state.save('load_stock_daily', last.isoformat(), BatchStatus.WAITING_REBASE,
                       message='须先完成新浪单源后复权初始化切换')
            return False
        if not report['expected']:
            state.save('load_stock_daily', last.isoformat(), BatchStatus.CLOSED,
                       message='无有效存续期内应采行情')
            state.service('load_stock_daily', 'ok')
            return True
        # Only fetch symbols with gaps/unaccepted rows, recent revisions, or weekly audit.
        # Full series for selected symbols makes previous-trade returns unambiguous.
        wanted = set(report['expected']) if rebase or last.weekday() == 6 else set(report['missing']) | set(report['unverified']) | set(report['publish_pending'])
        wanted |= {code for code, ds in report['expected'].items() if any(d >= last-timedelta(days=10) for d in sorted(ds)[-5:])}
        if requested is not None:
            if set(requested) - {s['stock_code'] for s in snapshot['stocks']}:
                raise ValueError('Repair manifest includes out-of-scope stocks')
            requested = {code: set(ds) & report['expected'].get(code,set()) for code,ds in requested.items()}
            requested = {code:ds for code,ds in requested.items() if ds}
            wanted |= set(requested)
        run_dir = directory / 'runs' / (datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8])
        payload, issues = {}, {}
        factor_snapshots=dict(manifest.get('factors',{})) if manifest.get('basis')==BASIS else {}
        processed_days=set()
        factor_affected=raw_changed=raw_fetches=0
        stocks = {r['stock_code']: r for r in snapshot['stocks']}
        for code in sorted(wanted):
            expected = report['expected'][code]
            needed = (expected if rebase or last.weekday()==6 else
                      report['missing'].get(code,set()) | report['unverified'].get(code,set()) |
                      set(sorted(expected)[-5:]) | (requested or {}).get(code,set()))
            if not needed <= expected:
                raise ValueError('Repair dates include a closed, suspended, or out-of-lifecycle session')
            try:
                prepared=prepare(stocks[code],expected,needed,run_dir,directory/'cache',
                                 factor_snapshots.get(code),review=rebase or last.weekday()==6,
                                 previous_raw_hashes={day:manifest.get('evidence',{}).get(key(code,day),{}).get('raw_hash')
                                                      for day in expected})
                accepted,problems=prepared.rows,prepared.issues
                factor_snapshots[code]=prepared.factors
                factor_affected+=len(prepared.affected)
                raw_changed+=len(prepared.raw_changed)
                raw_fetches+=int(prepared.fetched_raw)
                errors={}
            except Exception as exc:
                accepted=[]
                problems={day:'waiting_source' for day in needed}
                errors={'factor_or_source':str(exc)}
            processed_days.update(r['trade_date'] for r in accepted)
            if problems:
                issues[code] = {'dates': problems, 'errors': errors}
            merged = {r['trade_date']:r for r in snapshot['ods'] if r['stock_code_raw']==code and
                      r['trade_date'] in expected and r['source']=='akshare' and
                      manifest.get('hashes',{}).get(key(code,r['trade_date']))==price_hash(r)}
            merged.update({r['trade_date']:r for r in accepted})
            payload[code] = list(merged.values())
            if set(merged) != expected and not problems:
                issues[code] = {'reason':'incomplete_verified_series'}
        save_json(run_dir / 'validation.json', {'issues': issues, 'payload': payload})
        if issues:
            state.save('load_stock_daily', last.isoformat(), BatchStatus.WAITING_SOURCE,
                       message='原始行情或后复权因子证据不足；详见 stock_history/runs')
            state.service('load_stock_daily', 'incomplete')
            reasons=[reason for detail in issues.values() for reason in detail.get('dates',{}).values()]
            result={'complete':False,'issues':issues,'waiting_source':reasons.count('waiting_source'),
                    'source_conflict':reasons.count('source_conflict'),'invalid_source':reasons.count('invalid_source')}
            save_json(directory / 'result.json', result)
            logger.error('Stock single-source HFQ validation failed: %s', {k:v for k,v in result.items() if k!='issues'})
            return False
        # Unselected verified stocks are included to publish complete daily groups.
        if not rebase:
            for code, expected in report['expected'].items():
                if code not in payload:
                    payload[code] = [r for r in snapshot['ods'] if r['stock_code_raw']==code and r['trade_date'] in expected and r['source']=='akshare']
        stats={'added':0,'corrected':0}
        if payload:
            try:
                stats=publish(snapshot, payload, directory, rebase=rebase, evidence_dir=run_dir,
                              factor_snapshots=factor_snapshots)
            except Exception as exc:
                save_json(directory/'result.json', {'complete':False,'publication_errors':1,'reason':str(exc)})
                state.save('load_stock_daily', last.isoformat(), BatchStatus.PUBLISH_ERROR,
                           message=type(exc).__name__)
                raise
        _, final, _ = audit(START, last, directory)
        stats.update(factor_affected=factor_affected,raw_changed=raw_changed,raw_fetches=raw_fetches)
        save_json(directory / 'result.json', {**final,'stats':stats})
        logger.info('Stock history readback: %s', {**summarize(final),**stats})
        if not final['complete']:
            state.service('load_stock_daily', 'incomplete')
            return False
        for day in sorted(processed_days):
            state.save('load_stock_daily', day.isoformat(), BatchStatus.PUBLISHED,
                       message='新浪原价/因子证据及 ODS/DWD 回读验收')
        state.service('load_stock_daily', 'ok')
        state.export()
        return True
