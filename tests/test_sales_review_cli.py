from pathlib import Path

import pytest


def test_review_and_cleanup_plans_are_offline(monkeypatch):
    from scheduler import runner
    from scheduler.cli import admin_commands

    monkeypatch.setattr(admin_commands, "require_etl_account", lambda p: pytest.fail("plan checked credentials"))
    spec = Path(__file__).resolve().parents[1] / "docs/evidence/sales-source-review-2026-11-28.json"
    assert runner.main(["--apply-sales-source-review", str(spec), "--plan"]) == 0
    assert runner.main(["--cleanup-sales-snapshots", "--plan"]) == 0
    with pytest.raises(SystemExit):
        runner.main(["--cleanup-sales-snapshots", "--chain", "city"])


def test_daily_retention_job_does_not_request_startup_catchup(monkeypatch):
    from scheduler import runner

    scheduler = runner.create_scheduler()
    job = scheduler.get_job("snapshot-retention")
    assert job is not None
    assert "hour='2'" in str(job.trigger)
