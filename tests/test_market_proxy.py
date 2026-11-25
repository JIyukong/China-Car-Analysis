from types import SimpleNamespace
import pytest


def test_unconfigured_market_proxy_leaves_network_unchanged():
    from scheduler.market_proxy import ensure_proxy
    assert ensure_proxy(SimpleNamespace(STOCK_MARKET_PROXY_URL='')) is None


def test_existing_proxy_does_not_launch_or_change_global_config(monkeypatch):
    from scheduler import market_proxy as proxy
    monkeypatch.setattr(proxy,'port_ready',lambda *_:True)
    settings=SimpleNamespace(STOCK_MARKET_PROXY_URL='http://127.0.0.1:17897')
    assert proxy.ensure_proxy(settings)=='http://127.0.0.1:17897'


def test_market_proxy_requires_loopback():
    from scheduler.market_proxy import ensure_proxy
    settings=SimpleNamespace(STOCK_MARKET_PROXY_URL='http://0.0.0.0:17897')
    with pytest.raises(ValueError,match='loopback'):
        ensure_proxy(settings)
