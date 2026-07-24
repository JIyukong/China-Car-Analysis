from __future__ import annotations

import unittest
from datetime import date

from pipelines.dim.refresh_dim_model_ads import (
    DEFAULT_CSV_PATH,
    normalize_ads_proportion,
    normalize_listed_at,
    read_dim_model_ads_csv,
)


class RefreshDimModelAdsCommandTest(unittest.TestCase):
    def test_read_dim_model_ads_csv_should_return_rows(self) -> None:
        rows = read_dim_model_ads_csv(DEFAULT_CSV_PATH)
        self.assertGreater(len(rows), 0)
        self.assertIn("model_name", rows[0])
        self.assertIn("ads_proportion_raw", rows[0])
        self.assertIn("ads_supplier", rows[0])
        self.assertIn("ads_supplier_mode", rows[0])
        self.assertIn("listed_at_raw", rows[0])

    def test_normalize_ads_proportion_should_convert_percent_to_decimal(self) -> None:
        self.assertEqual(normalize_ads_proportion("95%"), 95.0)
        self.assertEqual(normalize_ads_proportion("20"), 20.0)

    def test_normalize_listed_at_should_support_exact_date(self) -> None:
        value = normalize_listed_at("2025/4/19")
        self.assertEqual(value["listed_at_date"], date(2025, 4, 19))
        self.assertEqual(value["listed_at_granularity"], "day")

    def test_normalize_listed_at_should_support_month_granularity(self) -> None:
        value = normalize_listed_at("Jun-21")
        self.assertEqual(value["listed_at_date"], date(2021, 6, 1))
        self.assertEqual(value["listed_at_granularity"], "month")

    def test_normalize_listed_at_should_support_quarter_granularity(self) -> None:
        value = normalize_listed_at("2026年四季度")
        self.assertEqual(value["listed_at_date"], date(2026, 10, 1))
        self.assertEqual(value["listed_at_granularity"], "quarter")

    def test_normalize_listed_at_should_support_season_granularity(self) -> None:
        value = normalize_listed_at("2026年秋季")
        self.assertEqual(value["listed_at_date"], date(2026, 9, 1))
        self.assertEqual(value["listed_at_granularity"], "season")


if __name__ == "__main__":
    unittest.main()
