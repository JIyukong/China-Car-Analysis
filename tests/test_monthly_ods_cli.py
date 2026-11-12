import runpy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_ENTRY_POINTS = [
    ("pipelines/ingest/sales/load_16888_style_to_ods.py", [], "style"),
    ("pipelines/ingest/sales/load_16888_city_to_ods.py", [], "city"),
    ("scheduler/jobs/load_ods_job.py", ["--chain", "city"], "city"),
]


@pytest.fixture(autouse=True)
def forbid_database_access(monkeypatch):
    from base_infra.db import engine

    def fail(*args, **kwargs):
        pytest.fail("Standalone monthly ODS entry point bypassed the guarded runner")

    monkeypatch.setattr(engine, "begin", fail)
    monkeypatch.setattr(engine, "connect", fail)


@pytest.mark.parametrize("entry, options, chain", _ENTRY_POINTS)
def test_monthly_ods_cli_delegates_one_explicit_period(monkeypatch, entry, options, chain):
    from scheduler import runner

    calls = []

    def guarded_run(args):
        calls.append(args)
        return 1

    monkeypatch.setattr(runner, "main", guarded_run)
    monkeypatch.setattr(sys, "argv", [entry, *options, "--period", "2026-07"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / entry), run_name="__main__")

    assert calls == [["--chain", chain, "--period", "2026-07", "--from-csv"]]
    assert exc.value.code == 1


@pytest.mark.parametrize("entry, options, chain", _ENTRY_POINTS)
@pytest.mark.parametrize("replay", ["all", "file", "period_and_file"])
def test_monthly_ods_cli_rejects_legacy_full_or_arbitrary_file_replay(
    monkeypatch, tmp_path, capsys, entry, options, chain, replay,
):
    args = [entry, *options]
    if replay == "period_and_file":
        args.extend(["--period", "2026-07"])
    if replay != "all":
        target = tmp_path / "any.csv"
        target.write_text("month,model_name,manufacturer,city,sales_volume\n2026-07,Model,Maker,City,5\n", encoding="utf-8")
        args.extend(["--file", str(target)])
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / entry), run_name="__main__")

    assert exc.value.code == 2
    guidance = capsys.readouterr().err
    if entry.startswith("scheduler/"):
        chain = "{style,city}"
    assert f"python -m scheduler.runner --chain {chain} --period YYYY-MM --from-csv" in guidance
