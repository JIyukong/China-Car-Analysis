"""City identity repair tests use only isolated in-memory SQLite."""

import pytest
from sqlalchemy import create_engine, text
from scheduler.reviewed_sales_revision import CITY_NEW, repair_city_identities

OLD = "\u7ea2\u65d7H5"


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE dim_city_model (id INTEGER PRIMARY KEY, source TEXT, source_model_name TEXT, model_name TEXT, model_code TEXT, review_status TEXT, energy_type TEXT, brand_code TEXT, oem_code TEXT, vehicle_type TEXT, evidence TEXT, national_candidates TEXT, first_seen_month TEXT, last_seen_month TEXT, is_active INTEGER)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE ods_sales_monthly_model_city (id INTEGER PRIMARY KEY, source TEXT, model_name_raw TEXT, stat_month TEXT, city_name_raw TEXT, price_range_raw TEXT, sales_volume INTEGER, row_hash TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE dwd_sales_monthly_city (id INTEGER PRIMARY KEY, source TEXT, model_name_raw TEXT, model_name TEXT, stat_month TEXT, city_name_raw TEXT, sales_volume INTEGER, row_hash TEXT, city_model_id INTEGER)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dim_city_model (id,source,source_model_name,model_name,review_status,energy_type,brand_code,oem_code,vehicle_type) VALUES (1,'16888_city',:old,:old,'confirmed',:fuel,'brand','oem','sedan')"
            ),
            dict(old=OLD, fuel="\u71c3\u6cb9"),
        )
        for i, (month, price, sales) in enumerate(
            [("2020-01", "15.98-22.58", 7), ("2024-04", "15.98\u201422.58", 9), ("2025-05", "15.98-24.98", 11)], 1
        ):
            params = dict(id=i, old=OLD, month=month, price=price, sales=sales)
            conn.execute(
                text(
                    "INSERT INTO ods_sales_monthly_model_city VALUES (:id,'16888_city',:old,:month,'city',:price,:sales,'original')"
                ),
                params,
            )
            conn.execute(
                text(
                    "INSERT INTO dwd_sales_monthly_city VALUES (:id,'16888_city',:old,:old,:month,'city',:sales,'original',1)"
                ),
                params,
            )
    yield engine
    engine.dispose()


def snapshot(conn):
    return {
        table: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY id"))]
        for table in ["dim_city_model", "ods_sales_monthly_model_city", "dwd_sales_monthly_city"]
    }


def test_city_repair_preserves_ids_sales_low_price_and_is_idempotent(database):
    with database.begin() as conn:
        report = repair_city_identities(conn)
        assert len(report["renamed_rows"]) == 2
        assert report["periods"] == ["2020-01", "2024-04"]
        for table in ["ods_sales_monthly_model_city", "dwd_sales_monthly_city"]:
            rows = list(conn.execute(text(f"SELECT id, model_name_raw, sales_volume FROM {table} ORDER BY id")))
            assert [tuple(row) for row in rows] == [(1, CITY_NEW, 7), (2, CITY_NEW, 9), (3, OLD, 11)]
        facts = list(conn.execute(text("SELECT city_model_id FROM dwd_sales_monthly_city ORDER BY id")).scalars())
        assert facts == [report["city_model_id"], report["city_model_id"], 1]
        before = snapshot(conn)
        assert repair_city_identities(conn)["renamed_rows"] == []
        assert snapshot(conn) == before


@pytest.mark.parametrize("failure", ["target", "sales"])
def test_city_repair_conflict_rolls_back_dimension_creation(database, failure):
    with database.begin() as conn:
        if failure == "target":
            conn.execute(
                text(
                    "INSERT INTO ods_sales_monthly_model_city VALUES (4,'16888_city',:name,'2020-01','city','15.98-22.58',7,'target')"
                ),
                dict(name=CITY_NEW),
            )
        else:
            conn.execute(text("UPDATE dwd_sales_monthly_city SET sales_volume=99 WHERE id=2"))
        before = snapshot(conn)
    with pytest.raises(ValueError):
        with database.begin() as conn:
            repair_city_identities(conn)
    with database.connect() as conn:
        assert snapshot(conn) == before
