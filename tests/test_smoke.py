import asyncio
from contextlib import nullcontext
from datetime import date
from importlib import import_module
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from scheduler import runner, runtime
from scheduler.task_config import CHAINS, STEPS


def get_api(path):
    from backend.app.main import app

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_api_serves_root():
    response = get_api("/")
    assert response.status_code == 200
    assert "/health" in response.json()["message"]


@pytest.mark.parametrize("connected, status, state", [(True, 200, "ok"), (False, 503, "degraded")])
def test_health_reports_database_availability(monkeypatch, connected, status, state):
    from base_infra import db

    connection = Mock()
    if not connected:
        connection.execute.side_effect = OSError("simulated database failure")
    monkeypatch.setattr(db.engine, "connect", lambda: nullcontext(connection))

    response = get_api("/health")
    assert response.status_code == status
    assert response.json()["status"] == state
    assert response.json()["database"] == ("connected" if connected else "disconnected")
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"


@pytest.mark.parametrize("task", sorted({task for chain in CHAINS.values() for task in chain}))
def test_scheduled_job_imports_and_accepts_business_key(task):
    module, name = STEPS[task].split(":")
    job = getattr(import_module(f"scheduler.jobs.{module}"), name)
    signature(job).bind(business_key="2026-07")


@pytest.mark.parametrize(
    "args",
    [
        ["--chain", "daily", "--period", "2026-02-30"],
        ["--chain", "daily", "--from-csv"],
        ["--chain", "city", "--step", "load_stock_daily"],
        ["--period", "2026-07"],
    ],
)
def test_scheduler_rejects_invalid_cli_arguments(args):
    with pytest.raises(SystemExit) as exc:
        runner.main([*args, "--plan"])
    assert exc.value.code == 2


def test_daily_chain_skips_closed_market(monkeypatch):
    monkeypatch.setattr(runtime, "is_trade_day", lambda _: False)
    monkeypatch.setattr(runtime, "import_module", lambda _: pytest.fail("Closed market ran an ETL job"))
    assert runner.main(["--chain", "daily", "--period", "2026-07-04"]) == 0


def test_daily_failure_still_runs_independent_jobs_and_returns_nonzero(monkeypatch):
    calls = []

    def job(name, success=True):
        def execute(*, business_key):
            calls.append((name, business_key))
            return runtime.TaskResult(success=success)

        return execute

    jobs = SimpleNamespace(
        load_stock_daily=job("stock", False),
        load_futures_main_daily=job("futures"),
        load_ths_index_daily=job("index"),
        load_stock_finance=job("finance"),
    )
    monkeypatch.setattr(runtime, "is_trade_day", lambda day: day == date(2026, 7, 3))
    monkeypatch.setattr(runtime, "import_module", lambda _: jobs)
    assert runner.main(["--chain", "daily", "--period", "2026-07-03"]) == 1
    assert calls == [(name, "2026-07-03") for name in ("stock", "futures", "index", "finance")]


def test_daily_single_step_dispatches_only_requested_job(monkeypatch):
    from scheduler.jobs import load_index_ods_job

    monkeypatch.setattr(runtime, "is_trade_day", lambda _: True)
    ingest = Mock(return_value=True)
    monkeypatch.setattr(load_index_ods_job, "run_index_ingest", ingest)
    assert runner.main(["--chain", "daily", "--period", "2026-07-03", "--step", "load_index_daily"]) == 0
    ingest.assert_called_once()
    assert ingest.call_args.kwargs == {"dry_run": False, "index_filter": None}


@pytest.mark.parametrize(
    "chain, column, table",
    [
        ("style", "manufacturer", "ods_sales_monthly_model"),
        ("city", "city", "ods_sales_monthly_model_city"),
    ],
)
def test_monthly_ods_job_upserts_only_target_csv(monkeypatch, tmp_path, chain, column, table):
    from scheduler.jobs import load_ods_job

    loader = import_module(f"pipelines.ingest.sales.load_16888_{chain}_to_ods")
    connection = Mock()
    monkeypatch.setattr(loader, "engine", SimpleNamespace(begin=lambda: nullcontext(connection)))
    monkeypatch.setattr(load_ods_job, f"{chain}_data_dir", lambda: tmp_path)
    job = getattr(load_ods_job, f"load_{chain}_ods")

    (tmp_path / f"{chain}_2026-06.csv").write_text("invalid historical CSV", encoding="utf-8")
    with pytest.raises(runtime.SkipTask):
        job(business_key="2026-07")
    connection.execute.assert_not_called()

    target = tmp_path / f"{chain}_2026-07.csv"
    target.write_text(
        f'month,model_name,{column},rank,sales_volume\n2026-07, 示例车型 ,示例,1,"1,234"\n',
        encoding="utf-8-sig",
    )
    assert job(business_key="2026-07") is True
    connection.execute.assert_called_once()
    statement, rows = connection.execute.call_args.args
    assert str(statement).split()[:3] == ["INSERT", "INTO", table]
    assert "ON DUPLICATE KEY UPDATE" in str(statement)
    (row,) = rows
    assert row["stat_month"] == date(2026, 7, 1)
    assert row["model_name_raw"] == "示例车型"
    assert row["sales_volume"] == 1234
    assert row["source_file"] == str(target)


@pytest.mark.parametrize("chain, column", [("style", "manufacturer"), ("city", "city")])
@pytest.mark.parametrize("rows", ["", "2026-07,车型A,示例,1,100\n2026-06,车型B,示例,2,50\n"])
def test_monthly_ods_replay_rejects_empty_or_mixed_month_csv(monkeypatch, tmp_path, chain, column, rows):
    from scheduler.jobs import load_ods_job

    loader = import_module(f"pipelines.ingest.sales.load_16888_{chain}_to_ods")
    engine = Mock()
    engine.begin.return_value = nullcontext(Mock())
    monkeypatch.setattr(loader, "engine", engine)
    monkeypatch.setattr(load_ods_job, f"{chain}_data_dir", lambda: tmp_path)
    target = tmp_path / f"{chain}_2026-07.csv"
    target.write_text(f"month,model_name,{column},rank,sales_volume\n{rows}", encoding="utf-8-sig")

    job = getattr(load_ods_job, f"load_{chain}_ods")
    assert job(business_key="2026-07") is False
    engine.begin.assert_not_called()
