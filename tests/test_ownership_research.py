def test_research_tick_dispatch(monkeypatch):
    from scheduler import runner, ownership_research
    calls=[]
    monkeypatch.setattr(ownership_research,'run',lambda state: calls.append(state) or True)
    assert runner.run_tick('ownership-research')
    assert len(calls)==1
