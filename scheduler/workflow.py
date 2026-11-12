"""Durable state and recovery for a single CarApp scheduler host."""

import json
import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from scheduler.runtime import SkipTask

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")
MONTHLY = ("style", "city")
STATE_DIR = Path(__file__).resolve().parents[1] / "logs" / "etl"


def timestamp() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


class State:
    def __init__(self, directory: Path = STATE_DIR):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS batch (
                    chain TEXT NOT NULL, period TEXT NOT NULL, status TEXT NOT NULL,
                    digest TEXT, published_digest TEXT, published_at TEXT,
                    updated_at TEXT NOT NULL, message TEXT NOT NULL, gaps TEXT NOT NULL,
                    recheck INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chain, period)
                );
                CREATE TABLE IF NOT EXISTS feishu_ticket (
                    kind TEXT NOT NULL, entity TEXT NOT NULL, record_id TEXT NOT NULL,
                    expected TEXT NOT NULL, notified TEXT, revision TEXT, result TEXT, reason TEXT,
                    PRIMARY KEY (kind, entity), UNIQUE (record_id)
                );
                CREATE TABLE IF NOT EXISTS service_status (
                    task TEXT PRIMARY KEY, status TEXT NOT NULL,
                    updated_at TEXT NOT NULL, message TEXT NOT NULL
                );
            """)

    @contextmanager
    def transaction(self):
        conn = sqlite3.connect(self.directory / "state.sqlite3", timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def lock(self, resource: str, timeout: float = 0) -> FileLock:
        return FileLock(self.directory / f"{resource}.lock", timeout=timeout)

    def batch(self, chain: str, period: str) -> dict:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM batch WHERE chain=? AND period=?", (chain, period)).fetchone()
        return dict(row) if row else {}

    def batches(self, chain: str | None = None) -> list[dict]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM batch WHERE (? IS NULL OR chain=?) ORDER BY chain, period DESC",
                (chain, chain),
            ).fetchall()
        return [dict(row) for row in rows]

    def save(
        self, chain: str, period: str, status: str, *,
        digest: str | None = None, message: str = "", gaps: list[dict] | None = None,
    ) -> None:
        now = timestamp()
        with self.transaction() as conn:
            conn.execute("""
                INSERT INTO batch (chain,period,status,digest,published_digest,published_at,updated_at,message,gaps)
                VALUES (?,?,?,?,?,?,?,?,COALESCE(?,'[]'))
                ON CONFLICT(chain,period) DO UPDATE SET status=excluded.status,
                    digest=COALESCE(excluded.digest,batch.digest),
                    published_digest=COALESCE(excluded.published_digest,batch.published_digest),
                    published_at=COALESCE(excluded.published_at,batch.published_at),
                    updated_at=excluded.updated_at, message=excluded.message,
                    gaps=COALESCE(?,batch.gaps),
                    recheck=CASE WHEN excluded.status IN ('published','waiting_manual') THEN 0 ELSE batch.recheck END
            """, (
                chain, period, status, digest, digest if status in {"published", "ods_accepted"} else None,
                now if status in {"published", "ods_accepted"} else None, now, message,
                json.dumps(gaps, ensure_ascii=False) if gaps is not None else None,
                json.dumps(gaps, ensure_ascii=False) if gaps is not None else None,
            ))

    def enqueue(self, chain: str, period: str) -> None:
        with self.transaction() as conn:
            conn.execute("""
                INSERT INTO batch (chain, period, status, updated_at, message, gaps)
                VALUES (?, ?, 'waiting_source', ?, '', '[]')
                ON CONFLICT(chain,period) DO UPDATE SET recheck=1,
                    status=CASE WHEN batch.digest IS NOT NULL AND batch.status NOT IN
                        ('collecting','waiting_source','source_error') THEN 'ready' ELSE batch.status END,
                    updated_at=excluded.updated_at
            """, (chain, period, timestamp()))

    def gaps(self) -> list[dict]:
        grouped = {}
        with self.transaction() as conn:
            resolved = {
                (row["kind"], row["entity"]) for row in conn.execute(
                    "SELECT kind, entity FROM feishu_ticket WHERE result='not_applicable'"
                )
            }
        for row in self.batches():
            for gap in json.loads(row["gaps"]):
                kind, key = gap["kind"], gap["key"]
                if (kind, key) in resolved:
                    continue
                item = grouped.setdefault((kind, key), {"fields": set(), "periods": set()})
                item["fields"].update(gap["fields"])
                item["periods"].add(row["period"])
        return [
            {"kind": kind, "key": key, "fields": sorted(item["fields"]), "periods": sorted(item["periods"])}
            for (kind, key), item in sorted(grouped.items())
        ]

    def service(self, task: str, status: str, message: str = "") -> None:
        with self.transaction() as conn:
            conn.execute("""
                INSERT INTO service_status VALUES (?, ?, ?, ?)
                ON CONFLICT(task) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                    message=excluded.message
            """, (task, status, timestamp(), message))

    def export(self) -> None:
        with self.lock("summary", timeout=15):
            with self.transaction() as conn:
                services = [dict(row) for row in conn.execute("SELECT * FROM service_status ORDER BY task")]
            rows = self.batches()
            latest = {}
            outstanding = []
            overdue = []
            today = datetime.now(TZ).date()
            for row in rows:
                chain, period = row["chain"], row["period"]
                if row["published_at"]:
                    latest.setdefault(chain, period)
                if row["status"] not in ("published", "ods_accepted", "closed"):
                    outstanding.append({
                        name: row[name] for name in ("chain", "period", "status", "message", "updated_at")
                    })
                    due = date.fromisoformat(period + "-01") if chain in MONTHLY else date.fromisoformat(period)
                    if chain in MONTHLY:
                        next_month = (due.replace(day=28) + timedelta(days=4)).replace(day=1)
                        due = next_month.replace(day=15 if chain == "style" else 25)
                    if due < today:
                        overdue.append({"chain": chain, "period": period})
            summary = {
                "generated_at": timestamp(), "latest_validated_period": latest,
                "outstanding": outstanding, "overdue": overdue, "manual_gaps": self.gaps(),
                "services": services,
            }
            with tempfile.NamedTemporaryFile(
                dir=self.directory, mode="w", encoding="utf-8", delete=False,
            ) as output:
                path = Path(output.name)
                json.dump(summary, output, ensure_ascii=False, indent=2)
            try:
                os.replace(path, self.directory / "status.json")
            finally:
                path.unlink(missing_ok=True)


def target_months(state: State, chain: str, today: date) -> list[str]:
    last = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    batches = state.batches(chain)
    periods = {last, *(row["period"] for row in batches if row["status"] != "published")}
    if batches:
        cursor = date.fromisoformat(batches[0]["period"] + "-01")
        while cursor.strftime("%Y-%m") < last:
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            periods.add(cursor.strftime("%Y-%m"))
    return sorted(periods)


def collect_month(*args, **kwargs):
    from scheduler.monthly_source import collect_month as collect

    return collect(*args, **kwargs)


def publish_month(chain: str, period: str, digest: str) -> list[dict]:
    from scheduler.monthly_publish import publish_month as publish

    return publish(chain, period, source_digest=digest)


def process_month(
    state: State, chain: str, period: str, *, from_csv: bool = False, resume: bool = False,
    prefer_resume: bool = False,
) -> bool:
    try:
        with state.lock(f"collect-{chain}"):
            batch = state.batch(chain, period)
            can_resume = batch.get("status") in {"ready", "publish_error"} and bool(batch.get("digest"))
            resume = resume or (prefer_resume and can_resume)
            if resume:
                if not can_resume:
                    return True
                digest = batch["digest"]
            else:
                state.save(chain, period, "collecting")
                try:
                    digest = collect_month(chain, period, from_csv=from_csv, accepted_digest=batch.get("digest"))
                except SkipTask as exc:
                    state.save(chain, period, "waiting_source", message=str(exc))
                    return True
                except Exception as exc:
                    logger.exception("%s %s source validation failed", chain, period)
                    state.save(chain, period, "source_error", message=str(exc)[:400])
                    return False
                state.save(chain, period, "ready", digest=digest)
            with state.lock("warehouse", timeout=120):
                if (
                    not resume and not from_csv and batch.get("status") == "published"
                    and digest == batch.get("published_digest") and not state.batch(chain, period)["recheck"]
                ):
                    with state.transaction() as conn:
                        conn.execute(
                            "UPDATE batch SET status='published', message='Source revision unchanged'"
                            " WHERE chain=? AND period=?",
                            (chain, period),
                        )
                    return True
                return _publish(state, chain, period, digest)
    except Timeout:
        logger.info("%s %s already being processed", chain, period)
        return True
    finally:
        state.export()


def _publish(state: State, chain: str, period: str, digest: str) -> bool:
    from scheduler.monthly_source import SourceRevisionChanged

    try:
        gaps = publish_month(chain, period, digest)
    except SourceRevisionChanged as exc:
        state.save(chain, period, "source_error", message=str(exc))
        return False
    except Exception as exc:
        gaps = getattr(exc, "gaps", None)
        if gaps is not None:
            state.save(chain, period, "waiting_manual", gaps=gaps, message=str(exc)[:400])
            return True
        logger.exception("%s %s warehouse publication failed", chain, period)
        state.save(chain, period, "publish_error", message=str(exc)[:400])
        return False
    state.save(chain, period, "published", digest=digest, gaps=gaps)
    if chain == "style" and state.batch("city", period).get("status") == "waiting_manual":
        state.enqueue("city", period)
    return True


def resume_ready(state: State) -> bool:
    now = datetime.now(TZ)
    results = []
    for chain in MONTHLY:
        for row in reversed(state.batches(chain)):
            retry_due = row["status"] == "publish_error" and (
                now - datetime.fromisoformat(row["updated_at"]) >= timedelta(minutes=30)
            )
            if row["status"] == "ready" or retry_due:
                results.append(process_month(state, chain, row["period"], resume=True))
    return all(results)
