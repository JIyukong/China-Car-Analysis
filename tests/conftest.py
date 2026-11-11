import socket

import pymysql
import pytest


@pytest.fixture(scope="session", autouse=True)
def offline_environment(tmp_path_factory):
    """Keep imports and in-process tests independent of the developer's .env."""

    def reject_network(*args, **kwargs):
        pytest.fail("Tests must use simulated connections instead of network access")

    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path_factory.mktemp("offline"))
        patch.setenv("DB_HOST", "127.0.0.1")
        patch.setenv("DB_PORT", "3306")
        patch.setenv("DB_USER", "smoke")
        patch.setenv("DB_PASSWORD", "smoke")
        patch.setenv("DB_NAME", "smoke")
        patch.setenv("ALERT_WEBHOOK_URL", "")
        patch.setenv("LOG_DIR", "logs")
        patch.setenv("LOG_TO_FILE", "false")
        patch.setenv("LOG_TO_CONSOLE", "false")
        # Windows asyncio uses a loopback socket pair to wake its event loop.
        patch.setattr(pymysql.connections.Connection, "connect", reject_network)
        patch.setattr(socket, "create_connection", reject_network)
        patch.setattr(socket, "getaddrinfo", reject_network)
        yield
