from scheduler.sales_source_rules import select_rows


def test_city_exact_identity_preserves_input_and_provenance():
    raw = {
        "model_name": "红旗H5",
        "price_range": "15.98—22.58",
        "city": "北京",
        "sales_volume": "7",
        "rank": "2",
        "source_id": "123",
    }
    selected, excluded = select_rows([raw], "city")
    assert selected == [dict(raw, model_name="22款红旗H5")]
    assert raw["model_name"] == "红旗H5"
    assert select_rows(selected, "city")[0] == selected
    assert not excluded


def test_city_other_identity_unchanged():
    rows = [
        {"model_name": "红旗H5", "price_range": "15.98-22.59"},
        {"model_name": "其他", "price_range": "15.98—22.58"},
    ]
    assert select_rows(rows, "city")[0] == rows


def test_duplicate_pages_normalizes_without_mutation():
    import pytest

    from scheduler.jobs.crawl_16888_job import _duplicate_pages

    rows = [
        dict(model_name="\u7ea2\u65d7H5", price_range=p, city="city", sales_volume="3", rank="1")
        for p in ["15.98\u201422.58", "15.98-24.98"]
    ]
    assert _duplicate_pages([rows], "city") == set()
    assert rows[0]["model_name"] == "\u7ea2\u65d7H5"
    with pytest.raises(ValueError):
        _duplicate_pages([[rows[0], dict(rows[0], sales_volume="4")]], "city")


def test_validated_csv_emits_identity_and_keeps_archive(tmp_path):
    import csv
    import hashlib
    import io
    import json

    from scheduler.monthly_source import _validated_csv

    rows = [
        dict(
            month="2023-03",
            model_name="\u7ea2\u65d7H5",
            price_range=p,
            city="city",
            sales_volume="3",
            rank="1",
            source_id=str(i),
        )
        for i, p in enumerate(["15.98\u201422.58", "15.98-24.98"])
    ]
    path = tmp_path / "city.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = path.read_bytes()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            dict(
                chain="city",
                period="2023-03",
                status="published",
                pagination_complete=True,
                source_total=2,
                page_rows=[2],
                csv_sha256=hashlib.sha256(before).hexdigest(),
            )
        ),
        encoding="utf-8",
    )
    selected = list(csv.DictReader(io.StringIO(_validated_csv(path, manifest, "city", "2023-03").decode("utf-8-sig"))))
    assert selected[0] == dict(rows[0], model_name="22\u6b3e\u7ea2\u65d7H5")
    assert selected[1] == rows[1]
    assert path.read_bytes() == before
