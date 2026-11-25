"""Optional client-only market-data proxy; never edits system proxy settings."""
import socket
from urllib.parse import urlparse

def port_ready(host, port):
    try:
        with socket.create_connection((host,port),timeout=.5):
            return True
    except OSError:
        return False


def ensure_proxy(settings=None):
    if settings is None:
        from base_infra.config import get_settings
        settings=get_settings()
    url=settings.STOCK_MARKET_PROXY_URL
    if not url:
        return None
    parsed=urlparse(url)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or not parsed.port:
        raise ValueError('Stock proxy must be an explicit loopback HTTP endpoint')
    if port_ready(parsed.hostname,parsed.port):
        return url
    raise RuntimeError('Configured stock proxy is unavailable')
