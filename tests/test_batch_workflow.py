from datetime import date

import pytest


def test_validated_source_survives_interruption_and_resumes_without_crawling(tmp_path, monkeypatch):
    from scheduler import workflow

    store = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-1")

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(workflow, "publish_month", interrupted)
    with pytest.raises(KeyboardInterrupt):
        workflow.process_month(store, "style", "2026-09")

    restarted = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: pytest.fail("recrawled"))
    monkeypatch.setattr(workflow, "publish_month", lambda *args: [])
    assert workflow.process_month(restarted, "style", "2026-09", resume=True)
    assert restarted.batch("style", "2026-09")["published_digest"] == "source-1"


def test_source_error_preserves_last_published_revision(tmp_path, monkeypatch):
    from scheduler import workflow

    store = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-1")
    monkeypatch.setattr(workflow, "publish_month", lambda *args: [])
    assert workflow.process_month(store, "style", "2026-09")

    def invalid(*args, **kwargs):
        raise ValueError("missing source page")

    monkeypatch.setattr(workflow, "collect_month", invalid)
    assert not workflow.process_month(store, "style", "2026-09")
    status = store.batch("style", "2026-09")
    assert status["status"] == "source_error"
    assert status["published_digest"] == "source-1"


def test_month_rollover_keeps_unfinished_months(tmp_path):
    from scheduler.workflow import State, target_months

    store = State(tmp_path)
    store.save("city", "2026-08", "waiting_source")
    assert target_months(store, "city", date(2026, 11, 5)) == ["2026-08", "2026-09", "2026-10"]


def test_optional_gaps_merge_and_do_not_change_published_state(tmp_path, monkeypatch):
    from scheduler import workflow

    store = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-1")
    monkeypatch.setattr(workflow, "publish_month", lambda *args: [
        {"kind": "ads", "key": "测试车型", "fields": ["智驾供应商"]},
    ])
    for period in ("2026-08", "2026-09"):
        assert workflow.process_month(store, "style", period)
    assert store.gaps() == [{
        "kind": "ads", "key": "测试车型", "fields": ["智驾供应商"], "periods": ["2026-08", "2026-09"],
    }]


def test_new_source_requires_publication_even_after_prior_publish(tmp_path, monkeypatch):
    from scheduler import workflow

    store = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-1")
    monkeypatch.setattr(workflow, "publish_month", lambda *args: [])
    assert workflow.process_month(store, "style", "2026-09")

    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-2")
    assert workflow.process_month(store, "style", "2026-09")
    assert store.batch("style", "2026-09")["published_digest"] == "source-2"


def test_enqueue_during_same_revision_collection_is_not_lost(tmp_path, monkeypatch):
    from scheduler import workflow

    store = workflow.State(tmp_path)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: "source-1")
    published = []
    monkeypatch.setattr(workflow, "publish_month", lambda *args: published.append(args) or [])
    assert workflow.process_month(store, "style", "2026-09")

    def same_source(*args, **kwargs):
        store.enqueue("style", "2026-09")
        return "source-1"

    monkeypatch.setattr(workflow, "collect_month", same_source)
    assert workflow.process_month(store, "style", "2026-09")
    assert len(published) == 2


def test_latest_accepted_month_resumes_before_source_is_contacted(tmp_path, monkeypatch):
    from scheduler import runner, workflow
    from scheduler.runtime import business_period

    state = workflow.State(tmp_path)
    period = business_period("style", None)
    state.save("style", period, "ready", digest="source-1")
    monkeypatch.setattr(workflow, "State", lambda: state)
    monkeypatch.setattr(workflow, "collect_month", lambda *args, **kwargs: pytest.fail("recrawled"))
    monkeypatch.setattr(workflow, "publish_month", lambda *args: [])
    assert runner.run_tick("style")
    assert state.batch("style", period)["published_digest"] == "source-1"
