import json
import os
import time

from scheduler.snapshot_retention import cleanup


def attempt(tmp_path, age):
    p = tmp_path / "pipelines/ingest/sales/crawl_16888_city/data/snapshots/2026-10/tmp123"
    p.mkdir(parents=True)
    (p / "manifest.json").write_text(
        json.dumps({"chain": "city", "period": "2026-10", "status": "published", "pagination_complete": True})
    )
    (p / "city_2026-10.csv").write_text("raw")
    for f in [*p.iterdir(), p]:
        os.utime(f, (time.time() - age, time.time() - age))
    return p


def test_preview_and_evidence(tmp_path):
    p = attempt(tmp_path, 500000)
    result = cleanup(tmp_path)
    assert result["eligible"] == [str(p)] and p.exists()
    result = cleanup(tmp_path, apply=True)
    assert result["deleted"] == [str(p)] and not p.exists()
    assert list((tmp_path / "logs/snapshot_retention").glob("*.json"))


def test_boundary_protection_and_nested(tmp_path):
    p = attempt(tmp_path, 432000)
    now = max(f.stat().st_mtime for f in [p, *p.iterdir()]) + 432000
    assert not cleanup(tmp_path, now=now)["eligible"]
    assert not cleanup(tmp_path, now=now + 1, protected_roots=[p])["eligible"]
    (p / "unexpected").mkdir()
    assert not cleanup(tmp_path, now=now + 1)["eligible"]


def test_failed_capture(tmp_path):
    p = attempt(tmp_path, 500000)
    (p / "manifest.json").unlink()
    (p / "crawl.log").write_text("Traceback (most recent call last):\nRuntimeError: capture failed\n")
    for f in [p, *p.iterdir()]:
        os.utime(f, (1, 1))
    assert cleanup(tmp_path, apply=True)["deleted"] == [str(p)]
    receipt = json.loads(next((tmp_path / "logs/snapshot_retention").glob("*.json")).read_text())
    assert "capture failed" in receipt["manifest"]["error_tail"]


def test_new_file_prevents_cleanup(tmp_path):
    p = attempt(tmp_path, 500000)
    (p / "crawl.log").write_text("fresh")
    assert not cleanup(tmp_path)["eligible"]


def test_evidence_failure_preserves_downloads(tmp_path, monkeypatch):
    from pathlib import Path

    p = attempt(tmp_path, 500000)
    original = Path.open

    def fail(path, *args, **kwargs):
        if "snapshot_retention" in path.parts:
            raise PermissionError("receipt denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    assert cleanup(tmp_path, apply=True)["errors"]
    assert (p / "city_2026-10.csv").exists()


def test_reparse_file_is_rejected(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    p = attempt(tmp_path, 500000)
    original = Path.lstat
    target = p / "city_2026-10.csv"

    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=1024)
        return info

    monkeypatch.setattr(Path, "lstat", reparse)
    assert not cleanup(tmp_path, apply=True)["eligible"]
    assert target.exists()
