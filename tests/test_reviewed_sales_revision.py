import hashlib
import json

import pytest


def test_recover_city_manifest_requires_complete_pages_and_known_failure(tmp_path):
    from scheduler.reviewed_sales_revision import recover_city_manifest

    p = tmp_path / "city_2023-03.csv"
    p.write_text(
        "month,rank,model_name,city,sales_volume,level,price_range\n2023-03,1,X,A,1,SUV,1-2\n", encoding="utf-8"
    )
    log = tmp_path / "crawl.log"
    log.write_text(
        "city 2023-03 第 1 页 1 行，累计 1/1\nValueError: 来源重复业务键内容冲突: ('红旗H5', '长春')\n",
        encoding="utf-8",
    )
    result = recover_city_manifest(p, "2023-03")
    assert result["source_total"] == 1 and result["page_rows"] == [1]
    assert result["csv_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    log.write_text("city 2023-03 第 1 页 1 行，累计 1/2\nTimeoutError", encoding="utf-8")
    with pytest.raises(ValueError):
        recover_city_manifest(p, "2023-03")


def test_national_review_is_exact_period_and_preserves_original_fact_id():
    from sqlalchemy import create_engine, text

    from scheduler.reviewed_sales_revision import repair_national_identity

    db = create_engine("sqlite://")
    with db.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE dim_model(id INTEGER PRIMARY KEY,model_code TEXT,model_name TEXT,brand_id INTEGER,oem_id INTEGER,energy_type TEXT,vehicle_type TEXT)"
            )
        )
        conn.execute(text("INSERT INTO dim_model VALUES(437,'OLD','哈弗枭龙MAX',130,110,'新能源','SUV')"))
        conn.execute(
            text(
                "CREATE TABLE dim_energy_vehicle(id INTEGER PRIMARY KEY,model_name TEXT,energy_type TEXT,vehicle_type TEXT,source_file TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE ods_sales_monthly_model(id INTEGER PRIMARY KEY,source TEXT,stat_month TEXT,model_name_raw TEXT,manufacturer_name_raw TEXT,sales_volume INTEGER,row_hash TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE dwd_sales_monthly_model(id INTEGER PRIMARY KEY,source TEXT,stat_month TEXT,model_name TEXT,model_name_raw TEXT,model_id INTEGER,oem_id INTEGER,sales_volume INTEGER,row_hash TEXT)"
            )
        )
        for i, month in enumerate(("2026-09", "2026-10"), 1):
            conn.execute(
                text(
                    "INSERT INTO ods_sales_monthly_model VALUES(:id,'16888_style',:month,'哈弗枭龙MAX','长城汽车',60,'original')"
                ),
                {"id": i, "month": month + "-01"},
            )
            conn.execute(
                text(
                    "INSERT INTO dwd_sales_monthly_model VALUES(:id,'16888_style',:month,'哈弗枭龙MAX','哈弗枭龙MAX',437,110,60,'original')"
                ),
                {"id": i, "month": month},
            )
        changed = repair_national_identity(conn)
        assert changed["changed"] is True
        assert (
            conn.execute(text("SELECT model_name_raw FROM ods_sales_monthly_model WHERE id=1")).scalar_one()
            == "哈弗枭龙MAX"
        )
        assert conn.execute(
            text("SELECT id,model_name,sales_volume FROM dwd_sales_monthly_model WHERE id=2")
        ).one() == (2, "长城H6L 新能源", 60)
        assert repair_national_identity(conn)["changed"] is False
        conn.execute(text("UPDATE dwd_sales_monthly_model SET sales_volume=61 WHERE id=2"))
        with pytest.raises(ValueError, match="ODS与DWD"):
            repair_national_identity(conn)


def test_review_spec_rejects_unapproved_period(tmp_path):
    from scheduler.reviewed_sales_revision import load_spec

    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"style": {"period": "2025-10"}, "city": []}))
    with pytest.raises(ValueError):
        load_spec(p)
