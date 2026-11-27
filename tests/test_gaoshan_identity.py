def test_gaoshan_names_never_alias_to_each_other():
    from scheduler.sales_source_renames import canonical_key, canonical_city_name, rename_plan
    hev = ('魏牌 高山', '长城汽车')
    phev = ('魏牌 高山新能源', '长城汽车')
    assert canonical_key(hev) == hev
    assert canonical_key(phev) == phev
    assert canonical_city_name(hev[0]) == hev[0]
    assert canonical_city_name(phev[0]) == phev[0]
    assert rename_plan({hev:(1,)}, {phev:(1,)}) == []


def test_repair_command_plan_is_offline_and_rejects_chain():
    import pytest
    from scheduler.runner import main
    assert main(['--repair-gaoshan-identities','unused.json','--plan']) == 0
    with pytest.raises(SystemExit):
        main(['--repair-gaoshan-identities','unused.json','--chain','style'])


def make_database():
    from sqlalchemy import create_engine, text
    from scheduler.gaoshan_identity import HEV, PHEV
    db = create_engine('sqlite://')
    with db.begin() as conn:
        for definition in [
            'dim_oem (id INTEGER PRIMARY KEY,oem_name TEXT)',
            'dim_model (id INTEGER PRIMARY KEY,model_name TEXT,energy_type TEXT,oem_id INTEGER,brand_id INTEGER,vehicle_type TEXT,first_seen_month TEXT,last_seen_month TEXT,is_active INTEGER)',
            'dim_energy_vehicle (model_name TEXT,energy_type TEXT,vehicle_type TEXT,source_file TEXT)',
            'dim_city_model (id INTEGER PRIMARY KEY,source TEXT,source_model_name TEXT,energy_type TEXT,vehicle_type TEXT,evidence TEXT)',
            'ods_sales_monthly_model (stat_month TEXT,source TEXT,model_name_raw TEXT,manufacturer_name_raw TEXT,sales_volume INTEGER)',
            'dwd_sales_monthly_model (id INTEGER PRIMARY KEY,stat_month TEXT,source TEXT,model_name_raw TEXT,oem_id INTEGER,brand_id INTEGER,company_id INTEGER,sales_volume INTEGER,price_min NUMERIC,price_max NUMERIC,rank_no INTEGER,row_hash TEXT,model_id INTEGER,model_name TEXT,UNIQUE(stat_month,model_id))',
        ]:
            conn.execute(text('CREATE TABLE '+definition))
        conn.execute(text('INSERT INTO dim_oem VALUES (110,:name)'), {'name':'长城汽车'})
        for identifier, name in [(1,HEV),(2,PHEV)]:
            conn.execute(text("INSERT INTO dim_model VALUES (:id,:name,'新能源',110,130,'MPV','2023-11-01','2026-10-01',1)"),{'id':identifier,'name':name})
            conn.execute(text("INSERT INTO dim_energy_vehicle VALUES (:name,'新能源','MPV','previous')"),{'name':name})
            conn.execute(text("INSERT INTO dim_city_model VALUES (:id,'16888_city',:name,'新能源','MPV','previous')"),{'id':identifier,'name':name})
        conn.execute(text("INSERT INTO ods_sales_monthly_model VALUES ('2026-07-01','16888_style',:name,:oem,5505)"), {'name':PHEV,'oem':'长城汽车'})
        for identifier, month, raw, model, sales in [(1,'2026-07',PHEV,1,5505),(2,'2026-10',HEV,1,364),(3,'2026-10',PHEV,2,6103)]:
            conn.execute(text("INSERT INTO dwd_sales_monthly_model VALUES (:id,:month,'16888_style',:raw,110,130,1,:sales,1,2,1,:hash,:model,:name)"),
                         {'id':identifier,'month':month,'raw':raw,'sales':sales,'hash':str(identifier),'model':model,'name':HEV if model==1 else PHEV})
    return db


def test_repair_moves_only_misassigned_phev_and_is_idempotent():
    from sqlalchemy import text
    from scheduler.gaoshan_identity import repair, HEV, PHEV
    db=make_database()
    with db.begin() as conn:
        before=conn.execute(text('SELECT id,sales_volume,row_hash FROM dwd_sales_monthly_model ORDER BY id')).all()
        assert repair(conn)['reassigned_phev_facts']==1
        assert repair(conn)['reassigned_phev_facts']==0
        assert conn.execute(text('SELECT id,sales_volume,row_hash FROM dwd_sales_monthly_model ORDER BY id')).all()==before
        assert conn.execute(text('SELECT model_id,model_name FROM dwd_sales_monthly_model WHERE id=1')).one()==(2,PHEV)
        assert conn.execute(text('SELECT model_name,energy_type,first_seen_month FROM dim_model WHERE id=1')).one()==(HEV,'燃油','2026-10-01')
        assert conn.execute(text('SELECT energy_type FROM dim_city_model WHERE id=1')).scalar_one()=='燃油'


def test_conflicting_target_month_rolls_back_without_changing_energy():
    import pytest
    from sqlalchemy import text
    from scheduler.gaoshan_identity import repair, PHEV
    db=make_database()
    with db.begin() as conn:
        conn.execute(text("INSERT INTO dwd_sales_monthly_model SELECT 4,stat_month,source,model_name_raw,oem_id,brand_id,company_id,sales_volume,price_min,price_max,rank_no,'4',2,:name FROM dwd_sales_monthly_model WHERE id=1"),{'name':PHEV})
    with pytest.raises(ValueError,match='同月PHEV'):
        with db.begin() as conn:
            repair(conn)
    with db.connect() as conn:
        assert conn.execute(text('SELECT energy_type FROM dim_model WHERE id=1')).scalar_one()=='新能源'
        assert conn.execute(text('SELECT model_id FROM dwd_sales_monthly_model WHERE id=1')).scalar_one()==1


def test_postcommit_receipt_failure_is_not_reported_as_database_rollback(tmp_path, monkeypatch):
    import pytest
    from contextlib import contextmanager, nullcontext
    from pathlib import Path
    from types import SimpleNamespace
    from scheduler import gaoshan_identity as job, workflow, monthly_publish
    committed=[]
    @contextmanager
    def transaction():
        yield object()
        committed.append(True)
    monkeypatch.setattr(workflow,'State',lambda:SimpleNamespace(lock=lambda *a,**k:nullcontext()))
    monkeypatch.setattr(monthly_publish,'_publication_transaction',transaction)
    monkeypatch.setattr(job,'repair',lambda conn:{'reassigned_phev_facts':33})
    def fail(*args,**kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(Path,'write_text',fail)
    with pytest.raises(job.ReceiptWriteError) as error:
        job.run(tmp_path/'receipt.json')
    assert committed == [True]
    assert error.value.report['reassigned_phev_facts']==33
