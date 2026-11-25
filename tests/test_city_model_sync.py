def test_city_model_tick_runs_dimension_sync(monkeypatch):
    from scheduler import runner, city_model_sync
    calls = []
    monkeypatch.setattr(city_model_sync, 'run', lambda state: calls.append(state) or True)
    from scheduler import manual_workflow
    monkeypatch.setattr(manual_workflow, 'poll', lambda state: True)
    assert runner.run_tick('city-models')
    assert len(calls) == 1


def test_city_dimension_queue_replaces_old_batch_gaps(tmp_path):
    import json
    from scheduler.workflow import State
    state = State(tmp_path)
    state.save('city', '2026-01', 'published', gaps=[
        dict(kind='city_model', key='Resolved', fields=['oem_name']),
        dict(kind='area', key='City', fields=['city_code'])])
    gap = dict(kind='city_model', key='Remaining', fields=['brand_name'], periods=['2026-11'])
    with state.transaction() as conn:
        conn.execute("INSERT INTO service_status VALUES ('city_model_gaps','ok','now',?)", (json.dumps([gap]),))
    assert {(g['kind'], g['key']) for g in state.gaps()} == {('area','City'),('city_model','Remaining')}


def test_checked_research_manifest_is_complete_and_has_evidence():
    import json
    from pathlib import Path
    from pipelines.dim.city_model import validate_city_model_review
    path = Path(__file__).resolve().parents[1] / 'data/manual_dimensions/city_model_reviews.json'
    if not path.exists():
        import pytest
        pytest.skip('Local business evidence is intentionally excluded from Git')
    reviews = json.loads(path.read_text(encoding='utf-8'))
    names = [item['source_model_name'] for item in reviews]
    assert len(names) == len(set(names))
    for item in reviews:
        values = validate_city_model_review(item['source_model_name'], item['values'])
        assert 'https://' in values['evidence']
