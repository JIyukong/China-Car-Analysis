import pytest
from sqlalchemy import create_engine, text


def test_approved_scope_is_fixed_and_excluded_from_company_analysis():
    from scheduler.research_scope import OEMS, COMPANY, research_companies
    assert len(OEMS) == len(set(OEMS)) == 34
    assert 'DS汽车' in OEMS and '云雀汽车' in OEMS
    rows = [{'company_code': 'SAIC'}, {'company_code': COMPANY['code']},
            {'company_code': 'OTHER'}, {'company_code': None}]
    assert research_companies(rows) == [rows[0]]
    assert len(rows) == 4  # market source remains intact
    from scheduler.research_scope import is_research_bucket
    assert is_research_bucket('company', COMPANY['name'])
    assert not is_research_bucket('brand', COMPANY['name'])


def test_scope_registration_uses_nonlisted_bucket_and_rejects_conflict():
    from scheduler.research_scope import COMPANY
    from scheduler.reviewed_private_companies import register_company
    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE dim_company (company_code TEXT,company_name TEXT,full_name TEXT,company_type TEXT,country TEXT,is_listed INTEGER)'))
        assert register_company(conn, COMPANY)
        assert not register_company(conn, COMPANY)
        with pytest.raises(ValueError, match='冲突'):
            register_company(conn, {**COMPANY, 'full': '真实法人'})


def test_scope_plan_is_offline_and_mixed_flags_rejected():
    from scheduler import runner
    assert runner.main(['--apply-research-exclusions', 'unused.json', '--plan']) == 0
    with pytest.raises(SystemExit):
        runner.main(['--apply-research-exclusions', 'unused.json', '--chain', 'style'])


@pytest.mark.parametrize('conflict', [False, True])
def test_scope_atomicity_and_idempotent_receipt(tmp_path, monkeypatch, conflict):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from scheduler import research_scope as job, monthly_publish, workflow
    from pipelines.dim import manual_refresh
    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE dim_company (id INTEGER PRIMARY KEY,company_code TEXT,company_name TEXT,full_name TEXT,company_type TEXT,country TEXT,is_listed INTEGER)'))
        conn.execute(text('CREATE TABLE dim_oem (id INTEGER PRIMARY KEY,oem_name TEXT,company_id INTEGER)'))
        conn.execute(text('CREATE TABLE bridge_brand_oem (oem_id INTEGER,company_id INTEGER,is_current INTEGER)'))
        for i, name in enumerate(job.OEMS, 1):
            conn.execute(text('INSERT INTO dim_oem VALUES (:id,:name,NULL)'), {'id': i, 'name': name})
    monkeypatch.setattr(workflow, 'State', lambda: SimpleNamespace(lock=lambda *a, **kw: nullcontext()))
    monkeypatch.setattr(monthly_publish, '_publication_transaction', db.begin)
    def apply(conn, kind, key, values):
        if conflict and key == job.OEMS[-1]:
            raise ValueError('confirmed ownership conflict')
        conn.execute(text('UPDATE dim_oem SET company_id=(SELECT id FROM dim_company WHERE company_code=:code) WHERE oem_name=:name'), {'code': values['company_code'], 'name': key})
    monkeypatch.setattr(manual_refresh, 'apply_feishu_dimension', apply)
    receipt = tmp_path / 'receipt.json'
    if conflict:
        with pytest.raises(ValueError, match='conflict'):
            job.run(receipt)
        assert not receipt.exists()
        with db.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM dim_company')).scalar_one() == 0
            assert conn.execute(text('SELECT COUNT(*) FROM dim_oem WHERE company_id IS NOT NULL')).scalar_one() == 0
    else:
        assert len(job.run(receipt)['oems']) == 34
        assert job.run(receipt)['companies_created'] == 0
        assert receipt.exists()


def test_closed_historical_owner_cannot_be_silently_accepted():
    from scheduler.research_scope import validate_historical_ownership
    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE dim_oem (id INTEGER,oem_name TEXT)'))
        conn.execute(text('CREATE TABLE dim_company (id INTEGER,company_code TEXT)'))
        conn.execute(text('CREATE TABLE bridge_brand_oem (oem_id INTEGER,company_id INTEGER,is_current INTEGER)'))
        conn.execute(text("INSERT INTO dim_oem VALUES (1,'DS汽车')"))
        conn.execute(text("INSERT INTO dim_company VALUES (2,'REAL_COMPANY')"))
        conn.execute(text('INSERT INTO bridge_brand_oem VALUES (1,2,0)'))
        with pytest.raises(ValueError, match='历史公司归属'):
            validate_historical_ownership(conn, 'DS汽车')
        conn.execute(text("UPDATE dim_company SET company_code='OUT_OF_RESEARCH'"))
        validate_historical_ownership(conn, 'DS汽车')
