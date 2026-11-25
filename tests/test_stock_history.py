from datetime import date
from decimal import Decimal as D
import pytest

from scheduler.stock_history import audit_snapshot, reconcile, price_hash

DAY = date(2026, 8, 13)

def bar(day=DAY, close='10', volume=100):
    return dict(trade_date=day, open=D('10'), high=D('11'), low=D('9'), close=D(close), volume=volume, amount=D('1000'), pct_change=None)

def snapshot():
    return dict(stocks=[dict(stock_code='002594.SZ',stock_name='Test',market='A',id=1,exchange='SZ',currency='CNY',is_listed=1,listed_date=DAY,delisted_date=None)], calendar={('A',DAY):True},suspended=set(),ods=[],dwd=[])

def test_audit_finds_gap_without_local_task_history():
    s=snapshot()
    result=audit_snapshot(s,DAY,DAY,{})
    assert result['missing']=={'002594.SZ':{DAY}}
    assert not result['references']

def test_missing_listing_and_calendar_never_infer_complete():
    s=snapshot();s['stocks'][0]['listed_date']=None;s['calendar']={}
    result=audit_snapshot(s,DAY,DAY,{})
    assert result['references']
    assert not result['complete']

def test_valid_but_unaccepted_row_is_pending_and_tamper_is_detected():
    s=snapshot();row={**bar(), 'stock_code_raw':'002594.SZ','source':'akshare'};s['ods']=[row]
    assert audit_snapshot(s,DAY,DAY,{})['unverified']=={'002594.SZ':{DAY}}
    accepted={'002594.SZ|2026-08-13':price_hash(row)}
    s['dwd']=[{**row,'stock_code':'002594.SZ'}]
    assert audit_snapshot(s,DAY,DAY,accepted)['complete']
    row['close']=D('10.5')
    assert audit_snapshot(s,DAY,DAY,accepted)['unverified']

def test_suspension_snapshots_excluded_and_us_excluded():
    s=snapshot();s['suspended']={('002594.SZ',DAY)};s['stocks'].append(dict(stock_code='LI.US',market='US'))
    assert audit_snapshot(s,DAY,DAY,{})['complete']


def test_confirmed_suspension_dwd_snapshot_must_be_explicitly_quarantined():
    s=snapshot();s['suspended']={('002594.SZ',DAY)}
    row={**bar(),'stock_code':'002594.SZ','is_valid_ohlc':1};s['dwd']=[row]
    assert audit_snapshot(s,DAY,DAY,{})['conflicts']
    row['is_valid_ohlc']=0
    report=audit_snapshot(s,DAY,DAY,{})
    assert report['complete'] and len(report['retained_invalid'])==1

def test_two_vendors_required_and_no_average_on_conflict():
    accepted,issues=reconcile({'em':[bar()]},{DAY})
    assert not accepted and issues[DAY]=='waiting_source'
    accepted,issues=reconcile({'em':[bar()],'tx':[bar(close='10.1')]},{DAY})
    assert not accepted and issues[DAY]=='source_conflict'
    accepted,issues=reconcile({'em':[bar()],'tx':[bar()]},{DAY})
    assert accepted[0]['close']==D('10') and not issues

def test_volume_conflict_and_single_source_optionals_are_not_copied():
    accepted,issues=reconcile({'em':[bar()],'tx':[bar(volume=200)]},{DAY})
    assert not accepted and issues[DAY]=='source_conflict'
    other={**bar(),'volume':None,'amount':None}
    accepted,_=reconcile({'em':[bar()],'tx':[other]},{DAY})
    assert accepted[0]['volume'] is None and accepted[0]['amount'] is None

def test_zero_volume_flat_is_not_a_trade():
    flat={**bar(volume=0),'open':D('10'),'high':D('10'),'low':D('10')}
    accepted,issues=reconcile({'em':[flat],'sina':[flat]},{DAY})
    assert not accepted

def test_source_units_and_identical_price_days_are_preserved():
    from pandas import DataFrame
    from scheduler.stock_history_sources import normalize
    frame=DataFrame([{'date':'2026-08-13','open':10,'high':11,'low':9,'close':10,'volume':3},
                     {'date':'2026-08-14','open':10,'high':11,'low':9,'close':10,'volume':3}])
    rows=normalize(frame,'em','A')
    assert len(rows)==2 and rows[0]['volume']==300


def test_silent_unadjusted_response_and_missing_factor_are_rejected():
    from pandas import DataFrame
    from scheduler.stock_history_sources import verify_adjustment, factor_prices
    factors=DataFrame([{'date':'2010-01-01','hfq_factor':'2'}])
    with pytest.raises(ValueError,match='HFQ response'):
        verify_adjustment([bar()],[bar()],factors,'A')
    with pytest.raises(ValueError,match='Missing HFQ'):
        factor_prices([bar()],DataFrame(),'A')
    assert factor_prices([bar()],factors,'A')[0]['close']==D('20')


def test_hk_cash_adjustment_does_not_change_volume_or_amount():
    from pandas import DataFrame
    from scheduler.stock_history_sources import factor_prices
    factors=DataFrame([{'date':'2010-01-01','hfq_factor':'2','cash':'1'}])
    row=factor_prices([bar()],factors,'HK')[0]
    assert row['close']==D('21') and row['volume']==100 and row['amount']==D('1000')


def test_missing_dwd_never_passes_even_if_ods_manifest_matches():
    s=snapshot();row={**bar(),'stock_code_raw':'002594.SZ','source':'akshare'};s['ods']=[row]
    result=audit_snapshot(s,DAY,DAY,{'002594.SZ|2026-08-13':price_hash(row)})
    assert not result['complete'] and result['publish_pending']


def test_unverified_single_source_never_writes(tmp_path,monkeypatch):
    from scheduler import stock_history as h,stock_hfq_cache as cache
    from scheduler.workflow import State
    s=snapshot()
    monkeypatch.setattr(h,'load_snapshot',lambda *_:s)
    monkeypatch.setattr(cache,'prepare',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('Missing factors')))
    monkeypatch.setattr(h,'publish',lambda *args,**kwargs:pytest.fail('missing factor evidence reached publication'))
    state=State(tmp_path/'state')
    assert not h.run(last=DAY,rebase=True,directory=tmp_path/'history',state=state)
    assert state.batch('load_stock_daily',str(DAY))['status']=='waiting_source'


def test_daily_requires_rebase_before_any_source_or_write(tmp_path,monkeypatch):
    from scheduler import stock_history as h, stock_history_sources as sources
    from scheduler.workflow import State
    monkeypatch.setattr(h,'load_snapshot',lambda *_:snapshot())
    monkeypatch.setattr(sources,'collect',lambda *args,**kwargs:pytest.fail('legacy prices mixed with HFQ'))
    state=State(tmp_path/'state')
    assert not h.run(last=DAY,directory=tmp_path/'history',state=state)
    assert state.batch('load_stock_daily',str(DAY))['status']=='waiting_rebase'


@pytest.mark.parametrize('has_previous',[False,True])
def test_atomic_publish_rolls_back_ods_when_dwd_fails(tmp_path,monkeypatch,has_previous):
    from contextlib import contextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from scheduler import stock_history as h
    from base_infra import db
    from pipelines.staging import clean_stock_daily_dwd as dwd
    stored=[]
    class Connection:
        def execute(self,sql,params=None):
            if str(sql).startswith('INSERT'):
                stored.extend(deepcopy(params))
            return SimpleNamespace(mappings=lambda:stored)
    @contextmanager
    def transaction():
        previous=deepcopy(stored)
        try:yield Connection()
        except BaseException:
            stored[:]=previous
            raise
    monkeypatch.setattr(db,'engine',SimpleNamespace(begin=transaction))
    monkeypatch.setattr(dwd,'publish_stock_day',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('DWD failed')))
    old={'basis':h.BASIS,'factors':{'002594.SZ':[{'date':'1900-01-01','hfq_factor':'1'}]}}
    new={'002594.SZ':[{'date':'1900-01-01','hfq_factor':'2'}]}
    if has_previous:h.save_json(tmp_path/'active.json',old)
    with pytest.raises(RuntimeError,match='DWD failed'):
        h.publish(snapshot(),{'002594.SZ':[bar()]},tmp_path,rebase=True,factor_snapshots=new)
    assert not stored
    if has_previous:assert h.read_json(tmp_path/'active.json',{})==old
    else:assert not (tmp_path/'active.json').exists()
    assert (tmp_path/'prepared.json').exists()
    assert h.read_json(tmp_path/'prepared.json',{})['factors']==new


def test_commit_recovery_requires_both_layers(tmp_path):
    from scheduler.stock_history import recover,save_json,BASIS
    row={**bar(),'stock_code_raw':'002594.SZ','stock_code':'002594.SZ','source':'akshare','stock_id':1}
    save_json(tmp_path/'prepared.json',{'adjust':'hfq','basis':BASIS,'hashes':{'002594.SZ|2026-08-13':price_hash(row)},'rows':[row]})
    s=snapshot();s['ods']=[row]
    recover(s,tmp_path)
    assert not (tmp_path/'active.json').exists()
    s['dwd']=[row];recover(s,tmp_path)
    assert (tmp_path/'active.json').exists()


def test_stock_process_market_dispatches_strict_history(monkeypatch,tmp_path):
    from scheduler import market_workflow as market,stock_history as h
    from scheduler.workflow import State
    calls=[]
    monkeypatch.setattr(h,'run',lambda **kwargs:calls.append(kwargs) or False)
    assert not market.process_market(State(tmp_path),market.STOCK,DAY,catch_up=True)
    assert calls[0]['last']==DAY

@pytest.mark.parametrize('mismatch',[None,'ods','dwd','ods_mapping','dwd_mapping','dwd_validity','optionals'])
def test_real_dwd_publisher_shares_transaction_and_readbacks(tmp_path,monkeypatch,mismatch):
    from contextlib import contextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from scheduler import stock_history as h
    from base_infra import db
    stock=snapshot()['stocks'][0]
    store={'ods':[],'dwd':[]}
    baseline=snapshot()
    sample={**bar(),'stock_code_raw':'002594.SZ','stock_code':'002594.SZ','source':'akshare',
            'stock_id':1,'market_raw':'A','market':'A','exchange_raw':'SZ','exchange':'SZ'}
    proposed=bar()
    if mismatch=='ods_mapping':
        baseline['ods']=[{**sample,'stock_id':99}];store['ods']=deepcopy(baseline['ods'])
    if mismatch=='dwd_mapping':
        baseline['dwd']=[{**sample,'stock_id':99}];store['dwd']=deepcopy(baseline['dwd'])
    if mismatch=='dwd_validity':
        baseline['ods']=[dict(sample)];store['ods']=deepcopy(baseline['ods'])
        baseline['dwd']=[{**sample,'is_valid_ohlc':0}];store['dwd']=deepcopy(baseline['dwd'])
    if mismatch=='optionals':
        baseline['ods']=[{**sample,'volume':123}];store['ods']=deepcopy(baseline['ods'])
        proposed={**bar(),'volume':None,'amount':None}
    class Result:
        def __init__(self,rows):self.rows=rows
        def mappings(self):return self
        def all(self):return self.rows
        def __iter__(self):return iter(self.rows)
    class Connection:
        def execute(self,statement,params=None):
            sql=str(statement).strip()
            if sql.startswith('INSERT INTO ods_stock_daily'):
                store['ods']=deepcopy(params)
                return Result([])
            if sql.startswith('UPDATE dwd_stock_daily SET stock_id='):
                store['dwd'][0]['stock_id']=params['stock_id']
                return Result([])
            if sql.startswith('INSERT INTO dwd_stock_daily') or sql.startswith('UPDATE dwd_stock_daily SET `'):
                store['dwd']=deepcopy(params)
                if mismatch=='dwd':store['dwd'][0]['close']=D('99')
                return Result([])
            if 'LEFT JOIN dim_stock' in sql:
                return Result([{**r,'ods_stock_id':r['stock_id'],'stock_code':r['stock_code_raw'],
                    'stock_name':stock['stock_name'],'market':'A','exchange':'SZ','currency':'CNY'} for r in store['ods']])
            if 'FROM dwd_stock_daily' in sql:
                return Result(deepcopy(store['dwd']))
            if 'FROM ods_stock_daily' in sql:
                rows=deepcopy(store['ods'])
                if mismatch=='ods' and rows:rows[0]['close']=D('99')
                return Result(rows)
            raise AssertionError(sql)
    @contextmanager
    def begin():
        saved=deepcopy(store)
        try:yield Connection()
        except BaseException:
            store.clear();store.update(saved)
            raise
    monkeypatch.setattr(db,'engine',SimpleNamespace(begin=begin))
    if mismatch in ('ods','dwd'):
        with pytest.raises(RuntimeError):
            h.publish(snapshot(),{'002594.SZ':[bar()]},tmp_path,rebase=True)
        assert store=={'ods':[],'dwd':[]} and not (tmp_path/'active.json').exists()
    else:
        h.publish(baseline,{'002594.SZ':[proposed]},tmp_path,rebase=True)
        assert store['ods'][0]['stock_id']==store['dwd'][0]['stock_id']==1
        if mismatch=='optionals':
            assert store['ods'][0]['volume']==123 and store['ods'][0]['amount']==D('1000')
        assert store['ods'][0]['close']==store['dwd'][0]['close']==D('10')
        if mismatch=='dwd_validity':assert store['dwd'][0]['is_valid_ohlc']==1
        assert (tmp_path/'active.json').exists()


def test_null_and_duplicate_calendar_references_do_not_become_closed():
    from scheduler.stock_history import calendar_index
    cal=calendar_index([('A',DAY,None)])
    assert cal['A',DAY] is None
    s=snapshot();s['calendar']=cal
    assert audit_snapshot(s,DAY,DAY,{})['references']
    with pytest.raises(ValueError,match='Duplicate calendar'):
        calendar_index([('A',DAY,1),('A',DAY,0)])


def test_unknown_listing_does_not_falsely_call_all_existing_dwd_excluded():
    s=snapshot();s['stocks'][0]['listed_date']=None
    s['dwd']=[{**bar(),'stock_code':'002594.SZ'}]
    report=audit_snapshot(s,DAY,DAY,{})
    assert report['references'] and not report['conflicts']


def test_rebase_is_all_or_nothing_for_multiple_stocks(tmp_path,monkeypatch):
    from scheduler import stock_history as h,stock_hfq_cache as cache
    from scheduler.workflow import State
    s=snapshot();s['stocks'].append({**s['stocks'][0],'stock_code':'000625.SZ','id':2})
    monkeypatch.setattr(h,'load_snapshot',lambda *_:s)
    monkeypatch.setattr(cache,'prepare',lambda stock,*args,**kwargs:
                        cache.Preparation([bar()] if stock['id']==1 else [],{} if stock['id']==1 else {DAY:'waiting_source'},
                                          [{'date':'1900-01-01','hfq_factor':'1'}],set(),set(),True,'version'))
    monkeypatch.setattr(h,'publish',lambda *args,**kwargs:pytest.fail('partial global rebase'))
    assert not h.run(last=DAY,rebase=True,directory=tmp_path/'history',state=State(tmp_path/'state'))


def test_single_source_with_factor_evidence_can_complete_global_rebase(tmp_path,monkeypatch):
    from scheduler import stock_history as h,stock_hfq_cache as cache
    from scheduler.workflow import State
    s=snapshot();calls=[]
    monkeypatch.setattr(h,'load_snapshot',lambda *_:s)
    factor=[{'date':'1900-01-01','hfq_factor':'1'}]
    monkeypatch.setattr(cache,'prepare',lambda *args,**kwargs:cache.Preparation(
        [{**bar(),'vendors':['sina'],'factor_version':'version'}],{},factor,{DAY},set(),True,'version'))
    def publish(snap,payload,directory,**kwargs):
        calls.append(kwargs)
        row={**payload['002594.SZ'][0],'stock_code_raw':'002594.SZ','source':'akshare','stock_id':1}
        s['ods']=[row];s['dwd']=[{**row,'stock_code':'002594.SZ'}]
        h.save_json(directory/'active.json',{'basis':h.BASIS,'adjust':'hfq','hashes':{h.key('002594.SZ',DAY):price_hash(row)},
                                           'factors':kwargs['factor_snapshots']})
        return {'added':1,'corrected':0}
    monkeypatch.setattr(h,'publish',publish)
    assert h.run(last=DAY,rebase=True,directory=tmp_path/'history',state=State(tmp_path/'state'))
    assert calls[0]['factor_snapshots']=={'002594.SZ':factor}


def test_history_cli_accepts_explicit_completed_business_date(monkeypatch):
    from scheduler import runner,stock_history as h
    observed=[]
    monkeypatch.setattr(h,'run',lambda **kwargs:observed.append(kwargs) or True)
    assert runner.main(['--rebase-stock-history','--adjust','hfq','--end-date','2026-11-23'])==0
    assert observed[0]['last']==date(2026,11,23)
    with pytest.raises(SystemExit):
        runner.main(['--rebase-stock-history','--adjust','hfq','--end-date','2999-01-01'])


def test_source_key_joins_are_safe_with_different_mysql_collations():
    from pipelines.staging.clean_stock_daily_dwd import _SOURCE_SQL
    sql=str(_SOURCE_SQL)
    # Key bytes are canonical ASCII; comparisons must not inherit conflicting
    # utf8mb4_0900_ai_ci / utf8mb4_unicode_ci column collations.
    assert 'CAST(ds.stock_code AS BINARY) = CAST(ods.stock_code_raw AS BINARY)' in sql
    assert 'CAST(suspended.stock_code AS BINARY) = CAST(ods.stock_code_raw AS BINARY)' in sql


def test_dwd_publication_retains_explicitly_quarantined_keys_without_counting_them():
    from types import SimpleNamespace
    from pipelines.staging.clean_stock_daily_dwd import _publish_rows
    stored=[{'stock_id':1,'trade_date':DAY,'close':D('10'),'is_valid_ohlc':1},
            {'stock_id':2,'trade_date':DAY,'close':D('7.3'),'is_valid_ohlc':0}]
    writes=[]
    class Connection:
        def execute(self,sql,params):
            if str(sql).strip().startswith('UPDATE'):
                writes.extend(params)
                for incoming in params:
                    for row in stored:
                        if row['stock_id']==incoming['stock_id']:row.update(incoming)
            return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[dict(r) for r in stored]))
    incoming={**stored[0],'close':D('11')}
    assert _publish_rows(Connection(),'dwd_stock_daily',DAY,"market IN ('A','HK')",
                         tuple(incoming),('stock_id','trade_date'),[incoming],
                         retained_invalid_keys={(2,DAY)})==1
    assert stored[0]['close']==D('11') and stored[1]['close']==D('7.3') and stored[1]['is_valid_ohlc']==0
    writes.clear()
    _publish_rows(Connection(),'dwd_stock_daily',DAY,"market IN ('A','HK')",tuple(incoming),
                  ('stock_id','trade_date'),[incoming],retained_invalid_keys={(2,DAY)})
    assert not writes


def test_listing_delisting_and_calendar_boundaries():
    from datetime import timedelta
    s=snapshot();following=DAY+timedelta(days=1);third=following+timedelta(days=1)
    s['stocks'][0]['delisted_date']=following
    s['calendar']={('A',DAY):True,('A',following):False,('A',third):True}
    report=audit_snapshot(s,DAY-timedelta(days=1),third,{})
    assert report['expected']=={'002594.SZ':{DAY}}
    assert not report['references']

def test_native_sina_factor_keys_are_explicitly_renamed():
    from scheduler.stock_history_sources import parse_sina_factors
    a=parse_sina_factors({'data':[{'d':'2026-07-31','f':'3.1623355064420000'}]},'A')
    assert a.iloc[0]['date']=='2026-07-31'
    assert a.iloc[0]['hfq_factor']=='3.1623355064420000'
    hk=parse_sina_factors({'data':[{'d':'2026-06-11','f':'11.4','c':'48.9086786'}]},'HK')
    assert hk.iloc[0]['cash']=='48.9086786'
    with pytest.raises(ValueError):parse_sina_factors({'data':[{'unexpected':'1'}]},'A')


def test_em_tx_basis_does_not_require_sina_reinvestment_prices():
    from pandas import DataFrame
    from scheduler.stock_history_sources import verify_adjustment
    raw={**bar(),'open':D('78.5'),'high':D('78.84'),'low':D('77.54'),'close':D('78.04')}
    adjusted={**raw,'open':D('246.04'),'high':D('247.06'),'low':D('243.16'),'close':D('244.66')}
    factors=DataFrame([{'date':'2026-07-31','hfq_factor':'3.1623355064420000'}])
    assert verify_adjustment([raw],[adjusted],factors,'A',basis='em_tx')==[adjusted]
    with pytest.raises(ValueError,match='unadjusted'):
        verify_adjustment([raw],[raw],factors,'A',basis='em_tx')

def test_tencent_rejects_silent_raw_branch(monkeypatch):
    import json
    import requests
    from types import SimpleNamespace
    from scheduler.stock_history_sources import tencent_frame
    response=SimpleNamespace(text=json.dumps({'data':{'sz002594':{'day':[['2026-11-23','10','10','11','9','1']]}}}),raise_for_status=lambda:None)
    monkeypatch.setattr(requests,'get',lambda *args,**kwargs:response)
    with pytest.raises(ValueError,match='explicit hfqday'):
        tencent_frame('sz002594','2026-11-23','2026-11-23','hfq')


def test_collect_accepts_matching_em_tx_and_excludes_sina_other_basis(monkeypatch,tmp_path):
    from pandas import DataFrame
    from scheduler import stock_history_sources as sources
    import akshare as ak
    raw=DataFrame([{'date':DAY,'open':10,'high':11,'low':9,'close':10,'volume':1}])
    adjusted=DataFrame([{'date':DAY,'open':11,'high':12,'low':10,'close':11,'volume':1}])
    monkeypatch.setattr(ak,'stock_zh_a_hist',lambda **kwargs:adjusted if kwargs['adjust']=='hfq' else raw)
    def tx(symbol,start,end,adjust,**kwargs):
        return (adjusted if adjust=='hfq' else raw).rename(columns={'volume':'amount'})
    monkeypatch.setattr(sources,'tencent_frame',tx)
    monkeypatch.setattr(sources,'ths_frame',lambda *args: (_ for _ in ()).throw(ValueError('fixture unavailable')))
    factors=DataFrame([{'date':'1900-01-01','hfq_factor':'2'}])
    monkeypatch.setattr(sources,'sina_frame',lambda *args,**kwargs:factors if kwargs.get('factors') else raw.assign(volume=100))
    vendors,errors=sources.collect({'stock_code':'002594.SZ','market':'A'},DAY,DAY,tmp_path,requested={DAY})
    rows,issues=reconcile(vendors,{DAY})
    assert not issues and rows[0]['vendors']==['em','tx']
    assert not vendors['sina'][0]['_price_eligible'] and 'sina_basis' in errors

def test_sina_assignment_ignores_trailing_script_comment():
    from scheduler.stock_history_sources import sina_assignment
    assert sina_assignment('var KLC_KL_00175="abc123="; /* extra compressed history */')=='"abc123="'
    assert sina_assignment('var sz002594hfq={"data":[]}; /* provider note */')=='{"data":[]}'

def test_tencent_hk_uses_dedicated_endpoint_and_share_units(monkeypatch):
    import json,requests
    from types import SimpleNamespace
    from scheduler.stock_history_sources import tencent_frame,normalize
    seen=[]
    response=SimpleNamespace(text=json.dumps({'data':{'hk01211':{'hfqday':[['2026-11-23','747.009','746.589','752.709','729.489','29489886']]}}}),raise_for_status=lambda:None)
    def get(url,**kwargs):seen.append(url);return response
    monkeypatch.setattr(requests,'get',get)
    frame=tencent_frame('hk01211','2026-11-23','2026-11-23','hfq')
    assert '/hkfqkline/' in seen[0]
    assert normalize(frame,'tx','HK')[0]['volume']==29489886


def test_field_evidence_does_not_validate_a_different_price_basis():
    tx={**bar(), 'volume':None, 'amount':None}
    sina={**bar(close='10.5'), '_price_eligible':False}
    rows,issues=reconcile({'tx':[tx],'ths':[bar()],'sina':[sina]},{DAY})
    assert not issues and rows[0]['close']==D('10.0000')
    assert rows[0]['volume']==100
    assert rows[0]['vendors']==['tx','ths']
    assert rows[0]['field_vendors']['volume']==['ths','sina']
    assert reconcile({'tx':[tx],'sina':[sina]},{DAY})[1][DAY]=='waiting_source'
    assert reconcile({'tx':[tx],'ths':[bar()],'sina':[{**sina,'volume':101}]},{DAY})[1][DAY]=='source_conflict'


def test_three_sources_must_agree_pairwise_at_tolerance_boundary():
    vendors={'em':[bar()],'tx':[bar(close='10.00019')],'ths':[bar(close='9.99981')]}
    assert reconcile(vendors,{DAY})[1][DAY]=='source_conflict'
    vendors={'em':[bar()],'tx':[{**bar(),'amount':D('1000.009')}],
             'ths':[{**bar(),'amount':D('999.991')}]}
    assert reconcile(vendors,{DAY})[1][DAY]=='source_conflict'


def test_ths_requires_explicit_hfq_wrapper_and_exact_columns(monkeypatch):
    from scheduler.stock_history_sources import ths_frame
    import requests
    from types import SimpleNamespace
    body='quotebridge_v6_line_hs_002594_02_2026({"data":"20261123,246.04,247.06,243.16,244.66,27320904,2133038164.74"})'
    response=SimpleNamespace(text=body,raise_for_status=lambda:None)
    monkeypatch.setattr(requests,'get',lambda *a,**k:response)
    frame=ths_frame('002594',2026,'hfq')
    assert frame.iloc[0]['close']=='244.66'
    assert frame.iloc[0]['volume']=='27320904'
    with pytest.raises(ValueError,match='wrapper'):
        ths_frame('002594',2026,'')


def test_ths_incomplete_placeholder_does_not_discard_valid_year(monkeypatch):
    from scheduler.stock_history_sources import ths_frame
    import requests
    from types import SimpleNamespace
    body='quotebridge_v6_line_hs_600418_00_2026({"data":"20261123,10,11,9,10,100,1000;20261124,,,,10,0,"})'
    monkeypatch.setattr(requests,'get',lambda *a,**k:SimpleNamespace(text=body,raise_for_status=lambda:None))
    frame=ths_frame('600418',2026,'')
    assert len(frame)==1
    assert frame.attrs['rejected_dates']==['2026-11-24']


def test_tencent_identity_requires_explicit_complete_factor_evidence(monkeypatch):
    from scheduler.stock_history_sources import tencent_frame
    import requests,json
    from types import SimpleNamespace
    from pandas import DataFrame
    body=json.dumps({'data':{'hk01810':{'day':[['2026-11-23','10','10','11','9','100']]}}})
    monkeypatch.setattr(requests,'get',lambda *a,**k:SimpleNamespace(text=body,raise_for_status=lambda:None))
    factors=DataFrame([{'date':'1900-01-01','hfq_factor':'1','cash':'0'}])
    frame=tencent_frame('hk01810','2026-11-23','2026-11-23','hfq',factors=factors)
    assert frame.attrs['adjustment_evidence']=='identity_factors'
    with pytest.raises(ValueError):
        tencent_frame('hk01810','2026-11-23','2026-11-23','hfq',factors=factors.assign(cash='1'))


def test_ths_retries_transient_server_failure(monkeypatch):
    from scheduler.stock_history_sources import ths_frame
    import requests
    from types import SimpleNamespace
    calls=[]
    def get(*args,**kwargs):
        calls.append(args[0])
        if len(calls)==1:
            response=SimpleNamespace(status_code=502)
            raise requests.HTTPError('502',response=response)
        assert '/v4/' in args[0]
        return SimpleNamespace(text='quotebridge_v4_line_hs_002594_02_2026({"data":""})',raise_for_status=lambda:None)
    monkeypatch.setattr(requests,'get',get)
    assert ths_frame('002594',2026,'hfq').empty
    assert len(calls)==2


def test_collect_retains_other_years_when_one_year_fails(monkeypatch,tmp_path):
    import akshare as ak
    from pandas import DataFrame
    from scheduler import stock_history_sources as sources
    def unavailable(*a,**k):raise ValueError('unavailable')
    monkeypatch.setattr(ak,'stock_zh_a_hist',unavailable)
    monkeypatch.setattr(sources,'tencent_frame',unavailable)
    factors=DataFrame([{'date':'1900-01-01','hfq_factor':'1'}])
    monkeypatch.setattr(sources,'sina_frame',lambda *a,**k:factors if k.get('factors') else DataFrame())
    def ths(symbol,year,adjust):
        if year==2018:raise ValueError('temporary yearly failure')
        return DataFrame([{**bar(date(year,1,3)), 'date':date(year,1,3)}]).drop(columns='trade_date')
    monkeypatch.setattr(sources,'ths_frame',ths)
    vendors,errors=sources.collect({'stock_code':'002594.SZ','market':'A'},date(2017,1,1),date(2019,12,31),tmp_path)
    assert {r['trade_date'].year for r in vendors['ths']}=={2017,2019}
    assert 'ths:2018' in errors
