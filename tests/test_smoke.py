import asyncio
from contextlib import nullcontext
from datetime import date
from importlib import import_module
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from scheduler import runner
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


def test_closed_market_never_calls_provider(monkeypatch, tmp_path):
    from scheduler import market_workflow
    from scheduler.workflow import State

    day = date(2026, 7, 4)
    monkeypatch.setattr(market_workflow, "expected_sessions", lambda *_: ({day: set()}, {day: set()}))
    monkeypatch.setattr(market_workflow, "ingest_daily", lambda *_: pytest.fail("Closed market ran a provider"))
    state = State(tmp_path)
    assert market_workflow.process_market(state, "load_stock_daily", day)
    assert state.batch("load_stock_daily", str(day))["status"] == "closed"


def test_daily_failure_still_runs_independent_jobs_and_returns_nonzero(monkeypatch, tmp_path):
    from scheduler import market_workflow, workflow

    calls = []

    def execute(state, task, day):
        calls.append((task, day))
        return task != "load_stock_daily"

    state = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "State", lambda: state)
    monkeypatch.setattr(market_workflow, "process_market", execute)
    assert runner.main(["--chain", "daily", "--period", "2026-07-03"]) == 1
    assert calls == [(task, date(2026, 7, 3)) for task in CHAINS["daily"]]


def test_daily_single_step_dispatches_only_requested_job(monkeypatch, tmp_path):
    from scheduler import market_workflow, workflow

    state = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "State", lambda: state)
    run = Mock(return_value=True)
    monkeypatch.setattr(market_workflow, "process_market", run)
    assert runner.main(["--chain", "daily", "--period", "2026-07-03", "--step", "load_index_daily"]) == 0
    run.assert_called_once_with(state, "load_index_daily", date(2026, 7, 3))


def _write_monthly_ods_manifest(target, chain, source_total):
    import hashlib
    import json

    target.with_suffix(".manifest.json").write_text(json.dumps({
        "chain": chain,
        "period": "2026-07",
        "status": "published",
        "pagination_complete": True,
        "source_total": source_total,
        "page_rows": [source_total],
        "csv_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }), encoding="utf-8")


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
    engine = Mock()
    engine.begin.return_value = nullcontext(connection)
    monkeypatch.setattr(loader, "engine", engine)
    monkeypatch.setattr(loader, "data_dir", lambda: tmp_path)
    job = getattr(load_ods_job, f"load_{chain}_ods")

    (tmp_path / f"{chain}_2026-06.csv").write_text("invalid historical CSV", encoding="utf-8")
    assert job() is False
    assert job(business_key="2026-07") is False

    target = tmp_path / f"{chain}_2026-07.csv"
    target.write_text(
        f'month,model_name,{column},rank,sales_volume\n2026-07, 示例车型 ,示例,1,"1,234"\n',
        encoding="utf-8-sig",
    )
    assert job(business_key="2026-07") is False
    engine.begin.assert_not_called()

    _write_monthly_ods_manifest(target, chain, 1)
    key_column = "manufacturer_name_raw" if chain == "style" else "city_name_raw"
    persisted = {"model_name_raw": "示例车型", key_column: "示例", "rank_no": 1,
                 "price_range_raw": None, "sales_volume": 1234, "level_raw": None}
    connection.execute.side_effect = [
        SimpleNamespace(mappings=lambda: []), None, SimpleNamespace(mappings=lambda: [persisted]),
    ]
    assert job(business_key="2026-07") is True
    engine.begin.assert_called_once()
    reads_and_write = connection.execute.call_args_list
    assert len(reads_and_write) == 3
    statement, rows = reads_and_write[1].args
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
    monkeypatch.setattr(loader, "data_dir", lambda: tmp_path)
    target = tmp_path / f"{chain}_2026-07.csv"
    target.write_text(f"month,model_name,{column},rank,sales_volume\n{rows}", encoding="utf-8-sig")
    _write_monthly_ods_manifest(target, chain, 2 if rows else 1)

    job = getattr(load_ods_job, f"load_{chain}_ods")
    assert job(business_key="2026-07") is False
    engine.begin.assert_not_called()
