from datetime import date
from contextlib import nullcontext

from pandas import DataFrame


def test_unbounded_futures_rejects_source_ohlc_before_write(monkeypatch):
    from pipelines.ingest.futures import load_futures_main_sina as loader
    from scheduler.jobs.common import build_scheduler_config

    info = {'commodity_id': 1, 'commodity_code': 'AL', 'commodity_name': 'Aluminum', 'main_symbol': 'AL0', 'exchange': 'SHFE'}
    frame = DataFrame([{'date': date(2006, 12, 6), 'open': 20350, 'high': 20260, 'low': 20200, 'close': 20240, 'settle': 20240, 'hold': 46542}])
    monkeypatch.setattr(loader, 'get_last_trade_date', lambda *_: None)
    monkeypatch.setattr(loader, 'patched_requests_runtime', lambda *_: nullcontext())
    monkeypatch.setattr(loader, 'fetch_futures_main_daily', lambda *_: frame)
    written = []
    monkeypatch.setattr(loader, 'insert_daily_data', lambda rows: written.extend(rows) or len(rows))
    result = loader.fetch_single_commodity(info, build_scheduler_config())
    assert result['status'] == 'failed'
    assert result['error_type'] == 'invalid_rows'
    assert written == []
import pytest


def test_shfe_closed_date_source_is_complemented_only_for_completed_years():
    from scheduler.futures_repair import parse_shfe_calendar
    rows = parse_shfe_calendar("define(function (){return {2025:'20250101,20250104',2026:'20260101'};});", through_year=2025)
    states = {str(r['trade_date']): r['is_open'] for r in rows}
    assert len(rows) == 365
    assert states['2025-01-01'] == 0
    assert states['2025-01-02'] == 1
    assert '2026-01-01' not in states
    with pytest.raises(ValueError):
        parse_shfe_calendar("define(function (){return {2025:'20240101'};});", through_year=2025)


def test_gap_requires_explicit_main_identity_and_exact_official_match():
    from scheduler.futures_repair import validate_gap
    good = {'code': 'AL', 'date': '2015-07-02', 'provider_symbol': 'alm', 'provider_name': '\u6caa\u94dd\u4e3b\u8fde', 'volume_oi_multiplier': 2, 'em_row': ['2015-07-02','10','11','12','9','5','','','','','','','7'], 'official': {'PRODUCTID':'al_f', 'DELIVERYMONTH':'1509', 'OPENPRICE':10,'CLOSEPRICE':11,'HIGHESTPRICE':12,'LOWESTPRICE':9,'VOLUME':10,'OPENINTEREST':14,'SETTLEMENTPRICE':11}}
    assert validate_gap(good)['volume'] == 10
    with pytest.raises(ValueError):
        validate_gap({**good, 'provider_symbol':'al1509'})
    with pytest.raises(ValueError):
        validate_gap({**good, 'official': {**good['official'], 'HIGHESTPRICE':13}})


def test_gap_state_acceptance_requires_complete_reconciliation(monkeypatch):
    from scheduler.futures_repair import reconcile_gap_acceptance
    from scheduler import market_workflow as market
    from types import SimpleNamespace
    day=date(2019,4,22)
    saved=[]
    state=SimpleNamespace(save=lambda *a,**k:saved.append((a,k)))
    monkeypatch.setattr(market,'expected_sessions',lambda *a:({day:{'CU','AL'}},{day:set()}))
    monkeypatch.setattr(market,'actual_sessions',lambda *a:{day:{'CU'}})
    with pytest.raises(ValueError):
        reconcile_gap_acceptance([day],state=state)
    assert not saved
    monkeypatch.setattr(market,'actual_sessions',lambda *a:{day:{'CU','AL'}})
    reconcile_gap_acceptance([day],state=state)
    assert saved[0][0]==('load_futures_daily','2019-04-22','ods_accepted')
    assert 'SHFE' in saved[0][1]['message']


def test_gap_rejects_invalid_ohlc_even_when_both_sources_agree():
    from scheduler.futures_repair import validate_gap
    bad = {'code': 'AL', 'date': '2015-07-02', 'provider_symbol': 'alm', 'provider_name': '\u6caa\u94dd\u4e3b\u8fde', 'volume_oi_multiplier': 2, 'em_row': ['2015-07-02','15','11','12','9','5','','','','','','','7'], 'official': {'PRODUCTID':'al_f', 'DELIVERYMONTH':'1509', 'OPENPRICE':15,'CLOSEPRICE':11,'HIGHESTPRICE':12,'LOWESTPRICE':9,'VOLUME':10,'OPENINTEREST':14,'SETTLEMENTPRICE':11}}
    with pytest.raises(ValueError, match='OHLC'):
        validate_gap(bad)


def test_gfex_calendar_obeys_own_notices_and_keeps_makeup_weekends_closed():
    from scheduler.gfex_calendar_repair import calendar_rows
    manifest={'start':'2023-07-21','end':'2025-12-31','annual_notices':{'2023':{'issuer':'GFEX','source_ref':'https://example.com/2023','closures':[['2023-09-29','2023-10-06']]},'2024':{'issuer':'GFEX','source_ref':'https://example.com/2024','closures':[['2024-02-09','2024-02-17']]},'2025':{'issuer':'GFEX','source_ref':'https://example.com/2025','closures':[['2025-10-01','2025-10-08']]}}}
    rows=calendar_rows(manifest)
    assert len(rows)==895
    states={str(x['trade_date']):x['is_open'] for x in rows}
    assert states['2024-02-09']==0
    assert states['2024-02-18']==0
    assert states['2024-02-19']==1
    assert states['2025-09-28']==0
    assert states['2025-10-08']==0
    assert states['2025-10-09']==1
    assert states['2023-10-07']==0
