"""Feishu submission polling and durable notification receipts."""

import hashlib
import json
import logging
from uuid import UUID

from filelock import Timeout

from scheduler.workflow import State, timestamp

logger = logging.getLogger(__name__)


def _tickets(state: State) -> list[dict]:
    with state.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM feishu_ticket ORDER BY kind, entity")]


def _bind(state: State, gap: dict, record_id: str) -> None:
    with state.transaction() as conn:
        conn.execute("""
            INSERT INTO feishu_ticket (kind, entity, record_id, expected) VALUES (?, ?, ?, ?)
            ON CONFLICT(kind,entity) DO UPDATE SET record_id=excluded.record_id, expected=excluded.expected,
                notified=CASE WHEN record_id=excluded.record_id THEN notified END
        """, (gap["kind"], gap["key"], record_id, json.dumps(gap["fields"], ensure_ascii=False)))


def _submit(state: State, client, ticket: dict, record: dict) -> None:
    from scheduler import feishu
    from scheduler.monthly_publish import affected_months

    fields = record["fields"]
    if feishu.cell_text(fields, feishu.STATUS) != feishu.SUBMITTED:
        return
    kind, key, record_id = ticket["kind"], ticket["entity"], ticket["record_id"]
    editable = (feishu.KIND, feishu.BUSINESS_KEY, feishu.TASK_KEY, feishu.INPUT)
    submitted = [feishu.cell_text(fields, name) for name in editable]
    revision = hashlib.sha256(json.dumps(submitted, ensure_ascii=False).encode()).hexdigest()
    result, reason = ticket["result"], ticket["reason"]
    if revision != ticket["revision"]:
        with state.lock("warehouse", timeout=10):
            try:
                change = feishu.submission(record, kind=kind, key=key, expected_fields=json.loads(ticket["expected"]))
                periods = affected_months(kind, key) if not change.not_applicable else []
                feishu.apply_submission(change)
            except ValueError as exc:
                result, reason = "rejected", str(exc)[:400]
            else:
                for chain, period in periods:
                    state.enqueue(chain, period)
                result = "not_applicable" if change.not_applicable else "applied"
                reason = "已确认不适用" if change.not_applicable else "已写入维度，受影响批次将自动复核"
            with state.transaction() as conn:
                conn.execute("""
                    UPDATE feishu_ticket SET revision=?, result=?, reason=? WHERE kind=? AND entity=?
                """, (revision, result, reason, kind, key))
    latest = client.get(record_id)["fields"]
    if feishu.cell_text(latest, feishu.STATUS) == feishu.SUBMITTED and submitted == [
        feishu.cell_text(latest, name) for name in editable
    ]:
        status = feishu.REJECTED if result == "rejected" else feishu.APPLIED
        client.update(record_id, {feishu.STATUS: status, feishu.FEEDBACK: reason})


def _ensure_ticket(state: State, client, gap: dict, bound, records: dict, keys: dict) -> None:
    from scheduler import feishu

    kind, key = gap["kind"], gap["key"]
    record = records.get(bound)
    desired = feishu.ticket_fields(kind, key, gap["fields"], gap["periods"])
    if record is None:
        existing = keys.get(desired[feishu.TASK_KEY], [])
        if len(existing) > 1:
            raise ValueError(f"飞书待办任务键重复，请保留一个记录：{kind} {key}")
        seed = f"{desired[feishu.TASK_KEY]}:{bound or ''}"
        token = str(UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16], version=4))
        record = existing[0] if existing else client.create(desired, client_token=token)
        records[record["record_id"]] = record
    _bind(state, gap, record["record_id"])
    context = {
        name: desired[name] for name in (feishu.PERIODS, feishu.CONTEXT)
        if feishu.cell_text(record["fields"], name) != desired[name]
    }
    if context:
        client.update(record["record_id"], context)


def _sync(state: State, client, table_url: str, *, notify: bool = True) -> bool:
    from scheduler import feishu
    from scheduler.alert import send_alert

    records = {row["record_id"]: row for row in client.records()}
    keys = {}
    for row in records.values():
        key = feishu.cell_text(row["fields"], feishu.TASK_KEY)
        if key:
            keys.setdefault(key, []).append(row)
    tickets = {(row["kind"], row["entity"]): row for row in _tickets(state)}
    ok = True
    for gap in state.gaps():
        identity = gap["kind"], gap["key"]
        try:
            _ensure_ticket(state, client, gap, tickets.get(identity, {}).get("record_id"), records, keys)
        except Exception:
            logger.exception("Feishu ticket synchronization failed for %s", identity)
            ok = False
    tickets = _tickets(state)
    for ticket in tickets:
        record = records.get(ticket["record_id"])
        if record:
            try:
                _submit(state, client, ticket, record)
            except Timeout:
                logger.info("Warehouse busy, deferring Feishu submission")
            except Exception:
                logger.exception("Feishu submission failed for record %s", ticket["record_id"])
                ok = False
    active = {(gap["kind"], gap["key"]) for gap in state.gaps()}
    unnotified = [
        row for row in tickets if row["notified"] is None and (row["kind"], row["entity"]) in active
    ]
    if unnotified and notify:
        preview = "\n".join(f"{row['kind']} · {row['entity'][:100]}" for row in unnotified[:12])
        sent = send_alert(
            "WARNING", "手工补录", "feishu-tasks",
            f"发现 {len(unnotified)} 项待补录。填写后将状态改为「已提交」。\n{preview}\n{table_url}",
            deduplicate=False,
        )
        if sent:
            with state.transaction() as conn:
                conn.executemany(
                    "UPDATE feishu_ticket SET notified=? WHERE record_id=?",
                    [(timestamp(), row["record_id"]) for row in unnotified],
                )
        ok = ok and sent
    return ok


def poll(state: State) -> bool:
    from base_infra.config import get_settings
    from scheduler.feishu import client_from_settings

    settings = get_settings()
    configured = settings.FEISHU_CLI_PATH or (settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET)
    try:
        with state.lock("feishu"):
            if not (configured and settings.FEISHU_BASE_TOKEN and settings.FEISHU_TABLE_ID):
                state.service("feishu", "unconfigured", "请配置飞书多维表格应用")
                return True
            notify = bool(
                settings.FEISHU_TABLE_URL.startswith("https://")
                and (
                    settings.FEISHU_ALERT_CHAT_ID
                    or (settings.ALERT_WEBHOOK_URL and settings.ALERT_WEBHOOK_TYPE.lower() == "feishu")
                )
            )
            ok = _sync(state, client_from_settings(settings), settings.FEISHU_TABLE_URL, notify=notify)
            status, message = "ok", ""
            if not ok:
                status, message = "error", "部分提交或通知失败，将重试"
            elif not notify:
                status, message = "notification_unconfigured", "补录已同步，请配置表格链接与飞书群机器人"
            state.service("feishu", status, message)
            return ok
    except Timeout:
        return True
    except Exception as exc:
        logger.exception("Feishu poll failed")
        state.service("feishu", "error", type(exc).__name__)
        return False
    finally:
        state.export()
