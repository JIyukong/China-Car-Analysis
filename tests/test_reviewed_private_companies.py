import pytest
from sqlalchemy import create_engine, text


def test_private_company_registration_is_idempotent_and_rejects_conflicts():
    from scheduler.reviewed_private_companies import register_company
    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE dim_company (company_code TEXT,company_name TEXT,full_name TEXT,company_type TEXT,country TEXT,is_listed INTEGER)'))
        values = {'code': 'HYCAN', 'name': '合创汽车', 'full': '合创汽车科技有限公司'}
        assert register_company(conn, values) is True
        assert register_company(conn, values) is False
        assert conn.execute(text('SELECT company_type,is_listed FROM dim_company')).one() == ('其他', 0)
        with pytest.raises(ValueError, match='冲突'):
            register_company(conn, {**values, 'full': 'Different entity'})
        assert conn.execute(text('SELECT COUNT(*) FROM dim_company')).scalar_one() == 1
    db.dispose()


def test_private_company_command_rejects_mixed_chain_before_database(monkeypatch):
    from scheduler import runner
    with pytest.raises(SystemExit):
        runner.main(['--seed-reviewed-private-companies', 'report.json', '--chain', 'style', '--period', '2026-07'])


@pytest.mark.parametrize('ownership_conflict', [False, True])
def test_job_commits_all_entities_or_rolls_back_without_success_receipt(tmp_path, monkeypatch, ownership_conflict):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from scheduler import reviewed_private_companies as job, monthly_publish, workflow
    from pipelines.dim import manual_refresh

    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE dim_company (id INTEGER PRIMARY KEY,company_code TEXT,company_name TEXT,full_name TEXT,company_type TEXT,country TEXT,is_listed INTEGER)'))
        conn.execute(text('CREATE TABLE dim_oem (id INTEGER PRIMARY KEY,oem_name TEXT,company_id INTEGER)'))
        conn.execute(text('CREATE TABLE ods_sales_monthly_model (source TEXT,stat_month TEXT,manufacturer_name_raw TEXT)'))
        for i, company in enumerate(job.COMPANIES, 1):
            conn.execute(text('INSERT INTO dim_oem VALUES (:id,:name,NULL)'), {'id': i, 'name': company['name']})
    monkeypatch.setattr(workflow, 'State', lambda: SimpleNamespace(lock=lambda *a, **kw: nullcontext()))
    monkeypatch.setattr(monthly_publish, '_publication_transaction', db.begin)
    def apply(conn, kind, key, values):
        if ownership_conflict and key == job.COMPANIES[1]['name']:
            raise ValueError('confirmed owner conflict')
        conn.execute(text('UPDATE dim_oem SET company_id=(SELECT id FROM dim_company WHERE company_code=:code) WHERE oem_name=:name'),
                     {'code': values['company_code'], 'name': key})
    monkeypatch.setattr(manual_refresh, 'apply_feishu_dimension', apply)
    receipt = tmp_path / 'receipt.json'
    if ownership_conflict:
        with pytest.raises(ValueError, match='conflict'):
            job.run(receipt)
        assert not receipt.exists()
        with db.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM dim_company')).scalar_one() == 0
            assert conn.execute(text('SELECT COUNT(*) FROM dim_oem WHERE company_id IS NOT NULL')).scalar_one() == 0
    else:
        assert job.run(receipt)['companies_created'] == len(job.COMPANIES)
        assert job.run(receipt)['companies_created'] == 0
        assert receipt.exists()
        with db.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM dim_oem WHERE company_id IS NOT NULL')).scalar_one() == len(job.COMPANIES)
    db.dispose()
