from __future__ import annotations

import unittest

from pipelines.ingest.huawei_ads.crawl_huawei_ads_models import (
    build_record_key,
    build_model_record,
    normalize_solution_groups,
)


class HuaweiAdsModelScraperTest(unittest.TestCase):
    def test_normalize_solution_groups_should_drop_empty_and_deduplicate(self) -> None:
        raw_groups = [
            {
                "solution_name": " 乾崑智驾 ",
                "features": [
                    " 华为乾崑智驾 ADS 3.0 ",
                    "",
                    "华为乾崑智驾 ADS 3.0",
                    "城区 NCA",
                ],
            },
            {
                "solution_name": "  ",
                "features": ["应被丢弃"],
            },
        ]

        self.assertEqual(
            normalize_solution_groups(raw_groups),
            [
                {
                    "solution_name": "乾崑智驾",
                    "features": ["华为乾崑智驾 ADS 3.0", "城区 NCA"],
                }
            ],
        )

    def test_build_model_record_should_mark_ads_features(self) -> None:
        raw_card = {
            "vehicle_name": "阿维塔 12",
            "h3_model_name": "阿维塔 12",
            "model_subtitle": "未来智能豪华轿车",
            "detail_url": "https://www.avatr.com/12/",
            "image_url": "https://example.com/avatr12.webp",
            "image_alt": "阿维塔 12",
            "solution_groups": [
                {
                    "solution_name": "乾崑智驾",
                    "features": ["华为乾崑智驾 ADS 3.0", "城区 NCA"],
                },
                {
                    "solution_name": "鸿蒙座舱",
                    "features": ["鸿蒙座舱 4.0"],
                },
            ],
            "extra_tip": "* 更多智能技术，敬请期待",
            "footnote": "* 点击了解详情跳转至官网",
        }

        record = build_model_record(raw_card)

        self.assertTrue(record["has_huawei_ads"])
        self.assertEqual(record["ads_features"], ["华为乾崑智驾 ADS 3.0"])
        self.assertEqual(
            record["solution_groups"][0],
            {
                "solution_name": "乾崑智驾",
                "features": ["华为乾崑智驾 ADS 3.0", "城区 NCA"],
            },
        )

    def test_build_record_key_should_keep_same_link_different_models(self) -> None:
        first_record = {
            "vehicle_name": "尚界 Z7",
            "h3_model_name": "尚界 Z7",
            "detail_url": "https://hima.auto/shangjie/z7-z7t/",
        }
        second_record = {
            "vehicle_name": "尚界 Z7T",
            "h3_model_name": "尚界 Z7T",
            "detail_url": "https://hima.auto/shangjie/z7-z7t/",
        }

        self.assertNotEqual(
            build_record_key(first_record),
            build_record_key(second_record),
        )


if __name__ == "__main__":
    unittest.main()
