"""维度种子脚本：预置 dim_index。

首期覆盖同花顺行业指数 881125 汽车整车，作为汽车板块基准线。

特性：
  - 使用 INSERT ... ON DUPLICATE KEY UPDATE，脚本可重复执行（幂等）
  - 通过 base_infra.config / base_infra.db 读取配置与引擎，不硬编码密码

用法:
    python -m pipelines.dim.seed_dim_index
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from base_infra.config import get_settings
from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# 指数主数据清单
# 字段说明:
#   index_code     指数代码（uk 的一部分，如 881125）
#   index_name     指数名称
#   index_source   指数编制来源（THS/CSINDEX/WIND/OTHER）
#   market         市场标识（CN/HK/US）
#   category       指数类别（industry/concept/broad_based）
#   description    指数描述
#   is_active      是否启用（1启用/0停用）
# ----------------------------------------------------------------------------
INDICES = [
    {
        "index_code": "881125",
        "index_name": "汽车整车",
        "index_source": "THS",
        "market": "CN",
        "category": "industry",
        "description": "同花顺行业指数-汽车整车，作为汽车板块基准线",
        "is_active": 1,
    },
]


def upsert_dim_index(conn) -> int:
    """幂等插入 dim_index，返回处理条数。"""
    sql = text("""
        INSERT INTO dim_index
            (index_code, index_name, index_source, market,
             category, description, is_active)
        VALUES
            (:index_code, :index_name, :index_source, :market,
             :category, :description, :is_active)
        ON DUPLICATE KEY UPDATE
            index_name  = VALUES(index_name),
            market      = VALUES(market),
            category    = VALUES(category),
            description = VALUES(description),
            is_active   = VALUES(is_active)
    """)
    count = 0
    for item in INDICES:
        conn.execute(sql, item)                             # 单条 upsert
        count += 1
    return count


def main() -> None:
    """脚本入口：在单事务内完成 dim_index 写入并打印处理条数。"""
    settings = get_settings()
    db_url = settings.database_url
    safe_target = db_url.split("@")[-1] if "@" in db_url else "***"
    logger.info("开始写入 dim_index 种子数据，目标库主机: %s", safe_target)

    with engine.begin() as conn:
        n = upsert_dim_index(conn)

    logger.info("dim_index 处理 %d 条", n)
    print(f"dim_index 处理 {n} 条")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口需要兜底打印
        logger.exception("dim_index 种子数据写入失败: %s", exc)
        print(f"[FATAL] dim_index 种子数据写入失败: {exc}")
        sys.exit(1)
