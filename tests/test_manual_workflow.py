from copy import deepcopy
from unittest.mock import Mock


class Bitable:
    def __init__(self):
        self.record = None
        self.fail_ack = False

    def records(self):
        return [deepcopy(self.record)] if self.record else []

    def create(self, fields, *, client_token):
        assert client_token
        self.record = {"record_id": "record1", "fields": dict(fields)}
        return deepcopy(self.record)

    def get(self, record_id):
        assert record_id == self.record["record_id"]
        return deepcopy(self.record)

    def update(self, record_id, fields):
        assert record_id == self.record["record_id"]
        if self.fail_ack and fields.get("状态") == "已应用":
            self.fail_ack = False
            raise RuntimeError("writeback temporarily unavailable")
        self.record["fields"].update(fields)


def test_failed_notification_and_receipt_retry_then_resume_exactly_once(tmp_path, monkeypatch):
    from scheduler import alert, feishu, manual_workflow, monthly_publish, workflow

    state = workflow.State(tmp_path)
    state.save("style", "2026-09", "published", digest="source-1", gaps=[
        {"kind": "energy", "key": "Test", "fields": ["energy_type"]},
    ])
    client = Bitable()
    notify = Mock(side_effect=[False, True])
    apply = Mock()
    monkeypatch.setattr(alert, "send_alert", notify)
    monkeypatch.setattr(feishu, "apply_submission", apply)
    monkeypatch.setattr(monthly_publish, "affected_months", lambda *_: [("style", "2026-09")])
    monkeypatch.setattr(workflow, "publish_month", lambda *_: [])

    assert not manual_workflow._sync(state, client, "https://example.test")
    assert manual_workflow._sync(state, client, "https://example.test")
    client.update("record1", {feishu.STATUS: feishu.SUBMITTED, feishu.INPUT: "{}"})
    assert manual_workflow._sync(state, client, "https://example.test")
    assert client.record["fields"][feishu.STATUS] == feishu.REJECTED
    apply.assert_not_called()

    client.update("record1", {feishu.STATUS: feishu.SUBMITTED, feishu.INPUT: '{"energy_type":"新能源"}'})
    client.fail_ack = True
    assert not manual_workflow._sync(state, client, "https://example.test")
    assert state.batch("style", "2026-09")["status"] == "ready"
    assert manual_workflow._sync(state, client, "https://example.test")
    assert client.record["fields"][feishu.STATUS] == feishu.APPLIED
    apply.assert_called_once()
    assert notify.call_count == 2

    workflow.resume_ready(state)
    assert state.batch("style", "2026-09")["status"] == "published"
    assert not state.gaps()
