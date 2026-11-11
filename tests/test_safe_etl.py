import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def offline_cli(*args):
    # -S hides third-party packages. The offline paths must run without a DB driver or credentials.
    return subprocess.run(
        [sys.executable, "-S", "-X", "utf8", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
    )


@pytest.mark.parametrize("script", ["scripts/init_database.py", "scripts/run_sql_file.py"])
def test_database_admin_commands_are_retired(script):
    result = offline_cli(script, "db/ods/01_create_ods_sales_monthly.sql")
    assert result.returncode == 2
    assert "已停用" in result.stderr


def test_dry_run_lists_real_chains_without_db_dependencies():
    result = offline_cli("scripts/dry_run_scheduler.py")
    assert result.returncode == 0, result.stderr
    assert "crawl_16888_style_monthly -> load_style_ods -> build_sales_dimensions" in result.stdout
    assert "crawl_16888_city_monthly -> load_city_ods -> run_dwd_city_clean" in result.stdout
    assert "load_stock_finance" in result.stdout


def test_csv_replay_has_an_explicit_month_and_skips_the_crawler():
    result = offline_cli("-m", "scheduler.runner", "--chain", "style", "--period", "2026-07", "--from-csv", "--plan")
    assert result.returncode == 0, result.stderr
    assert "2026-07" in result.stdout
    assert "load_style_ods -> build_sales_dimensions" in result.stdout
    assert "crawl_16888_style_monthly" not in result.stdout


def test_etl_failure_stops_the_monthly_chain(monkeypatch):
    from scheduler import runtime

    calls = []

    def load(*, business_key):
        calls.append(("load_style_ods", business_key))
        return True

    def dimensions(*, business_key):
        calls.append(("build_dimensions", business_key))
        return False

    jobs = SimpleNamespace(load_style_ods=load, build_dimensions=dimensions)
    monkeypatch.setattr(runtime, "import_module", lambda _: jobs)
    assert runtime.run_chain("style", "2026-07", from_csv=True) is False
    assert calls == [("load_style_ods", "2026-07"), ("build_dimensions", "2026-07")]


def test_city_etl_can_continue_from_already_loaded_ods(monkeypatch):
    from scheduler import runtime

    calls = []

    def crawl(*, business_key):
        calls.append(("crawl", business_key))
        raise runtime.AlreadyLoaded()

    def load(*, business_key):
        calls.append(("load", business_key))

    def clean(*, business_key):
        calls.append(("clean", business_key))

    jobs = SimpleNamespace(crawl_16888_city_monthly=crawl, load_city_ods=load, run_dwd_city_clean=clean)
    monkeypatch.setattr(runtime, "import_module", lambda _: jobs)
    assert runtime.run_chain("city", "2026-07") is True
    assert calls == [("crawl", "2026-07"), ("clean", "2026-07")]


def test_scheduler_registers_original_cron_times_without_starting_jobs():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    now = datetime(2026, 11, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    expected = {
        "style": "2026-11-12T03:00:00+08:00",
        "city": "2026-11-20T03:30:00+08:00",
        "load_stock_daily": "2026-11-11T17:30:00+08:00",
        "load_futures_daily": "2026-11-11T17:30:00+08:00",
        "load_index_daily": "2026-11-11T17:45:00+08:00",
        "load_stock_finance": "2026-11-11T18:30:00+08:00",
    }
    assert {job.id: job.trigger.get_next_fire_time(None, now).isoformat() for job in scheduler.get_jobs()} == expected


@pytest.mark.parametrize("dimension", ["dim_area", "dim_energy_vehicle", "dim_model_ads_manual"])
def test_manual_refresh_releases_only_its_temporary_table_on_error(dimension):
    from pipelines.dim.manual_refresh import upsert_dimension

    statements = []

    class Connection:
        def execute(self, sql, rows=None):
            statement = str(sql)
            statements.append(statement)
            if statement.startswith(f"INSERT INTO {dimension} "):
                raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        upsert_dimension(Connection(), dimension, [{"source_file": "test.csv"}])
    assert statements[0] == f"CREATE TEMPORARY TABLE tmp_manual_{dimension} LIKE stg_{dimension}"
    assert "ON DUPLICATE KEY UPDATE" in statements[2]
    assert statements[-1] == f"DROP TEMPORARY TABLE IF EXISTS tmp_manual_{dimension}"


def test_operational_sources_contain_no_destructive_table_statements():
    paths = [*ROOT.glob("pipelines/**/*.py"), *ROOT.glob("scheduler/**/*.py"), *ROOT.glob("scripts/*.py")]
    paths.extend((ROOT / "db").rglob("*.sql"))
    destructive = re.compile(
        r"^\s*(?:(?:\w+\.)*execute\(text\([\x22']\s*)?"
        r"(?:DROP\s+TABLE|TRUNCATE\s+TABLE|DELETE\s+(?:FROM|\w+\s*$)|REPLACE\s+INTO)\b",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    violations = [str(path.relative_to(ROOT)) for path in paths if destructive.search(path.read_text(encoding="utf-8"))]
    assert not violations
