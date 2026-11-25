from datetime import date
import pytest


def test_quarantine_preserves_snapshot_prices_and_rejects_actual_trades():
    from types import SimpleNamespace
    from scheduler.stock_repair import quarantine_suspension_snapshot
    statements=[]
    row={'open':7.3,'high':7.3,'low':7.3,'close':7.3,'volume':0,'is_valid_ohlc':1}
    class Connection:
        def execute(self,sql,params):
            statements.append(str(sql))
            if str(sql).startswith('UPDATE'):
                row['is_valid_ohlc']=0
            return SimpleNamespace(mappings=lambda:SimpleNamespace(one_or_none=lambda:dict(row)))
    quarantine_suspension_snapshot(Connection(),{'code':'1114.HK','date':'2021-03-31'})
    assert row['open']==7.3 and row['close']==7.3 and row['is_valid_ohlc']==0
    update=next(s for s in statements if s.startswith('UPDATE'))
    assert 'SET is_valid_ohlc=0' in update and 'DELETE' not in update
    row.update(volume=100,is_valid_ohlc=1)
    with pytest.raises(ValueError,match='non-snapshot'):
        quarantine_suspension_snapshot(Connection(),{'code':'1114.HK','date':'2021-03-31'})


def test_repair_manifest_rejects_us_before_collection(tmp_path):
    from scheduler.stock_repair import read_dates
    p=tmp_path/'dates.csv'
    p.write_text('code,date\nLI.US,2026-08-13\n',encoding='utf-8')
    with pytest.raises(ValueError,match='A/HK'):
        read_dates(p)


def test_repair_manifest_deduplicates_and_rejects_future(tmp_path):
    from scheduler.stock_repair import read_dates
    p=tmp_path/'dates.csv'
    p.write_text('code,date\n002594.SZ,2026-08-13\n002594.SZ,2026-08-13\n1211.HK,2026-08-14\n',encoding='utf-8')
    assert read_dates(p)=={'002594.SZ':frozenset({date(2026,8,13)}),'1211.HK':frozenset({date(2026,8,14)})}
    p.write_text('code,date\n002594.SZ,2999-01-01\n',encoding='utf-8')
    with pytest.raises(ValueError):read_dates(p)


def test_evidence_rejects_invalid_reference_before_writing(tmp_path):
    import json
    from scheduler.stock_repair import apply_evidence
    p=tmp_path/'evidence.json'
    p.write_text(json.dumps({'suspensions':[{'code':'002594.SZ','date':'2026-08-13','source_ref':'unverified'}]}),encoding='utf-8')
    with pytest.raises(ValueError,match='source URL'):
        apply_evidence(p)


def test_repair_cli_rejects_mixed_operations():
    from scheduler.runner import main
    with pytest.raises(SystemExit) as exc:
        main(['--repair-stock-dates','dates.csv','--chain','daily'])
    assert exc.value.code==2
