from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from unittest import mock
from argparse import Namespace
from datetime import date
from pathlib import Path

import requests
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _DummyEngine:
    def connect(self):  # pragma: no cover - import stub only
        raise AssertionError("engine.connect should not be used in these unit tests")

    def begin(self):  # pragma: no cover - import stub only
        raise AssertionError("engine.begin should not be used in these unit tests")


def _install_import_stubs() -> None:
    sys.modules.setdefault("akshare", types.ModuleType("akshare"))

    db_module = types.ModuleType("base_infra.db")
    db_module.engine = _DummyEngine()
    sys.modules["base_infra.db"] = db_module

    logging_module = types.ModuleType("base_infra.logging_config")
    logging_module.get_logger = lambda _name: types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )
    sys.modules["base_infra.logging_config"] = logging_module


def _load_daily_module():
    _install_import_stubs()
    sys.modules.pop("pipelines.ingest.stock.load_stock_daily_akshare", None)
    return importlib.import_module("pipelines.ingest.stock.load_stock_daily_akshare")


def _load_finance_module():
    _install_import_stubs()
    sys.modules.pop("pipelines.ingest.stock.load_stock_finance_akshare", None)
    return importlib.import_module("pipelines.ingest.stock.load_stock_finance_akshare")


def _load_network_module():
    return importlib.import_module("pipelines.ingest.stock.network_runtime")


class StockAkshareNetworkingTest(unittest.TestCase):
    def tearDown(self) -> None:
        for key in [
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
        ]:
            os.environ.pop(key, None)

    def test_build_runtime_config_includes_eastmoney_direct_domains(self) -> None:
        daily = _load_daily_module()

        config = daily.build_runtime_config(
            Namespace(
                dry_run=True,
                stock=None,
                proxy="http://127.0.0.1:7890",
                direct_domains="foo.com,bar.com",
                timeout=12,
                max_retries=4,
                sleep_seconds=0.8,
                preflight_only=False,
                start_date="20170101",
            )
        )

        self.assertEqual(config.proxy_url, "http://127.0.0.1:7890")
        self.assertEqual(config.request_timeout, 12)
        self.assertIn("foo.com", config.direct_domains)
        self.assertIn("eastmoney.com", config.direct_domains)
        self.assertTrue(daily.should_bypass_proxy("83.push2.eastmoney.com", config.direct_domains))

    def test_setup_proxy_merges_existing_no_proxy(self) -> None:
        daily = _load_daily_module()
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"

        config = daily.build_runtime_config(
            Namespace(
                dry_run=True,
                stock=None,
                proxy="socks5://127.0.0.1:6789",
                direct_domains=None,
                timeout=10,
                max_retries=3,
                sleep_seconds=0.5,
                preflight_only=False,
                start_date="20170101",
            )
        )

        daily.setup_proxy(config)

        self.assertEqual(os.environ["HTTPS_PROXY"], "socks5://127.0.0.1:6789")
        self.assertIn("localhost", os.environ["NO_PROXY"])
        self.assertIn("eastmoney.com", os.environ["NO_PROXY"])
        self.assertEqual(os.environ["NO_PROXY"], os.environ["no_proxy"])

    def test_classify_fetch_error_marks_proxy_failures(self) -> None:
        daily = _load_daily_module()

        error = requests.exceptions.ProxyError("Unable to connect to proxy")

        self.assertEqual(daily.classify_fetch_error(error), "network_proxy_blocked")

    def test_check_daily_data_completeness_flags_tiny_full_load(self) -> None:
        daily = _load_daily_module()

        result = daily.check_daily_data_completeness(
            rows=[
                {
                    "trade_date": date(2026, 7, 24),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.9,
                    "close": 10.2,
                }
            ],
            last_date=None,
            start_date="20170101",
        )

        self.assertFalse(result["is_complete"])
        self.assertIn("首次全量", " ".join(result["issues"]))

    def test_a_share_fetch_falls_back_to_tx_source(self) -> None:
        daily = _load_daily_module()
        expected_df = object()
        config = daily.build_runtime_config(
            Namespace(
                dry_run=True,
                stock=None,
                proxy=None,
                direct_domains=None,
                timeout=10,
                max_retries=1,
                sleep_seconds=0.0,
                preflight_only=False,
                start_date="20170101",
            )
        )

        with mock.patch.object(
            daily,
            "fetch_a_stock_daily",
            side_effect=requests.exceptions.ConnectionError("RemoteDisconnected"),
        ), mock.patch.object(
            daily,
            "fetch_a_stock_daily_tx",
            return_value=expected_df,
        ):
            df, source_name = daily.fetch_a_stock_daily_with_fallback(
                "002594.SZ",
                "20170101",
                config,
            )

        self.assertIs(df, expected_df)
        self.assertEqual(source_name, "tx")

    def test_hk_fetch_falls_back_to_sina_source(self) -> None:
        daily = _load_daily_module()
        expected_df = object()
        config = daily.build_runtime_config(
            Namespace(
                dry_run=True,
                stock=None,
                proxy=None,
                direct_domains=None,
                timeout=10,
                max_retries=1,
                sleep_seconds=0.0,
                preflight_only=False,
                start_date="20170101",
            )
        )

        with mock.patch.object(
            daily,
            "fetch_hk_stock_daily",
            side_effect=requests.exceptions.ConnectionError("RemoteDisconnected"),
        ), mock.patch.object(
            daily,
            "fetch_hk_stock_daily_sina",
            return_value=expected_df,
        ):
            df, source_name = daily.fetch_hk_stock_daily_with_fallback(
                "1211.HK",
                "20170101",
                config,
            )

        self.assertIs(df, expected_df)
        self.assertEqual(source_name, "sina")

    def test_hk_fetch_sina_falls_back_to_full_when_filter_empty(self) -> None:
        daily = _load_daily_module()
        full_df = pd.DataFrame([
            {"date": "2026-07-21", "open": 2.9, "high": 3.0, "low": 2.8, "close": 2.9, "volume": 100.0},
            {"date": "2026-07-22", "open": 2.9, "high": 2.95, "low": 2.8, "close": 2.85, "volume": 120.0},
            {"date": "2026-07-23", "open": 2.85, "high": 2.9, "low": 2.75, "close": 2.78, "volume": 150.0},
        ])

        with mock.patch.object(daily, "fetch_hk_stock_daily_sina", wraps=daily.fetch_hk_stock_daily_sina) as wrapped:
            with mock.patch.object(daily.ak, "stock_hk_daily", return_value=full_df, create=True):
                result = wrapped("07489.HK", start_date="20260724")

        self.assertEqual(len(result), 3, "过滤窗口内为空时应回退到全量数据")
        self.assertIn("2026-07-21", result["date"].astype(str).tolist())

    def test_fetch_single_stock_skips_us_market(self) -> None:
        daily = _load_daily_module()
        config = daily.build_runtime_config(
            Namespace(
                dry_run=True,
                stock=None,
                proxy=None,
                direct_domains=None,
                timeout=10,
                max_retries=1,
                sleep_seconds=0.0,
                preflight_only=False,
                start_date="20170101",
            )
        )

        result = daily.fetch_single_stock(
            {
                "stock_id": 1,
                "stock_code": "NIO.US",
                "stock_name": "蔚来",
                "market": "US",
                "exchange": "NASDAQ",
            },
            config=config,
            dry_run=True,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["error_type"], "unsupported_market")
        self.assertIn("US", result["error"])

    def test_finance_completeness_flags_tiny_full_load(self) -> None:
        finance = _load_finance_module()

        result = finance.check_data_completeness(
            rows=[
                {
                    "report_date": date(2025, 12, 31),
                    "report_type": "年报",
                    "eps": 1.23,
                    "net_profit": 100.0,
                    "total_revenue": 1000.0,
                }
            ],
            is_full_load=True,
        )

        self.assertFalse(result["is_complete"])
        self.assertIn("首次全量", " ".join(result["key_fields_missing"]))

    def test_transform_finance_data_supports_eastmoney_english_columns(self) -> None:
        finance = _load_finance_module()
        df = pd.DataFrame(
            [
                {
                    "REPORT_DATE": "2026-03-31 00:00:00",
                    "BASIC_EPS": 0.448,
                    "NETPROFIT": 4010931000,
                    "TOTAL_OPERATE_INCOME": 150225314000,
                    "OPERATE_INCOME": 150225314000,
                    "TOTAL_PROFIT": 4746395000,
                }
            ]
        )

        rows = finance.transform_finance_data(df, stock_id=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["report_type"], "Q1")
        self.assertEqual(rows[0]["eps"], 0.448)
        self.assertEqual(rows[0]["net_profit"], 4010931000.0)
        self.assertEqual(rows[0]["total_revenue"], 150225314000.0)
        self.assertEqual(rows[0]["total_profit"], 4746395000.0)

    def test_preflight_retries_on_transient_disconnect(self) -> None:
        network_runtime = _load_network_module()
        config = network_runtime.build_runtime_config(
            Namespace(
                proxy=None,
                direct_domains=None,
                timeout=5,
                max_retries=2,
                sleep_seconds=0.0,
                preflight_only=True,
                start_date="20170101",
            )
        )

        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"diff": []}}
        session = mock.Mock()
        session.get.side_effect = [
            requests.exceptions.ConnectionError(
                "Remote end closed connection without response"
            ),
            response,
        ]

        with mock.patch.object(network_runtime.requests, "Session", return_value=session):
            result = network_runtime.run_preflight_check(config)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.get.call_count, 2)

    def test_preflight_uses_custom_url(self) -> None:
        network_runtime = _load_network_module()
        config = network_runtime.build_runtime_config(
            Namespace(
                proxy=None,
                direct_domains=None,
                timeout=5,
                max_retries=1,
                sleep_seconds=0.0,
                preflight_only=True,
                start_date="20170101",
            )
        )

        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        session = mock.Mock()
        session.get.return_value = response

        with mock.patch.object(network_runtime.requests, "Session", return_value=session):
            result = network_runtime.run_preflight_check(
                config,
                preflight_url="https://emweb.securities.eastmoney.com/test",
                response_validator=lambda payload: payload.get("ok") is True,
            )

        self.assertTrue(result["ok"])
        session.get.assert_called_with(
            "https://emweb.securities.eastmoney.com/test",
            timeout=5,
        )


if __name__ == "__main__":
    unittest.main()
