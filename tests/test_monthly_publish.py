from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def warehouse(monkeypatch):
    from scheduler import monthly_publish as publish

    db = create_engine('sqlite://')
    schema = [
        'dim_company (id INTEGER PRIMARY KEY, company_code TEXT, company_name TEXT)',
        'dim_brand (id INTEGER PRIMARY KEY, brand_code TEXT, brand_name TEXT)',
        'dim_oem (id INTEGER PRIMARY KEY, oem_name TEXT, oem_code TEXT, company_id INTEGER, aliases TEXT, first_seen_month TEXT, last_seen_month TEXT, is_active INTEGER DEFAULT 1)',
        'bridge_brand_oem (oem_id INTEGER, brand_id INTEGER, is_current INTEGER, effective_end_date TEXT, company_id INTEGER, effective_start_date TEXT)',
        'dim_model (id INTEGER PRIMARY KEY, model_code TEXT UNIQUE, model_name TEXT, brand_id INTEGER, brand_name TEXT, oem_id INTEGER, oem_name TEXT, energy_type TEXT, vehicle_type TEXT, ads_id INTEGER, ads_supplier_name TEXT, first_seen_month TEXT, last_seen_month TEXT, is_active INTEGER DEFAULT 1)',
        'dim_energy_vehicle (model_name TEXT, energy_type TEXT, vehicle_type TEXT)',
        'dim_area (id INTEGER PRIMARY KEY, city_short_name TEXT)',
        'dim_vehicle_type (id INTEGER PRIMARY KEY)',
        'dim_vehicle_subtype (id INTEGER PRIMARY KEY, subtype_name TEXT, vehicle_type_id INTEGER)',
    ]
    ods = 'id INTEGER PRIMARY KEY, stat_month TEXT, source TEXT, model_name_raw TEXT, sales_volume INTEGER, price_range_raw TEXT, rank_no INTEGER, row_hash TEXT'
    dwd = 'id INTEGER PRIMARY KEY, stat_month TEXT, source TEXT, model_name TEXT, model_name_raw TEXT, model_id INTEGER, oem_id INTEGER, brand_id INTEGER, company_id INTEGER, ads_id INTEGER, ads_supplier_name TEXT, sales_volume INTEGER, price_min NUMERIC, price_max NUMERIC, rank_no INTEGER, row_hash TEXT UNIQUE'
    schema += [
        f'ods_sales_monthly_model ({ods}, manufacturer_name_raw TEXT)',
        f'ods_sales_monthly_model_city ({ods}, city_name_raw TEXT, level_raw TEXT)',
        f'dwd_sales_monthly_model ({dwd}, manufacturer_name TEXT, UNIQUE(stat_month, model_id))',
        f'dwd_sales_monthly_model_city ({dwd}, city_name TEXT, city_name_raw TEXT, area_id INTEGER, subtype_name TEXT, level_raw TEXT, vehicle_subtype_id INTEGER, vehicle_type_id INTEGER)',
    ]
    schema += [
        '''dim_city_model (id INTEGER PRIMARY KEY, source TEXT, source_model_name TEXT,
        model_code TEXT UNIQUE, model_name TEXT, brand_code TEXT, brand_name TEXT,
        oem_code TEXT, oem_name TEXT, production_type TEXT, import_channel TEXT,
        energy_type TEXT, vehicle_type TEXT, review_status TEXT, evidence TEXT,
        national_candidates TEXT, first_seen_month TEXT, last_seen_month TEXT,
        is_active INTEGER DEFAULT 1, UNIQUE(source,source_model_name))''',
        '''dwd_sales_monthly_city (id INTEGER PRIMARY KEY, stat_month TEXT, source TEXT,
        model_name TEXT, model_name_raw TEXT, city_model_id INTEGER, sales_volume INTEGER,
        price_min NUMERIC, price_max NUMERIC, rank_no INTEGER, row_hash TEXT UNIQUE,
        city_name TEXT, city_name_raw TEXT, area_id INTEGER, subtype_name TEXT,
        level_raw TEXT, vehicle_subtype_id INTEGER, vehicle_type_id INTEGER)''',
    ]
    schema += [
        f'dws_sales_monthly_{group} (id INTEGER PRIMARY KEY, stat_month TEXT, {group}_id INTEGER, {group}_name TEXT, total_sales INTEGER, market_share NUMERIC, yoy_growth NUMERIC, mom_growth NUMERIC, is_current INTEGER, UNIQUE(stat_month, {group}_id))'
        for group in ('brand', 'company')
    ]

    def execute(sql, **values):
        with db.begin() as conn:
            result = conn.execute(text(sql), values)
            return result.all() if result.returns_rows else None

    for table in schema:
        execute(f'CREATE TABLE {table}')
    execute("INSERT INTO dim_company VALUES (1,'C','Company'), (2,'C2','Company 2')")
    execute("INSERT INTO dim_brand VALUES (1,'B','Brand'), (2,'B2','Brand 2')")
    execute("INSERT INTO dim_oem (id,oem_name,company_id) VALUES (1,'OEM',1)")
    execute("INSERT INTO bridge_brand_oem VALUES (1,1,1,'9999-12-31',1,'1900-01-01')")

    @contextmanager
    def offline_transaction():
        with db.begin() as conn:
            yield conn

    monkeypatch.setattr(publish, '_publication_transaction', offline_transaction)
    monkeypatch.setattr(publish, 'engine', db)
    yield publish, execute
    db.dispose()


def enable_city_wide(monkeypatch, execute):
    from base_infra.config import get_settings
    monkeypatch.setattr(get_settings(), 'ETL_CITY_WIDE_ENABLED', True)
    execute("INSERT INTO dim_area VALUES (1,'City')")
    execute("INSERT INTO dim_vehicle_type VALUES (1)")
    execute("INSERT INTO dim_vehicle_subtype VALUES (1,'SUV',1)")


def test_city_replay_skips_unchanged_fact_updates(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, 'Extra(进口)', city=True)
    publish.publish_month('city', '2026-01')
    original = publish._update
    writes = []
    def track(conn, table, rows, columns):
        if table == 'dwd_sales_monthly_city':
            writes.extend(rows)
        return original(conn, table, rows, columns)
    monkeypatch.setattr(publish, '_update', track)
    publish.publish_month('city', '2026-01')
    assert writes == []


def test_city_wide_publishes_unknown_models_without_national_or_company(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, 'Extra(进口)', city=True)
    gaps = publish.publish_month('city', '2026-01')
    assert [(g['kind'], g['key']) for g in gaps] == [('city_model', 'Extra(进口)')]
    assert execute('SELECT model_name,production_type,import_channel,review_status,oem_name FROM dim_city_model') == [
        ('Extra(进口)', 'imported', 'unknown', 'pending', None)]
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_city') == [(7,)]
    assert execute('SELECT id FROM dwd_sales_monthly_model_city') == []
    assert execute('SELECT id FROM dim_model') == []
    publish.publish_month('city', '2026-01')
    assert execute('SELECT COUNT(*) FROM dim_city_model') == [(1,)]
    assert execute('SELECT COUNT(*) FROM dwd_sales_monthly_city') == [(1,)]


def test_city_wide_import_identity_and_review_are_not_overwritten(warehouse, monkeypatch):
    from pipelines.dim.city_model import apply_city_model_review
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 8, 'Model', city=True)
    add_source(execute, '2026-01', 2, 'Model(进口)', city=True)
    add_source(execute, '2026-01', 10)
    publish.publish_month('city', '2026-01')
    assert execute('SELECT COUNT(*) FROM dim_city_model') == [(2,)]
    values = dict(brand_code='B', brand_name='Brand', oem_code='OVERSEAS',
                  oem_name='Overseas', production_type='imported', import_channel='unknown',
                  energy_type='燃油', vehicle_type='SUV', evidence='人工核实的来源凭据')
    with publish.engine.begin() as conn:
        apply_city_model_review(conn, 'Model(进口)', values)
    publish.publish_month('city', '2026-01')
    assert execute("SELECT oem_name,review_status FROM dim_city_model WHERE source_model_name='Model(进口)'") == [('Overseas', 'confirmed')]
    assert execute('SELECT SUM(sales_volume) FROM dwd_sales_monthly_city') == [(10,)]
    with publish.engine.begin() as conn, pytest.raises(ValueError, match='进口'):
        apply_city_model_review(conn, 'Model(进口)', {**values, 'production_type': 'domestic'})


def test_city_wide_failed_area_gate_preserves_facts_but_discovers_candidates(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, city=True)
    publish.publish_month('city', '2026-01')
    execute('UPDATE ods_sales_monthly_model_city SET sales_volume=99')
    execute("UPDATE dim_area SET city_short_name='Elsewhere'")
    with pytest.raises(publish.ManualRequired):
        publish.publish_month('city', '2026-01')
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_city') == [(7,)]


def test_city_wide_national_hints_are_not_confirmed_ownership(warehouse, monkeypatch):
    import json
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 10)
    add_source(execute, '2026-01', 7, city=True)
    publish.publish_month('city', '2026-01')
    hints, status, oem = execute('SELECT national_candidates,review_status,oem_name FROM dim_city_model')[0]
    assert json.loads(hints) == [{'oem_name': 'OEM', 'brand_code': 'B', 'brand_name': 'Brand'}]
    assert (status, oem) == ('pending', None)
    # No company table or national model table is needed by the new city path.
    execute('ALTER TABLE dim_company RENAME TO unused_company')
    # dim_model is now an explicit reference dependency.
    publish.publish_month('city', '2026-01')
    assert execute('SELECT SUM(sales_volume) FROM dwd_sales_monthly_city') == [(7,)]


def test_city_wide_history_missing_keys_keep_previous_facts(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, city=True)
    publish.publish_month('city', '2026-01')
    execute("UPDATE ods_sales_monthly_model_city SET model_name_raw='Changed', row_hash='changed'")
    with pytest.raises(publish.ReconciliationError, match='业务键'):
        publish.publish_month('city', '2026-01')
    assert execute('SELECT model_name,sales_volume FROM dwd_sales_monthly_city') == [('Model', 7)]
    assert execute("SELECT id FROM dim_city_model WHERE source_model_name='Changed'") == []


def test_city_review_only_enqueues_city_months(warehouse):
    publish, execute = warehouse
    add_source(execute, '2026-01', 10)
    add_source(execute, '2026-01', 7, city=True)
    assert publish.affected_months('city_model', 'Model') == [('city', '2026-01')]


def test_city_wide_lifecycle_uses_city_history(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, 'Old', city=True)
    add_source(execute, '2026-06', 4, 'New', city=True)
    publish.publish_month('city', '2026-06')
    publish.publish_month('city', '2026-01')
    assert execute('SELECT model_name,first_seen_month,last_seen_month,is_active FROM dim_city_model ORDER BY model_name') == [
        ('New', '2026-06-01', '2026-06-01', 1), ('Old', '2026-01-01', '2026-01-01', 0)]


def test_city_wide_view_retains_pending_sales_and_imports(warehouse, monkeypatch):
    from pathlib import Path

    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 8, 'Model', city=True)
    add_source(execute, '2026-01', 2, 'Model(进口)', city=True)
    publish.publish_month('city', '2026-01')
    ddl = (Path(__file__).resolve().parents[1] / 'db/migrations/009_city_model_wide.sql').read_text(encoding='utf-8')
    view = ddl[ddl.index('CREATE OR REPLACE VIEW'):].replace('CREATE OR REPLACE VIEW', 'CREATE VIEW', 1)
    execute(view)
    assert execute('SELECT brand_name,SUM(sales_volume) FROM vw_sales_monthly_city_wide GROUP BY brand_name') == [('归属待确认', 10)]
    assert execute("SELECT SUM(sales_volume) FROM vw_sales_monthly_city_wide WHERE production_type='imported'") == [(2,)]


def test_city_wide_corrupted_fact_write_rolls_back_revision(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 7, city=True)
    publish.publish_month('city', '2026-01')
    execute('''CREATE TRIGGER corrupt_city AFTER UPDATE OF sales_volume ON dwd_sales_monthly_city
        BEGIN UPDATE dwd_sales_monthly_city SET sales_volume=1 WHERE id=NEW.id; END''')
    execute('UPDATE ods_sales_monthly_model_city SET sales_volume=99')
    with pytest.raises(publish.ReconciliationError, match='落库校验'):
        publish.publish_month('city', '2026-01')
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_city') == [(7,)]


def test_city_wide_discovers_zero_sales_models_without_zero_facts(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-01', 0, 'Zero(进口)', city=True)
    result = publish.publish_month_result('city', '2026-01')
    assert result.dwd_rows == 0
    assert result.gaps[0]['key'] == 'Zero(进口)'
    assert 'national_candidates' in result.gaps[0]['context']
    assert execute('SELECT model_name,is_active FROM dim_city_model') == [('Zero(进口)', 0)]
    assert execute('SELECT COUNT(*) FROM dwd_sales_monthly_city') == [(0,)]


def add_source(execute, period, sales, name='Model', *, city=False):
    suffix = '_city' if city else ''
    fields = ',city_name_raw,level_raw' if city else ',manufacturer_name_raw'
    labels = ",'City','SUV'" if city else ",'OEM'"
    execute(f"""INSERT INTO ods_sales_monthly_model{suffix}
        (stat_month, source, model_name_raw, sales_volume, row_hash{fields})
        VALUES (:month, :source, :name, :sales, :key{labels})""",
        month=period + '-01', source='16888_city' if city else '16888_style',
        name=name, sales=sales, key=f'{period}/{name}/{suffix}')


def test_style_revisions_update_business_key_and_future_rates(warehouse):
    publish, execute = warehouse
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    for month, sales in [('2025-12', 100), ('2026-01', 150), ('2026-12', 200)]:
        add_source(execute, month, sales)
        assert publish.publish_month('style', month) == []
    original_id, = execute("SELECT id FROM dwd_sales_monthly_model WHERE stat_month='2025-12'")[0]
    execute("UPDATE ods_sales_monthly_model SET sales_volume=50, row_hash='corrected' WHERE stat_month='2025-12-01'")
    publish.publish_month('style', '2025-12')
    assert execute("SELECT id,sales_volume,row_hash FROM dwd_sales_monthly_model WHERE stat_month='2025-12'") == [(original_id, 50, 'corrected')]
    assert execute('SELECT stat_month,total_sales,mom_growth,yoy_growth FROM dws_sales_monthly_brand ORDER BY stat_month') == [
        ('2025-12-01', 50, None, None), ('2026-01-01', 150, 200, None), ('2026-12-01', 200, None, 300),
    ]
    assert execute('SELECT first_seen_month,last_seen_month,is_active FROM dim_model') == [
        ('2025-12-01', '2026-12-01', 1),
    ]


def test_reviewed_source_rename_preserves_existing_dimension_identity(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_oem SET oem_name='长城汽车' WHERE id=1")
    execute("INSERT INTO dim_model (id,model_name,oem_id,brand_id) VALUES (10,'哈弗大狗',1,1)")
    execute("INSERT INTO dim_energy_vehicle VALUES ('哈弗大狗','燃油','SUV')")
    add_source(execute, '2026-07', 5760, name='哈弗大狗')
    execute("UPDATE ods_sales_monthly_model SET manufacturer_name_raw='长城汽车'")
    publish.publish_month('style', '2026-07')
    original_id = execute('SELECT id FROM dwd_sales_monthly_model')[0][0]
    execute("UPDATE ods_sales_monthly_model SET model_name_raw='长城大狗',row_hash='renamed-source'")
    publish.publish_month('style', '2026-07')
    assert execute('SELECT id,model_name,model_name_raw FROM dwd_sales_monthly_model') == [(original_id, '哈弗大狗', '长城大狗')]
    assert execute('SELECT model_id,oem_id,sales_volume FROM dwd_sales_monthly_model') == [(10, 1, 5760)]
    assert execute('SELECT COUNT(*) FROM dim_model') == [(1,)]
    from datetime import date
    with publish.engine.connect() as conn:
        matches = publish._dimensions(conn, 'style', date(2026, 7, 1))
    assert next(iter(matches.values()))[0]['energy_type'] == '燃油'


def test_reviewed_rename_derives_missing_dim_with_canonical_name(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_oem SET oem_name='长城汽车' WHERE id=1")
    add_source(execute, '2026-07', 5760, name='长城大狗')
    execute("UPDATE ods_sales_monthly_model SET manufacturer_name_raw='长城汽车'")
    publish.publish_month('style', '2026-07')
    assert execute('SELECT model_name FROM dim_model') == [('哈弗大狗',)]
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_model') == [(5760,)]


def test_city_reviewed_rename_updates_same_fact_without_national_dimensions(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    add_source(execute, '2026-06', 10, name='哈弗大狗', city=True)
    publish.publish_month('city', '2026-06')
    original_id = execute('SELECT id FROM dwd_sales_monthly_city')[0][0]
    execute("UPDATE ods_sales_monthly_model_city SET model_name_raw='长城大狗',row_hash='city-renamed'")
    publish.publish_month('city', '2026-06')
    assert execute('SELECT id,model_name_raw,sales_volume FROM dwd_sales_monthly_city') == [(original_id, '长城大狗', 10)]
    assert execute('SELECT COUNT(*) FROM dim_model') == [(0,)]


def test_missing_dimensions_leave_last_good_publication_and_city_has_own_gates(warehouse):
    publish, execute = warehouse
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    add_source(execute, '2026-01', 10)
    add_source(execute, '2026-01', 7, city=True)
    publish.publish_month('style', '2026-01')
    with pytest.raises(publish.ManualRequired) as error:
        publish.publish_month('city', '2026-01')
    assert {(gap['kind'], gap['key']) for gap in error.value.gaps} == {('area', 'City'), ('subtype', 'SUV')}
    assert execute('SELECT total_sales FROM dws_sales_monthly_company') == [(10,)]
    assert execute('SELECT id FROM dwd_sales_monthly_model_city') == []
    execute("UPDATE dim_energy_vehicle SET vehicle_type='占位'")
    assert {'kind': 'energy', 'key': 'Model', 'fields': ['vehicle_type']} in publish.publish_month('style', '2026-01')
    execute('UPDATE dim_oem SET company_id=3')
    execute('UPDATE ods_sales_monthly_model SET sales_volume=50')
    with pytest.raises(publish.ManualRequired) as error:
        publish.publish_month('style', '2026-01')
    assert error.value.gaps == [{'kind': 'ownership', 'key': 'OEM', 'fields': ['company_code']}]
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_model') == [(10,)]


def test_oem_alias_resolves_existing_oem_name_in_both_sales_chains(warehouse):
    publish, execute = warehouse
    execute("""UPDATE dim_oem SET aliases='["Renamed"]' WHERE id=1""")
    execute("INSERT INTO dim_oem (id,oem_name,company_id) VALUES (2,'Renamed',1)")
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    add_source(execute, '2026-01', 10)
    execute("UPDATE ods_sales_monthly_model SET manufacturer_name_raw='Renamed'")
    publish.publish_month('style', '2026-01')
    execute("""INSERT INTO dim_model (id,model_name,oem_id,brand_id)
               VALUES (10,'Model',2,1)""")
    add_source(execute, '2026-01', 7, city=True)
    execute("INSERT INTO dim_area VALUES (1,'City')")
    execute("INSERT INTO dim_vehicle_type VALUES (1)")
    execute("INSERT INTO dim_vehicle_subtype VALUES (1,'SUV',1)")
    publish.publish_month('city', '2026-01')
    assert execute("SELECT manufacturer_name,oem_id,model_id FROM dwd_sales_monthly_model") == [('OEM',1,1)]
    assert execute("SELECT manufacturer_name_raw FROM ods_sales_monthly_model") == [('Renamed',)]
    assert execute("SELECT oem_id,model_id FROM dwd_sales_monthly_model_city") == [(1,1)]
    original = execute("SELECT id FROM dwd_sales_monthly_model")
    execute("UPDATE dwd_sales_monthly_model SET manufacturer_name='Renamed'")
    publish.publish_month('style', '2026-01')
    assert execute("SELECT id FROM dwd_sales_monthly_model") == original
    assert execute("SELECT manufacturer_name FROM dwd_sales_monthly_model") == [('OEM',)]


def test_publication_uses_company_effective_at_sale_month_end(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_oem SET company_id=2 WHERE id=1")
    execute("INSERT INTO bridge_brand_oem VALUES (1,1,0,'2026-01-31',1,'1900-01-01')")
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    for month in ('2026-01', '2026-02'):
        add_source(execute, month, 10)
        publish.publish_month('style', month)
    assert execute('SELECT company_id FROM dwd_sales_monthly_model ORDER BY stat_month') == [(1,), (2,)]


def test_reviewed_existing_display_alias_keeps_fact_id_on_republication(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_oem SET oem_name='长城汽车' WHERE id=1")
    add_source(execute, '2026-10', 10, '长城大狗')
    execute("UPDATE ods_sales_monthly_model SET manufacturer_name_raw='长城汽车'")
    execute("INSERT INTO dim_model (id,model_name,oem_id,brand_id) VALUES (1,'哈弗大狗',1,1)")
    publish.publish_month('style','2026-10')
    original=execute('SELECT id,sales_volume FROM dwd_sales_monthly_model')
    execute("UPDATE dwd_sales_monthly_model SET model_name='长城大狗'")
    publish.publish_month('style','2026-10')
    assert execute('SELECT id,sales_volume FROM dwd_sales_monthly_model')==original
    assert execute('SELECT model_name FROM dwd_sales_monthly_model')==[('哈弗大狗',)]


def test_research_bucket_preserves_market_totals_without_operating_growth(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_company SET company_code='OUT_OF_RESEARCH',company_name='非研究范围车企（改名）' WHERE id=1")
    for month, sales in [('2025-01', 5), ('2026-01', 10), ('2026-02', 20)]:
        add_source(execute, month, sales)
        publish.publish_month('style', month)
    assert execute('SELECT total_sales,yoy_growth,mom_growth FROM dws_sales_monthly_company ORDER BY stat_month') == [
        (5, None, None), (10, None, None), (20, None, None)]
    assert execute('SELECT SUM(sales_volume) FROM dwd_sales_monthly_model') == [(35,)]
    assert execute("SELECT market_share FROM dws_sales_monthly_company WHERE stat_month='2026-02-01'") == [(100,)]
    # Revising a baseline must not recreate operating growth on future bucket rows.
    execute("UPDATE ods_sales_monthly_model SET sales_volume=12 WHERE stat_month='2026-01-01'")
    publish.publish_month('style', '2026-01')
    assert execute("SELECT yoy_growth,mom_growth FROM dws_sales_monthly_company WHERE stat_month='2026-02-01'") == [(None, None)]


def test_regrouped_totals_keep_old_groups_out_of_publication(warehouse):
    publish, execute = warehouse
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    add_source(execute, '2026-01', 10)
    publish.publish_month('style', '2026-01')
    execute('UPDATE dim_oem SET company_id=2')
    execute('UPDATE dim_model SET brand_id=2')
    publish.publish_month('style', '2026-01')
    for group in ('brand', 'company'):
        assert execute(f'SELECT {group}_id,total_sales,is_current FROM dws_sales_monthly_{group} ORDER BY {group}_id') == [(1, 10, 0), (2, 10, 1)]


def test_republication_does_not_rewrite_legacy_ads_columns(warehouse):
    publish, execute = warehouse
    add_source(execute, "2026-01", 10)
    publish.publish_month("style", "2026-01")
    execute("UPDATE dwd_sales_monthly_model SET ads_id=1, ads_supplier_name='Old'")
    publish.publish_month("style", "2026-01")
    assert execute("SELECT ads_id,ads_supplier_name FROM dwd_sales_monthly_model") == [(1, "Old")]


def test_dws_verification_failure_rolls_back_dwd_revision(warehouse):
    publish, execute = warehouse
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    add_source(execute, '2026-01', 10)
    publish.publish_month('style', '2026-01')
    execute('''CREATE TRIGGER corrupt_group AFTER UPDATE OF total_sales ON dws_sales_monthly_brand
        BEGIN UPDATE dws_sales_monthly_brand SET total_sales = 1 WHERE id = NEW.id; END''')
    execute('UPDATE ods_sales_monthly_model SET sales_volume=50')
    with pytest.raises(publish.ReconciliationError):
        publish.publish_month('style', '2026-01')
    assert execute('SELECT sales_volume FROM dwd_sales_monthly_model') == [(10,)]
    assert execute('SELECT total_sales FROM dws_sales_monthly_brand') == [(10,)]


def test_ownership_affected_months_covers_both_dimension_and_raw_models(warehouse):
    publish, execute = warehouse
    execute("INSERT INTO dim_model (model_name,oem_id) VALUES ('Dim Model',1)")
    add_source(execute, '2026-01', 10, ' Raw Model ')
    add_source(execute, '2025-12', 2, 'Raw Model', city=True)
    add_source(execute, '2026-01', 2, ' Dim Model ', city=True)
    add_source(execute, '2026-02', 2, 'OEM', city=True)
    add_source(execute, '2026-03', 2, 'Raw Model SE', city=True)
    assert publish.affected_months('ownership', 'OEM') == [
        ('city', '2025-12'), ('city', '2026-01'), ('city', '2026-02'), ('style', '2026-01'),
    ]


def test_city_model_auto_reference_is_unique_complete_and_idempotent(warehouse, monkeypatch):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    execute("UPDATE dim_oem SET oem_code='OEM_CODE'")
    execute("INSERT INTO dim_model (id,model_code,model_name,brand_id,oem_id,energy_type,vehicle_type) VALUES (1,'M','Model',1,1,'燃油','SUV')")
    add_source(execute, '2026-01', 7, city=True)
    assert publish.publish_month('city', '2026-01') == []
    assert execute('SELECT brand_code,oem_code,energy_type,review_status,production_type FROM dim_city_model') == [('B','OEM_CODE','燃油','confirmed','unknown')]
    execute("UPDATE dim_model SET energy_type='新能源'")
    publish.publish_month('city', '2026-01')
    assert execute('SELECT energy_type FROM dim_city_model') == [('燃油',)]


@pytest.mark.parametrize('ambiguous', [False, True])
def test_city_model_reference_incomplete_or_ambiguous_stays_in_feishu_gaps(warehouse, monkeypatch, ambiguous):
    publish, execute = warehouse
    enable_city_wide(monkeypatch, execute)
    execute("UPDATE dim_oem SET oem_code='OEM_CODE'")
    execute("INSERT INTO dim_model (id,model_code,model_name,brand_id,oem_id,energy_type,vehicle_type) VALUES (1,'M','Model',1,1,'占位','SUV')")
    if ambiguous:
        execute("UPDATE dim_model SET energy_type='燃油'")
        execute("INSERT INTO dim_model (id,model_code,model_name,brand_id,oem_id,energy_type,vehicle_type) VALUES (2,'M2','Model',2,1,'燃油','SUV')")
    add_source(execute, '2026-01', 7, city=True)
    gaps = publish.publish_month('city', '2026-01')
    assert [(g['kind'],g['key']) for g in gaps] == [('city_model','Model')]
    assert execute('SELECT review_status FROM dim_city_model') == [('conflict' if ambiguous else 'pending',)]
