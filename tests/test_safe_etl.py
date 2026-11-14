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


@pytest.mark.parametrize(
    "script", ["scripts/init_database.py", "scripts/run_sql_file.py", "scripts/check_scheduler_heartbeat.py"]
)
def test_legacy_commands_fail_after_retirement(script):
    result = offline_cli(script, "db/ods/01_create_ods_sales_monthly.sql")
    assert result.returncode == 2
    assert "已停用" in result.stderr


def test_dry_run_lists_real_chains_without_db_dependencies():
    result = offline_cli("scripts/dry_run_scheduler.py")
    assert result.returncode == 0, result.stderr
    assert "style: 16888 采集 -> 来源对账 -> ODS -> 全国 DWD / DWS 发布" in result.stdout
    assert "city: 16888 采集 -> 来源对账 -> ODS -> 城市 DWD 发布" in result.stdout
    assert "load_stock_finance" in result.stdout


def test_csv_replay_has_an_explicit_month_and_skips_the_crawler():
    result = offline_cli("-m", "scheduler.runner", "--chain", "style", "--period", "2026-07", "--from-csv", "--plan")
    assert result.returncode == 0, result.stderr
    assert "2026-07" in result.stdout
    assert "CSV 与来源清单 -> 来源对账 -> ODS" in result.stdout
    assert "16888 采集" not in result.stdout


def test_legacy_chain_uses_guarded_runner(monkeypatch):
    from scheduler import runner, runtime

    def guarded(args):
        assert args == ["--chain", "style", "--period", "2026-07", "--from-csv"]
        return 1

    monkeypatch.setattr(runner, "main", guarded)
    assert runtime.run_chain("style", "2026-07", from_csv=True) is False


def test_warehouse_only_resume_requires_accepted_source(monkeypatch, tmp_path):
    from scheduler import runner, workflow

    state = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "State", lambda: state)
    monkeypatch.setattr(workflow, "publish_month", lambda *_: pytest.fail("resumed unverified ODS"))
    assert runner.main(["--chain", "city", "--period", "2026-07", "--resume"]) == 1


def test_scheduler_checks_monthly_sources_every_day_and_polls_feishu():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    now = datetime(2026, 11, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
    expected = {
        "style": "2026-11-11T03:00:00+08:00",
        "city": "2026-11-11T03:30:00+08:00",
        "load_stock_daily": "2026-11-11T23:30:00+08:00",
        "load_futures_daily": "2026-11-11T17:30:00+08:00",
        "load_index_daily": "2026-11-11T23:45:00+08:00",
        "load_stock_finance": "2026-11-11T18:30:00+08:00",
        "manual": "2026-11-11T00:00:00+08:00",
        "resume": "2026-11-11T00:00:00+08:00",
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


@pytest.mark.parametrize("current_brand", [1, 9])
def test_excel_refresh_preserves_existing_relationship_dates(current_brand):
    from datetime import date

    import pandas as pd

    from pipelines.dim.seed_dim_from_excel import refresh_bridge_brand_oem

    writes = []
    current = SimpleNamespace(brand_id=current_brand, effective_start_date=date(2025, 3, 1))

    class Connection:
        def execute(self, sql, params):
            if str(sql).lstrip().startswith("SELECT"):
                assert params["oem_id"] == 2
                return SimpleNamespace(one_or_none=lambda: current)
            writes.append(params)

    source = pd.DataFrame([{"品牌": "示例品牌", "主机厂名称": "示例厂商"}])
    args = (Connection(), source, {"示例品牌": (1, "示例品牌")}, {"示例厂商": (2, "示例厂商", 3, "示例公司")})
    if current_brand == 1:
        assert refresh_bridge_brand_oem(*args) == 1
        assert writes[0]["effective_start_date"] == "2025-03-01"
    else:
        with pytest.raises(ValueError, match="生效日期"):
            refresh_bridge_brand_oem(*args)
        assert not writes
