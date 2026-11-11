"""维度种子脚本：预置 dim_commodity。

首期覆盖碳酸锂 LC / 铜 CU / 铝 AL，作为整车金属 BOM 成本基准。

特性：
  - 使用 INSERT ... ON DUPLICATE KEY UPDATE，脚本可重复执行（幂等）
  - 通过 base_infra.config / base_infra.db 读取配置与引擎

用法:
    python -m pipelines.dim.seed_dim_commodity
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.config import get_settings
from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# 商品品种主数据清单
# 字段与 dim_commodity 列一一对应（id/时间戳由 DB 生成）
# ----------------------------------------------------------------------------
COMMODITIES = [
    {
        "commodity_code": "LC",
        "commodity_name": "碳酸锂",
        "exchange": "GFEX",
        "category": "energy_metal",
        "trading_unit": "1吨/手",
        "quote_unit": "元/吨",
        "listed_date": "2023-07-21",
        "main_symbol": "LC0",
        "currency": "CNY",
        "description": "广州期货交易所碳酸锂期货，动力电池正极核心材料成本基准",
        "is_active": 1,
    },
    {
        "commodity_code": "CU",
        "commodity_name": "铜",
        "exchange": "SHFE",
        "category": "industrial_metal",
        "trading_unit": "5吨/手",
        "quote_unit": "元/吨",
        "listed_date": "1999-06-18",
        "main_symbol": "CU0",
        "currency": "CNY",
        "description": "上海期货交易所铜期货，电机绕组与线束核心材料成本基准",
        "is_active": 1,
    },
    {
        "commodity_code": "AL",
        "commodity_name": "铝",
        "exchange": "SHFE",
        "category": "industrial_metal",
        "trading_unit": "5吨/手",
        "quote_unit": "元/吨",
        "listed_date": "1999-08-04",
        "main_symbol": "AL0",
        "currency": "CNY",
        "description": "上海期货交易所铝期货，车身轻量化与电池托盘核心材料成本基准",
        "is_active": 1,
    },
]


def upsert_dim_commodity(conn) -> int:
    """幂等插入 dim_commodity，返回处理条数。"""
    sql = text("""
        INSERT INTO dim_commodity
            (commodity_code, commodity_name, exchange, category,
             trading_unit, quote_unit, listed_date, main_symbol,
             currency, description, is_active)
        VALUES
            (:commodity_code, :commodity_name, :exchange, :category,
             :trading_unit, :quote_unit, :listed_date, :main_symbol,
             :currency, :description, :is_active)
        ON DUPLICATE KEY UPDATE
            commodity_name = VALUES(commodity_name),
            exchange       = VALUES(exchange),
            category       = VALUES(category),
            trading_unit   = VALUES(trading_unit),
            quote_unit     = VALUES(quote_unit),
            listed_date    = VALUES(listed_date),
            main_symbol    = VALUES(main_symbol),
            currency       = VALUES(currency),
            description    = VALUES(description),
            is_active      = VALUES(is_active)
    """)
    count = 0
    for item in COMMODITIES:
        conn.execute(sql, item)                                 # 单条 upsert
        count += 1
    return count


def main() -> None:
    """脚本入口：单事务写入 dim_commodity。"""
    settings = get_settings()
    db_url = settings.database_url
    safe_target = db_url.split("@")[-1] if "@" in db_url else "***"
    logger.info("开始写入 dim_commodity 种子数据，目标库主机: %s", safe_target)

    with engine.begin() as conn:
        n = upsert_dim_commodity(conn)

    logger.info("dim_commodity 处理 %d 条", n)
    print(f"dim_commodity 处理 {n} 条")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口兜底
        logger.exception("dim_commodity 种子数据写入失败: %s", exc)
        print(f"[FATAL] dim_commodity 种子数据写入失败: {exc}")
        sys.exit(1)
