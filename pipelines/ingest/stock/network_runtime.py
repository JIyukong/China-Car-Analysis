"""股票采集网络运行时工具。

用于统一处理代理、NO_PROXY、预检、异常分类与 requests 直连策略。
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


DEFAULT_DIRECT_DOMAINS = (
    "eastmoney.com",
    ".eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "quote.eastmoney.com",
    "datacenter-web.eastmoney.com",
)

DEFAULT_PREFLIGHT_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn=1&pz=1&po=1&np=1&fltt=2&invt=2&fid=f3"
    "&fs=m:0+t:6+f:!2&fields=f12,f14,f2&ut=b2884a393a59ad64002292a3e90d46a5"
)


@dataclass(slots=True)
class DailyFetch:
    """Requested dates committed per code, and hard failures for attempted codes."""

    observed: dict[str, frozenset[date]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeConfig:
    proxy_url: str | None
    direct_domains: tuple[str, ...]
    request_timeout: int
    max_retries: int
    base_sleep_seconds: float
    stock_sleep_seconds: float
    preflight_only: bool
    start_date: str
    repair_from: str | None = None
    trade_dates: dict[str, frozenset[date]] | None = None
    report_through: date | None = None
    daily_fetch: DailyFetch | None = None


def fetch_daily_symbol(fetch, instrument: dict, code: str, config: RuntimeConfig, *, dry_run: bool) -> dict:
    """Record the bounded run's result and keep collecting after a symbol failure."""
    report = config.daily_fetch
    try:
        result = fetch(instrument, config=config, dry_run=dry_run)
    except Exception as exc:
        if report is None:
            raise
        logger.exception("采集 %s 失败", code)
        result = {**instrument, "status": "failed", "error_type": type(exc).__name__, "error": str(exc)}
    if report is not None:
        if result["status"] in {"success", "warning", "no_new_rows"}:
            report.observed[code] = result.get("written_dates", frozenset())
        else:
            report.failures[code] = result["error_type"] or "unknown"
    return result


def write_daily_rows(insert, rows: list[dict], result: dict) -> None:
    """Record date observations only after the ODS upsert returns successfully."""
    result["rows_inserted"] = insert(rows)
    if result["rows_inserted"] != len(rows):
        raise RuntimeError("ODS 写入行数与已验证行数不符")
    result["written_dates"] = frozenset(row["trade_date"] for row in rows)


def parse_direct_domains(raw_domains: str | None) -> tuple[str, ...]:
    custom_domains = []
    if raw_domains:
        custom_domains.extend(
            item.strip() for item in raw_domains.split(",") if item.strip()
        )

    merged = []
    seen = set()
    for item in [*custom_domains, *DEFAULT_DIRECT_DOMAINS]:
        lowered = item.lower()
        if lowered not in seen:
            merged.append(lowered)
            seen.add(lowered)
    return tuple(merged)


def build_runtime_config(args: SimpleNamespace) -> RuntimeConfig:
    return RuntimeConfig(
        proxy_url=getattr(args, "proxy", None),
        direct_domains=parse_direct_domains(getattr(args, "direct_domains", None)),
        request_timeout=int(getattr(args, "timeout", 15)),
        max_retries=max(1, int(getattr(args, "max_retries", 3))),
        base_sleep_seconds=float(getattr(args, "sleep_seconds", 1.0)),
        stock_sleep_seconds=float(getattr(args, "sleep_seconds", 1.0)),
        preflight_only=bool(getattr(args, "preflight_only", False)),
        start_date=str(getattr(args, "start_date", "20170101")),
    )


def should_bypass_proxy(hostname: str | None, direct_domains: Iterable[str]) -> bool:
    if not hostname:
        return False

    host = hostname.lower()
    for domain in direct_domains:
        candidate = domain.lower().lstrip(".")
        if not candidate:
            continue
        if host == candidate or host.endswith(f".{candidate}"):
            return True
    return False


def _merge_no_proxy_values(existing_value: str | None, direct_domains: Iterable[str]) -> str:
    values = []
    seen = set()
    for item in (existing_value or "").split(","):
        normalized = item.strip()
        if normalized and normalized not in seen:
            values.append(normalized)
            seen.add(normalized)

    for item in direct_domains:
        normalized = item.strip()
        if normalized and normalized not in seen:
            values.append(normalized)
            seen.add(normalized)

    return ",".join(values)


def setup_proxy(config: RuntimeConfig) -> None:
    merged_no_proxy = _merge_no_proxy_values(os.getenv("NO_PROXY"), config.direct_domains)
    os.environ["NO_PROXY"] = merged_no_proxy
    os.environ["no_proxy"] = merged_no_proxy

    if not config.proxy_url:
        return

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ[key] = config.proxy_url


@contextmanager
def patched_requests_runtime(config: RuntimeConfig):
    original_request = requests.sessions.Session.request

    def patched_request(session, method, url, *args, **kwargs):
        host = urlparse(url).hostname
        kwargs.setdefault("timeout", config.request_timeout)

        if should_bypass_proxy(host, config.direct_domains):
            original_trust_env = getattr(session, "trust_env", True)
            session.trust_env = False
            proxies = dict(kwargs.get("proxies") or {})
            proxies.setdefault("http", None)
            proxies.setdefault("https", None)
            kwargs["proxies"] = proxies
            try:
                return original_request(session, method, url, *args, **kwargs)
            finally:
                session.trust_env = original_trust_env

        return original_request(session, method, url, *args, **kwargs)

    requests.sessions.Session.request = patched_request
    try:
        yield
    finally:
        requests.sessions.Session.request = original_request


def classify_fetch_error(error: Exception) -> str:
    if isinstance(error, requests.exceptions.ProxyError):
        return "network_proxy_blocked"
    if isinstance(error, requests.exceptions.Timeout):
        return "network_timeout"
    if isinstance(error, requests.exceptions.ConnectionError):
        message = str(error).lower()
        if "remote end closed connection without response" in message:
            return "remote_disconnected"
        if "proxy" in message:
            return "network_proxy_blocked"
        return "network_timeout"

    message = str(error).lower()
    if "remote end closed connection without response" in message:
        return "remote_disconnected"
    if "proxy" in message:
        return "network_proxy_blocked"
    if "timeout" in message:
        return "network_timeout"
    if "too many requests" in message or "rate" in message:
        return "rate_limited_or_temp_blocked"
    return "unknown"


def run_preflight_check(
    config: RuntimeConfig,
    preflight_url: str | None = None,
    response_validator=None,
) -> dict:
    target_url = preflight_url or DEFAULT_PREFLIGHT_URL
    result = {
        "ok": False,
        "status": "unknown",
        "url": target_url,
        "message": "",
        "using_proxy": bool(config.proxy_url or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")),
        "direct_domains": list(config.direct_domains),
    }

    session = requests.Session()
    try:
        for attempt_index in range(config.max_retries):
            try:
                with patched_requests_runtime(config):
                    response = session.get(
                        target_url,
                        timeout=config.request_timeout,
                    )
                response.raise_for_status()
                payload = response.json()
                validator = response_validator or (lambda data: data.get("data", {}).get("diff") is not None)
                if not validator(payload):
                    result["status"] = "schema_mismatch"
                    result["message"] = "东方财富预检返回成功，但响应结构不符合预期"
                    return result

                result["ok"] = True
                result["status"] = "ok"
                result["message"] = "东方财富预检通过"
                return result
            except Exception as exc:  # noqa: BLE001
                result["status"] = classify_fetch_error(exc)
                result["message"] = str(exc)
                if attempt_index == config.max_retries - 1:
                    return result
                sleep_before_retry(config, attempt_index)
        return result
    finally:
        session.close()


def sleep_before_retry(config: RuntimeConfig, attempt_index: int) -> float:
    base = max(0.0, config.base_sleep_seconds)
    delay = base * (2 ** attempt_index)
    jitter = random.uniform(0.8, 1.2)
    sleep_seconds = delay * jitter
    time.sleep(sleep_seconds)
    return sleep_seconds
