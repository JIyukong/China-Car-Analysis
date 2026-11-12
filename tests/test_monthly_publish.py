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
        'dim_oem (id INTEGER PRIMARY KEY, oem_name TEXT, company_id INTEGER, aliases TEXT, first_seen_month TEXT, last_seen_month TEXT, is_active INTEGER DEFAULT 1)',
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
    assert execute("SELECT manufacturer_name,oem_id,model_id FROM dwd_sales_monthly_model") == [('Renamed',1,1)]
    assert execute("SELECT oem_id,model_id FROM dwd_sales_monthly_model_city") == [(1,1)]


def test_publication_uses_company_effective_at_sale_month_end(warehouse):
    publish, execute = warehouse
    execute("UPDATE dim_oem SET company_id=2 WHERE id=1")
    execute("INSERT INTO bridge_brand_oem VALUES (1,1,0,'2026-01-31',1,'1900-01-01')")
    execute("INSERT INTO dim_energy_vehicle VALUES ('Model','新能源','SUV')")
    for month in ('2026-01', '2026-02'):
        add_source(execute, month, 10)
        publish.publish_month('style', month)
    assert execute('SELECT company_id FROM dwd_sales_monthly_model ORDER BY stat_month') == [(1,), (2,)]


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
