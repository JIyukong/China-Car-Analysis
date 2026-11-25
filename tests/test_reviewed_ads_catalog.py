import json

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def catalog_db():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE dim_brand (id INTEGER PRIMARY KEY, brand_code TEXT UNIQUE, brand_name TEXT UNIQUE, brand_origin_country TEXT, is_joint_venture_brand INT)'))
        conn.execute(text('CREATE TABLE dim_oem (id INTEGER PRIMARY KEY, oem_name TEXT, company_id INT)'))
        conn.execute(text("INSERT INTO dim_oem VALUES (80,'广汽埃安',7),(76,'东风汽车',14),(39,'奇瑞捷豹路虎',12)"))
        conn.execute(text('CREATE TABLE dim_model (id INTEGER PRIMARY KEY, model_code TEXT UNIQUE, model_name TEXT, brand_id INT, brand_name TEXT, oem_id INT, oem_name TEXT, energy_type TEXT, vehicle_type TEXT, first_seen_month TEXT, last_seen_month TEXT)'))
        conn.execute(text('CREATE TABLE dim_energy_vehicle (id INTEGER PRIMARY KEY, model_name TEXT UNIQUE, energy_type TEXT, vehicle_type TEXT, source_file TEXT)'))
        conn.execute(text('''CREATE TRIGGER model_classification AFTER INSERT ON dim_model BEGIN
            UPDATE dim_model SET energy_type=COALESCE((SELECT energy_type FROM dim_energy_vehicle WHERE model_name=NEW.model_name),'占位'),
            vehicle_type=COALESCE((SELECT vehicle_type FROM dim_energy_vehicle WHERE model_name=NEW.model_name),'占位') WHERE id=NEW.id;
            END'''))
        yield conn


def test_verified_catalog_is_incremental_and_retains_no_sales_dates(catalog_db):
    from scheduler.reviewed_ads_catalog import seed
    first = seed(catalog_db)
    assert first['inserted_models'] == 4
    assert first['inserted_brands'] == 3
    assert first['inserted_classifications'] == 4
    assert seed(catalog_db)['inserted_models'] == 0
    assert catalog_db.execute(text('SELECT COUNT(*) FROM dim_model WHERE first_seen_month IS NOT NULL OR last_seen_month IS NOT NULL')).scalar() == 0
    assert catalog_db.execute(text("SELECT brand_origin_country,is_joint_venture_brand FROM dim_brand WHERE brand_name='神行者'")).one() == ('GB', 1)


def test_existing_identity_conflict_is_rejected(catalog_db):
    from scheduler.reviewed_ads_catalog import seed
    seed(catalog_db)
    catalog_db.execute(text("UPDATE dim_model SET oem_id=39 WHERE model_name='启境GT7'"))
    with pytest.raises(ValueError, match='conflict'):
        seed(catalog_db)


def test_existing_classification_conflict_is_rejected(catalog_db):
    from scheduler.reviewed_ads_catalog import seed
    catalog_db.execute(text("INSERT INTO dim_energy_vehicle (model_name,energy_type,vehicle_type) VALUES ('启境GT7','燃油','轿车')"))
    with pytest.raises(ValueError, match='classification conflict'):
        seed(catalog_db)


def test_alignment_exception_requires_exact_verified_triplet():
    from scheduler.reviewed_ads_catalog import verified_identity
    assert verified_identity('启境GX7', '启境', '广汽埃安')
    assert not verified_identity('启境GX7', '埃安', '广汽埃安')
    assert not verified_identity('启境GX8', '启境', '广汽埃安')
    assert not verified_identity('启境GX7', '启境', '奇瑞捷豹路虎')


def test_checker_accepts_verified_brand_but_rejects_unverified_mismatch(catalog_db):
    from scheduler.reviewed_ads_catalog import seed
    from scripts.data_quality_check import check_dim_model_brand_alignment
    seed(catalog_db)
    catalog_db.execute(text('CREATE TABLE bridge_brand_oem (oem_id INT, brand_id INT, is_current INT, effective_end_date TEXT)'))
    catalog_db.execute(text("INSERT INTO bridge_brand_oem VALUES (80,999,1,'9999-12-31'),(76,999,1,'9999-12-31'),(39,999,1,'9999-12-31')"))
    assert check_dim_model_brand_alignment(catalog_db)
    catalog_db.execute(text("UPDATE dim_model SET model_name='未核实车型' WHERE model_name='启境GX7'"))
    assert not check_dim_model_brand_alignment(catalog_db)


def test_official_evidence_rejects_partial_or_wrong_producer(tmp_path):
    from scheduler.reviewed_ads_catalog import verify_evidence
    payload = {'handleResult': {'respCode': 200}, 'countResult': {'total': 2},
               'cpList': [{'qymc': '广汽埃安新能源汽车股份有限公司', 'cpsb': '启境牌'}]}
    (tmp_path / 'ads-miit-qijing.json').write_text(json.dumps(payload), encoding='utf8')
    with pytest.raises(ValueError, match='complete'):
        verify_evidence(tmp_path)
