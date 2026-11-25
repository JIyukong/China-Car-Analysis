from datetime import date, timedelta
from decimal import Decimal as D

import pandas as pd
import pytest

from scheduler import stock_hfq_cache as cache


DAY=date(2026,8,13)
DATES={DAY-timedelta(days=1),DAY,DAY+timedelta(days=1)}
STOCK={'stock_code':'002594.SZ','market':'A'}


def frames(factor='2',market='A'):
    raw=pd.DataFrame([{'date':day,'open':10,'high':11,'low':9,'close':10,'volume':123,'amount':1230} for day in sorted(DATES)])
    factors=pd.DataFrame([{'date':'1900-01-01','hfq_factor':factor,**({'cash':'1'} if market=='HK' else {})}])
    return raw,factors


def test_free_single_source_requires_factors_and_preserves_trade_units(tmp_path,monkeypatch):
    raw,factors=frames()
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:factors if k['factors'] else raw)
    result=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    assert not result.issues and len(result.rows)==3
    assert result.rows[0]['close']==D('20.0000')
    assert result.rows[0]['volume']==123 and result.rows[0]['amount']==D('1230')
    assert result.rows[0]['vendors']==['sina']
    assert result.rows[0]['factor_version']==result.factor_version
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:pd.DataFrame() if k['factors'] else raw)
    with pytest.raises(ValueError,match='factor'):
        cache.prepare(STOCK,DATES,DATES,tmp_path/'run2',tmp_path/'cache',result.factors)


def test_new_dividend_recalculates_only_its_effective_interval_from_cached_raw(tmp_path,monkeypatch):
    raw,factors=frames();calls=[]
    def fetch(*a,**k):calls.append(k['factors']);return factors if k['factors'] else raw
    monkeypatch.setattr(cache,'fetch_frame',fetch)
    first=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    factors=pd.DataFrame([{'date':'1900-01-01','hfq_factor':'2'},{'date':str(DAY),'hfq_factor':'3'}])
    calls.clear()
    result=cache.prepare(STOCK,DATES,set(),tmp_path/'run2',tmp_path/'cache',first.factors)
    assert calls==[True]
    assert result.affected=={DAY,DAY+timedelta(days=1)}
    assert {r['trade_date'] for r in result.rows}==result.affected
    assert all(r['close']==D('30.0000') for r in result.rows)


def test_factor_revision_or_removal_recalculates_until_next_unchanged_event():
    old=[{'date':'1900-01-01','hfq_factor':'1'}, {'date':str(DAY),'hfq_factor':'2'},
         {'date':str(DAY+timedelta(days=1)),'hfq_factor':'3'}]
    new=[old[0],old[2]]
    assert cache.changed_factor_dates(old,new,DATES,'A')=={DAY}
    assert cache.changed_factor_dates(old,old,DATES,'A')==set()


def test_hk_cash_changes_are_tracked_and_do_not_adjust_volume(tmp_path,monkeypatch):
    raw,factors=frames(market='HK')
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:factors if k['factors'] else raw)
    result=cache.prepare({'stock_code':'1211.HK','market':'HK'},DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    assert result.rows[0]['close']==D('21.0000') and result.rows[0]['volume']==123
    changed=[{**result.factors[0],'cash':'2'}]
    assert cache.changed_factor_dates(result.factors,changed,DATES,'HK')==DATES


def test_repeat_run_uses_cache_and_changed_raw_history_is_detected_on_review(tmp_path,monkeypatch):
    raw,factors=frames();calls=[]
    def fetch(*a,**k):calls.append(k['factors']);return factors if k['factors'] else raw
    monkeypatch.setattr(cache,'fetch_frame',fetch)
    first=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    second=cache.prepare(STOCK,DATES,{max(DATES)},tmp_path/'run2',tmp_path/'cache',first.factors)
    assert not second.fetched_raw
    raw['close']=raw['close'].astype(float)
    raw.loc[0,'close']=10.5
    third=cache.prepare(STOCK,DATES,{max(DATES)},tmp_path/'run3',tmp_path/'cache',first.factors,review=True)
    assert third.raw_changed=={min(DATES)}
    assert min(DATES) in {r['trade_date'] for r in third.rows}


def test_missing_zero_volume_and_duplicate_records_cannot_pass(tmp_path,monkeypatch):
    raw,factors=frames();raw=raw.iloc[:1].copy();raw.loc[0,['open','high','low','close','volume']]=[10,10,10,10,0]
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:factors if k['factors'] else raw)
    result=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    assert not result.rows and result.issues[min(DATES)]=='invalid_source'
    assert result.issues[max(DATES)]=='waiting_source'
    raw=pd.concat([raw,raw])
    with pytest.raises(ValueError,match='Duplicate'):
        cache.prepare(STOCK,DATES,DATES,tmp_path/'run2',tmp_path/'cache2',None)


def test_cache_corruption_refetches_instead_of_trusting_or_deleting_history(tmp_path,monkeypatch):
    raw,factors=frames();calls=[]
    def fetch(*a,**k):calls.append(k['factors']);return factors if k['factors'] else raw
    monkeypatch.setattr(cache,'fetch_frame',fetch)
    first=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    path=tmp_path/'cache'/'002594.SZ'/'raw.json'
    path.write_text('{broken',encoding='utf-8');calls.clear()
    result=cache.prepare(STOCK,DATES,DATES,tmp_path/'run2',tmp_path/'cache',first.factors)
    assert calls==[True,False] and len(result.rows)==3


def test_uncommitted_raw_revision_survives_cache_refresh_and_retry(tmp_path,monkeypatch):
    from scheduler.stock_history import price_hash
    from scheduler.stock_history_sources import normalize
    raw,factors=frames()
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:factors if k['factors'] else raw)
    first=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    committed={r['trade_date']:price_hash(r) for r in normalize(raw,'sina','A')}
    raw['close']=raw['close'].astype(float);raw.loc[0,'close']=10.5
    second=cache.prepare(STOCK,DATES,{max(DATES)},tmp_path/'run2',tmp_path/'cache',first.factors,
                         review=True,previous_raw_hashes=committed)
    assert second.raw_changed=={min(DATES)}
    # A different stock fails publication: accepted evidence remains unchanged.
    retry=cache.prepare(STOCK,DATES,{max(DATES)},tmp_path/'run3',tmp_path/'cache',first.factors,
                        previous_raw_hashes=committed)
    assert not retry.fetched_raw
    assert retry.raw_changed=={min(DATES)}
    assert any(r['trade_date']==min(DATES) and r['close']==D('21') for r in retry.rows)


def test_invalid_cached_bar_is_refetched_on_same_business_day(tmp_path,monkeypatch):
    raw,factors=frames();raw.loc[0,['open','high','low','close','volume']]=[10,10,10,10,0]
    monkeypatch.setattr(cache,'fetch_frame',lambda *a,**k:factors if k['factors'] else raw)
    first=cache.prepare(STOCK,DATES,DATES,tmp_path/'run',tmp_path/'cache',None)
    assert first.issues[min(DATES)]=='invalid_source'
    raw.loc[0,['high','low','volume']]=[11,9,123]
    result=cache.prepare(STOCK,DATES,{min(DATES)},tmp_path/'run2',tmp_path/'cache',first.factors)
    assert result.fetched_raw and not result.issues


def test_factors_missing_baseline_or_duplicate_effective_date_are_rejected():
    with pytest.raises(ValueError):
        cache.changed_factor_dates([], [{'date':str(DAY),'hfq_factor':'1'}],DATES,'A')
    with pytest.raises(ValueError):
        cache.changed_factor_dates([], [{'date':'1900-01-01','hfq_factor':'1'}]*2,DATES,'A')
