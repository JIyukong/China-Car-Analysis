"""初始化品牌维度种子数据。

当前脚本只负责写入 `dim_brand`，主机厂与品牌归属关系统一由
`seed_dim_from_excel.py` 根据人工维护的 Excel 导入。
"""

from __future__ import annotations

import hashlib
import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


def make_brand_code(brand_name: str) -> str:
    """基于品牌名称生成稳定编码。"""
    normalized = brand_name.strip()
    if normalized.upper() == "OTHER":
        return "OTHER"
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:10]
    return f"BRAND_{digest}".upper()


BRANDS = [
    {
        "brand_code": "OTHER",
        "brand_name": "OTHER",
        "brand_origin_country": "CN",
        "is_joint_venture_brand": 0,
    },
]


def upsert_dim_brand(conn) -> int:
    """幂等写入品牌维度。"""
    sql = text(
        """
        INSERT INTO dim_brand (
            brand_code,
            brand_name,
            brand_origin_country,
            is_joint_venture_brand
        )
        VALUES (
            :brand_code,
            :brand_name,
            :brand_origin_country,
            :is_joint_venture_brand
        )
        ON DUPLICATE KEY UPDATE
            brand_name = VALUES(brand_name),
            brand_origin_country = VALUES(brand_origin_country),
            is_joint_venture_brand = VALUES(is_joint_venture_brand)
        """
    )
    count = 0
    for brand in BRANDS:
        payload = dict(brand)
        if not payload["brand_code"]:
            payload["brand_code"] = make_brand_code(payload["brand_name"])
        conn.execute(sql, payload)
        count += 1
    return count


def main() -> None:
    """脚本入口。"""
    with engine.begin() as conn:
        count = upsert_dim_brand(conn)
    logger.info("dim_brand 处理 %d 条", count)
    print(f"dim_brand 处理 {count} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("初始化 dim_brand 失败: %s", exc)
        print(f"[FATAL] 初始化 dim_brand 失败: {exc}")
        sys.exit(1)
