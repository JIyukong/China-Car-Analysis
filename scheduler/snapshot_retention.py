"""Bounded SALES snapshot retention; caller must protect active chains with locks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

RETENTION_SECONDS = 120 * 60 * 60


def run_daily(state, project_root, *, force=False):
    from contextlib import ExitStack
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from filelock import Timeout

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    try:
        with ExitStack() as locks:
            for resource in ("snapshot-retention", "collect-city", "collect-style"):
                locks.enter_context(state.lock(resource, timeout=0))
            with state.transaction() as conn:
                previous = conn.execute(
                    "SELECT status,updated_at FROM service_status WHERE task='snapshot_retention'"
                ).fetchone()
            if not force and previous and previous["status"] == "ok" and previous["updated_at"][:10] == today:
                return {"skipped": "already cleaned today", "errors": []}
            result = cleanup(project_root, apply=True)
            state.service(
                "snapshot_retention", "error" if result["errors"] else "ok", json.dumps(result, ensure_ascii=False)
            )
            state.export()
            return result
    except Timeout:
        return {"skipped": "active sales collection or cleanup", "errors": []}


def _safe(path, root):
    if not path.is_relative_to(root):
        return False
    for part in [path, *path.parents]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                return False
        if part == root:
            break
    return path.resolve().is_relative_to(root.resolve())


def cleanup(project_root, *, apply=False, protected_roots=(), now=None):
    """Preview by default. Hold chain locks throughout apply, or protect their roots.

    The only disposable hierarchy is fixed SALES data/snapshots/YYYYMM/attempt.
    Evidence is durable before any unlink. Exceptions leave remaining files intact.
    """
    root = Path(project_root).absolute()
    now = time.time() if now is None else now
    protected = [Path(p).absolute() for p in protected_roots]
    result = {"eligible": [], "deleted": [], "skipped": [], "errors": [], "bytes": 0}
    for chain in ("city", "style"):
        base = root / f"pipelines/ingest/sales/crawl_16888_{chain}/data/snapshots"
        if not _safe(base, root) or not base.is_dir():
            continue
        for period in base.iterdir():
            if (
                not re.fullmatch(r"\d{4}-?(?:0[1-9]|1[0-2])", period.name)
                or not _safe(period, root)
                or not period.is_dir()
            ):
                continue
            for folder in period.iterdir():
                try:
                    if not _safe(folder, root) or not folder.is_dir():
                        continue
                    if any(folder == p or folder.is_relative_to(p) or p.is_relative_to(folder) for p in protected):
                        result["skipped"].append(str(folder))
                        continue
                    files = list(folder.iterdir())
                    if not files or any(not _safe(f, root) or not f.is_file() for f in files):
                        result["skipped"].append(str(folder))
                        continue
                    newest = max(f.stat().st_mtime for f in [folder, *files])
                    if now - newest <= RETENTION_SECONDS:
                        continue
                    manifest_path = folder / "manifest.json"
                    allowed = {
                        "manifest.json",
                        "crawl.log",
                        f"{chain}_{period.name}.csv",
                        f"{chain}_{period.name}.json",
                    }
                    if any(
                        f.name not in allowed
                        and not any(word in f.name.lower() for word in ("error", "review", "exclusion"))
                        for f in files
                    ):
                        result["skipped"].append(str(folder))
                        continue
                    if manifest_path.exists():
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    else:
                        log = (folder / "crawl.log").read_text(encoding="utf-8", errors="replace")[-4000:]
                        last = log.rstrip().splitlines()[-1] if log.strip() else ""
                        if not re.match(r"(?:[\w.]+(?:Error|Exception)|KeyboardInterrupt|SystemExit):(?: .*)?$", last):
                            result["skipped"].append(str(folder))
                            continue
                        manifest = {"chain": chain, "period": period.name, "status": "failed", "error_tail": log}
                    if (
                        manifest.get("chain") != chain
                        or manifest.get("period") != period.name
                        or manifest.get("status") not in ("published", "unpublished", "failed")
                    ):
                        result["skipped"].append(str(folder))
                        continue
                    evidence = {"path": str(folder), "manifest": manifest, "time": now, "files": []}
                    for f in files:
                        raw = f.read_bytes()
                        evidence["files"].append(
                            {"name": f.name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                        )
                        if any(word in f.name.lower() for word in ("error", "review", "exclusion")):
                            evidence.setdefault("review_evidence", {})[f.name] = raw.decode("utf-8")
                    result["eligible"].append(str(folder))
                    if not apply:
                        continue
                    archive = root / "logs/snapshot_retention"
                    if not _safe(archive, root):
                        raise ValueError("unsafe evidence destination")
                    archive.mkdir(parents=True, exist_ok=True)
                    receipt = archive / (hashlib.sha256(str(folder).encode()).hexdigest() + ".json")
                    if not _safe(receipt, root):
                        raise ValueError("unsafe evidence file")
                    with receipt.open("w", encoding="utf-8") as stream:
                        json.dump(evidence, stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if not _safe(folder, root) or set(folder.iterdir()) != set(files):
                        raise ValueError("snapshot changed")
                    for f, record in zip(files, evidence["files"], strict=True):
                        if not _safe(f, root) or hashlib.sha256(f.read_bytes()).hexdigest() != record["sha256"]:
                            raise ValueError("snapshot changed")
                    for f in files:
                        f.unlink()
                    folder.rmdir()
                    result["deleted"].append(str(folder))
                    result["bytes"] += sum(f["size"] for f in evidence["files"])
                except (OSError, ValueError, UnicodeError) as exc:
                    result["errors"].append({"path": str(folder), "error": str(exc)})
    return result
