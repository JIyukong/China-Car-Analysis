from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("send_succeeds", [True, False])
def test_configured_feishu_group_is_the_only_destination(monkeypatch, send_succeeds):
    from scheduler import alert, feishu

    settings = SimpleNamespace(
        FEISHU_ALERT_CHAT_ID="oc_carapp",
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="test-secret",
        FEISHU_BASE_TOKEN="base",
        FEISHU_TABLE_ID="tbl_test",
        ALERT_WEBHOOK_URL="https://example.invalid/old-group",
        ALERT_WEBHOOK_TYPE="feishu",
    )
    send = Mock(return_value="om_test") if send_succeeds else Mock(side_effect=feishu.FeishuError("unavailable"))
    monkeypatch.setattr(alert, "get_settings", lambda: settings)
    monkeypatch.setattr(feishu.FeishuClient, "send_chat_text", send, raising=False)
    monkeypatch.setattr(alert.urllib.request, "urlopen", lambda *_a, **_kw: pytest.fail("used the old webhook"))

    assert alert.send_alert("INFO", "deployment", "test", "hello", deduplicate=False) is send_succeeds
    assert send.call_count == 1
    assert send.call_args.args[0] == "oc_carapp"
    assert "hello" in send.call_args.args[1]


def test_feishu_group_request_uses_app_token_and_chat_id(monkeypatch):
    from scheduler.feishu import FeishuClient

    client = FeishuClient("cli_test", "test-secret", "base", "tbl_test")
    request = Mock(return_value={"data": {"message_id": "om_test"}})
    monkeypatch.setattr(client, "_access_token", lambda: "tenant-token")
    monkeypatch.setattr(client, "_request", request)

    assert client.send_chat_text("oc_carapp", "测试") == "om_test"
    request.assert_called_once_with(
        "POST", "/im/v1/messages", params={"receive_id_type": "chat_id"}, token="tenant-token",
        body={"receive_id": "oc_carapp", "msg_type": "text", "content": '{"text": "测试"}'},
    )


def test_cli_reads_and_updates_base_v3_fields_without_exporting_a_secret(monkeypatch):
    import json
    from subprocess import CompletedProcess

    from scheduler import feishu

    cli = feishu.FeishuCliClient("lark-cli.exe", "base", "tbl_test")
    reply = {
        "record_id_list": ["rec_test"], "fields": [feishu.STATUS],
        "data": [[["已提交"]]], "has_more": False,
    }
    run = Mock(side_effect=[
        CompletedProcess([], 0, json.dumps({"ok": True, "data": reply}), ""),
        CompletedProcess([], 0, json.dumps({"ok": True, "data": {"record_id": "rec_test"}}), ""),
    ])
    monkeypatch.setattr(feishu.subprocess, "run", run)

    records = cli.records()
    assert feishu.cell_text(records[0]["fields"], feishu.STATUS) == feishu.SUBMITTED
    cli.update("rec_test", {feishu.STATUS: feishu.APPLIED})
    args = run.call_args.args[0]
    assert args == [
        "lark-cli.exe", "api", "PATCH", "/open-apis/base/v3/bases/base/tables/tbl_test/records/rec_test",
        "--as", "bot", "--data", "-",
    ]
    assert json.loads(run.call_args.kwargs["input"]) == {feishu.STATUS: [feishu.APPLIED]}


def test_cli_create_recovers_record_id_by_exact_task_key(monkeypatch):
    import json

    from scheduler import feishu

    cli = feishu.FeishuCliClient("lark-cli.exe", "base", "tbl_test")
    fields = {feishu.TASK_KEY: "carapp-ticket-1", feishu.STATUS: feishu.PENDING}
    reply = {
        "record_id_list": ["rec_created"], "fields": list(fields),
        "data": [["carapp-ticket-1", [feishu.PENDING]]], "has_more": False,
    }
    call = Mock(side_effect=[{"insert": {}}, reply])
    monkeypatch.setattr(cli, "_call", call)

    assert cli.create(fields)["record_id"] == "rec_created"
    assert [invocation.args[0] for invocation in call.call_args_list] == ["POST", "GET"]
    assert json.loads(call.call_args.kwargs["params"]["filter"])["conditions"] == [
        [feishu.TASK_KEY, "==", "carapp-ticket-1"],
    ]
