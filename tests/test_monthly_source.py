import csv
import hashlib
import json
from contextlib import contextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest


class SimulatedODS:
    def __init__(self):
        self.rows = {}
        self.writes = 0

    @contextmanager
    def begin(self):
        yield self

    def execute(self, sql, params):
        if str(sql).lstrip().startswith("SELECT"):
            return self
        assert str(sql).lstrip().startswith("INSERT INTO ods_sales_monthly_model")
        assert "ON DUPLICATE KEY UPDATE" in str(sql)
        self.writes += 1
        for row in params:
            self.rows[(row["model_name_raw"], row["manufacturer_name_raw"])] = row

    def mappings(self):
        return self.rows.values()


def write_snapshot(path, rows, *, source_total=None, complete=True, status="published"):
    csv_path = path / "style_2026-07.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("month", "model_name", "manufacturer", "sales_volume"))
        writer.writerows(rows)
    (path / "manifest.json").write_text(
        json.dumps({
            "chain": "style",
            "period": "2026-07",
            "status": status,
            "pagination_complete": complete,
            "source_total": len(rows) if source_total is None else source_total,
            "page_rows": [len(rows)] if rows else [],
            "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        }),
        encoding="utf-8",
    )


@pytest.fixture
def source(monkeypatch, tmp_path):
    monthly = import_module("scheduler.monthly_source")
    loader = import_module("pipelines.ingest.sales.load_16888_style_to_ods")
    ods = SimulatedODS()
    monkeypatch.setattr(loader, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(loader, "engine", ods)
    return monthly, ods


def test_source_digest_detects_sales_revision_with_same_row_count(source, monkeypatch):
    monthly, ods = source
    source_rows = [["2026-07", "Model", "Maker", "5"]]
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, source_rows))

    first = monthly.collect_month("style", "2026-07")
    assert monthly.collect_month("style", "2026-07", accepted_digest=first) == first
    assert ods.writes == 1

    source_rows[0][3] = "6"
    revised = monthly.collect_month("style", "2026-07", accepted_digest=first)
    assert revised != first
    assert ods.rows[("Model", "Maker")]["sales_volume"] == 6
    assert ods.writes == 2


def test_accepted_digest_reconciles_missing_ods_row(source, monkeypatch):
    monthly, ods = source
    rows = [["2026-07", "Model", "Maker", "5"]]
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, rows))
    digest = monthly.collect_month("style", "2026-07")
    ods.rows.clear()

    assert monthly.collect_month("style", "2026-07", accepted_digest=digest) == digest
    assert ("Model", "Maker") in ods.rows


def test_ods_revision_check_detects_out_of_band_change(source, monkeypatch):
    monthly, ods = source
    rows = [["2026-07", "Model", "Maker", "5"]]
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, rows))
    digest = monthly.collect_month("style", "2026-07")
    monthly.verify_ods_revision("style", "2026-07", digest, connection=ods)

    ods.rows[("Model", "Maker")]["sales_volume"] = 9
    with pytest.raises(ValueError, match="ODS.*验收"):
        monthly.verify_ods_revision("style", "2026-07", digest, connection=ods)


@pytest.mark.parametrize(
    ("rows", "extra", "error"),
    [
        ([["2026-07", "Model", "Maker", "5"]], {"complete": False}, "分页"),
        ([["2026-07", "Model", "Maker", "5"]], {"source_total": 2}, "覆盖"),
        ([["2026-06", "Model", "Maker", "5"]], {}, "月份"),
        ([["2026-07", "", "Maker", "5"]], {}, "业务键"),
        ([["2026-07", "Model", "Maker", "--"]], {}, "销量"),
        ([["2026-07", "Model", "Maker", "-5"]], {}, "销量"),
        ([["2026-07", "Model", "Maker", "1.5"]], {}, "销量"),
        ([["2026-07", "Model", "Maker", "5"]] * 2, {}, "重复"),
    ],
)
def test_incomplete_or_invalid_raw_snapshot_cannot_write_ods(source, monkeypatch, tmp_path, rows, extra, error):
    monthly, ods = source
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, rows, **extra))

    with pytest.raises(ValueError, match=error):
        monthly.collect_month("style", "2026-07")

    assert ods.writes == 0
    assert len(list(tmp_path.rglob("style_2026-07.csv"))) == 1


def test_explicit_source_no_data_is_a_skip(source, monkeypatch):
    from scheduler.runtime import SkipTask

    monthly, ods = source
    monkeypatch.setattr(
        monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, [], status="unpublished")
    )
    with pytest.raises(SkipTask):
        monthly.collect_month("style", "2026-07")
    assert ods.writes == 0


def test_previously_accepted_month_becoming_empty_reports_source_withdrawal(source, monkeypatch):
    monthly, ods = source
    rows = [["2026-07", "Model", "Maker", "5"]]
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, rows))
    digest = monthly.collect_month("style", "2026-07")
    monkeypatch.setattr(
        monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, [], status="unpublished")
    )

    with pytest.raises(ValueError, match="已验收"):
        monthly.collect_month("style", "2026-07", accepted_digest=digest)
    assert ods.writes == 1


def test_withdrawn_keys_are_reported_and_retained(source, monkeypatch):
    monthly, ods = source
    source_rows = [["2026-07", "Model", "Maker", "5"], ["2026-07", "Older", "Maker", "1"]]
    monkeypatch.setattr(monthly, "_crawl_snapshot", lambda chain, period, path: write_snapshot(path, source_rows))
    digest = monthly.collect_month("style", "2026-07")
    source_rows.pop()

    with pytest.raises(ValueError, match="历史"):
        monthly.collect_month("style", "2026-07", accepted_digest=digest)

    assert ods.writes == 1
    assert ("Older", "Maker") in ods.rows


def test_csv_replay_requires_evidence_and_rejects_changed_bytes(source, tmp_path):
    monthly, ods = source
    rows = [["2026-07", "Model", "Maker", "0"]]
    write_snapshot(tmp_path, rows)
    manifest = tmp_path / "manifest.json"
    manifest.rename(tmp_path / "style_2026-07.manifest.json")
    csv_path = tmp_path / "style_2026-07.csv"
    csv_path.write_text(csv_path.read_text().replace(",0", ",9"), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256"):
        monthly.collect_month("style", "2026-07", from_csv=True)
    assert ods.writes == 0


def test_csv_replay_cannot_accept_bare_historical_file(source, tmp_path):
    monthly, ods = source
    (tmp_path / "style_2026-07.csv").write_text("month,model_name,manufacturer,sales_volume\n2026-07,A,B,1\n")

    with pytest.raises(ValueError, match="manifest"):
        monthly.collect_month("style", "2026-07", from_csv=True)
    assert ods.writes == 0


def test_capture_cannot_declare_completion_when_next_button_disappears_early(tmp_path):
    from scheduler.jobs import crawl_16888_job

    def goto(url, **kwargs):
        page.url = url
        return SimpleNamespace(ok=True)

    class Locator:
        def __init__(self, selector):
            self.selector = selector
            self.first = self

        def evaluate_all(self, _):
            assert self.selector == "input.xl-date-input"
            return ["2026-07", "2026-07"]

        def all_text_contents(self):
            assert self.selector == ".curr"
            return ["1"]

        def count(self):
            return 0

        def locator(self, selector):
            return Locator(selector)

    page = SimpleNamespace(goto=goto, locator=Locator)
    crawler = SimpleNamespace(
        PAGE_URL_TEMPLATE="https://xl.16888.com/style-{ym}-{ym}-{page}.html",
        PAGER_CONTAINER=".xl-data-pageing",
        NEXT_PAGE_BUTTON=".xl-data-pageing .next",
        CSV_COLUMNS=("month", "model_name", "manufacturer", "sales_volume"),
        get_total_count=lambda _: 2,
        extract_table_rows=lambda _: [{"model_name": "A", "manufacturer": "B", "sales_volume": "5"}],
    )

    with pytest.raises(RuntimeError, match="覆盖"):
        crawl_16888_job._capture_pages(page, crawler, "style", "2026-07", tmp_path)

    assert "2026-07,A,B,5" in (tmp_path / "style_2026-07.csv").read_text(encoding="utf-8-sig")
    assert not (tmp_path / "manifest.json").exists()
