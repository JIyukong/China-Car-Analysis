import pytest

from test_monthly_publish import warehouse, add_source, enable_city_wide


def test_city_history_relinks_orphans_without_changing_legacy(warehouse, monkeypatch):
    from scheduler.city_history import restore_month
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    execute("""INSERT INTO dwd_sales_monthly_model_city
        (stat_month,source,model_name,model_name_raw,model_id,city_name_raw,city_name,sales_volume,row_hash)
        VALUES ('2024-01','16888_city','CityOnly','CityOnly',999,'City','City',7,'legacy')""")
    before = execute('SELECT * FROM dwd_sales_monthly_model_city')
    report = restore_month('2024-01')
    assert report['legacy_orphan_rows_retained'] == 1
    assert report['warehouse_rows'] == 1
    assert report['sales'] == 7
    assert execute('SELECT * FROM dwd_sales_monthly_model_city') == before
    assert execute('SELECT sales_volume,city_model_id FROM dwd_sales_monthly_city') == [(7, 1)]
    assert restore_month('2024-01')['inserted_rows'] == 0


def test_city_history_rejects_retained_fact_disagreement(warehouse, monkeypatch):
    from scheduler.city_history import restore_month
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    publish.publish_month('city', '2024-01')
    execute('UPDATE ods_sales_monthly_model_city SET sales_volume=8')
    with pytest.raises(publish.ReconciliationError):
        restore_month('2024-01')
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_city') == [(7,)]


def test_city_replay_skips_unchanged_discovery_and_lifecycle_writes(warehouse, monkeypatch):
    from sqlalchemy import event
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    publish.publish_month('city', '2024-01')
    updates = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE DIM_CITY_MODEL'):
            updates.append(statement)
    event.listen(publish.engine, 'before_cursor_execute', observe)
    try:
        publish.publish_month('city', '2024-01')
    finally:
        event.remove(publish.engine, 'before_cursor_execute', observe)
    assert updates == []


def test_city_history_empty_ods_is_complete(warehouse, tmp_path, monkeypatch):
    from scheduler.city_history import run
    from scheduler import workflow
    state_type = workflow.State
    monkeypatch.setattr(workflow, 'State', lambda: state_type(tmp_path / 'state'))
    report = run(tmp_path / 'report.json')
    assert report['complete'] is True
    assert report['months'] == []
    assert (tmp_path / 'report.json').exists()


def test_city_history_accepts_unique_exact_full_area_name(warehouse, monkeypatch):
    from scheduler.city_history import restore_month
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    execute('ALTER TABLE dim_area ADD COLUMN city_name TEXT')
    execute("UPDATE dim_area SET city_name='Full City'")
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    execute("UPDATE ods_sales_monthly_model_city SET city_name_raw='Full City'")
    assert restore_month('2024-01')['sales'] == 7
    assert execute('SELECT area_id FROM dwd_sales_monthly_city') == [(1,)]


def test_city_history_quarantines_unknown_area_without_blocking_valid_rows(warehouse, monkeypatch):
    from scheduler.city_history import restore_month
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    add_source(execute, '2024-01', 3, 'UnknownArea', city=True)
    execute("UPDATE ods_sales_monthly_model_city SET city_name_raw='Unmapped' WHERE model_name_raw='UnknownArea'")
    result = restore_month('2024-01')
    assert result['warehouse_rows'] == 1
    assert result['complete'] is False
    assert result['quarantined_sales'] == 3
    assert result['quarantined_rows'][0]['city_name'] == 'Unmapped'


def test_city_area_hainan_alias_requires_reviewed_qinghai_codes(warehouse):
    from scheduler.city_publish import area_ids
    publish, execute = warehouse
    for name in ('city_code', 'province_code'):
        execute(f'ALTER TABLE dim_area ADD COLUMN {name} TEXT')
    execute("INSERT INTO dim_area (id,city_short_name,city_code,province_code) VALUES (1,'海南州','632500','630000')")
    with publish.engine.connect() as conn:
        assert area_ids(conn)['海南'] == 1
    execute("UPDATE dim_area SET province_code='460000'")
    with publish.engine.connect() as conn:
        assert '海南' not in area_ids(conn)


def test_city_partial_restore_cannot_discard_already_published_keys(warehouse, monkeypatch):
    from scheduler.city_history import restore_month
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    execute("INSERT INTO dim_area VALUES (2,'Other')")
    add_source(execute, '2024-01', 7, 'CityOnly', city=True)
    add_source(execute, '2024-01', 3, 'Second', city=True)
    execute("UPDATE ods_sales_monthly_model_city SET city_name_raw='Other' WHERE model_name_raw='Second'")
    publish.publish_month('city', '2024-01')
    execute("UPDATE dim_area SET city_short_name='Renamed' WHERE id=2")
    with pytest.raises(publish.ReconciliationError):
        restore_month('2024-01')
    assert execute('SELECT SUM(sales_volume),COUNT(*) FROM dwd_sales_monthly_city') == [(10, 2)]


def test_city_restore_cli_routes_one_month_without_source_acceptance(monkeypatch):
    from scheduler import runner, city_history
    from base_infra.config import get_settings
    monkeypatch.setattr(get_settings(), 'DB_USER', 'etl')
    calls = []
    monkeypatch.setattr(city_history, 'run', lambda path, *, period=None:
                        calls.append((path, period)) or {'complete': True})
    assert runner.main(['--restore-city-history', 'report.json', '--period', '2024-01']) == 0
    assert calls == [('report.json', '2024-01')]


@pytest.mark.parametrize('extra', [['--period', '2024-13'], ['--period', '2024-1'], ['--from-csv']])
def test_city_restore_cli_rejects_invalid_or_mixed_scope(extra):
    from scheduler import runner
    with pytest.raises(SystemExit):
        runner.main(['--restore-city-history', 'report.json', *extra])
