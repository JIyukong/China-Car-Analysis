"""M0 种子数据脚本：预置 dim_company / dim_stock / bridge_company_stock。

当前版本将 `dim_company` 的语义收敛为“上市公司维度表”，仅服务股票分析：
  - dim_company          上市公司维度表（含 OTHER 占位记录）
  - dim_stock            股票维度表（统一编码"代码.市场"，如 002594.SZ / 1211.HK / NIO.US）
  - bridge_company_stock 上市公司-股票桥接表（多对多，含 primary_market / is_primary）

特性：
  - 三张表均使用 INSERT ... ON DUPLICATE KEY UPDATE，脚本可重复执行（幂等）
  - 在同一事务内完成：dim_company 插入 → 查 id → dim_stock 插入 → 查 id → bridge 插入
  - 通过 backend.app.config / backend.app.db 读取配置与引擎，不硬编码密码

用法:
    python scripts/seed/seed_dim_and_bridge.py
"""

from __future__ import annotations

import json
import sys

from sqlalchemy import text

from base_infra.config import get_settings
from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# 上市公司主数据清单
# 字段说明:
#   company_code   上市公司唯一编码（uk，如 BYD / SAIC / OTHER）
#   company_name   上市公司简称
#   company_type   上市公司类型（A股/港股/美股/非上市占位）
#   aliases        别名 JSON 数组
#   stocks         各市场股票列表；OTHER 不产生股票与桥接记录
# ----------------------------------------------------------------------------
COMPANIES = [
    {
        "company_code": "OTHER",
        "company_name": "OTHER",
        "full_name": "未归属上市公司占位",
        "company_type": "非上市占位",
        "country": "CN",
        "aliases": ["other", "其他", "未上市", "未归属上市公司"],
        "is_listed": 0,
        "stocks": [],
    },
    {
        "company_code": "BYD",
        "company_name": "比亚迪",
        "full_name": "比亚迪股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["比亚迪股份", "BYD"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "002594.SZ",
                "stock_name": "比亚迪",
                "market": "A",
                "exchange": "SZ",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
            {
                "stock_code": "1211.HK",
                "stock_name": "比亚迪股份",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "A",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "GREATWALL",
        "company_name": "长城汽车",
        "full_name": "长城汽车股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["长城", "GreatWall"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "601633.SH",
                "stock_name": "长城汽车",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
            {
                "stock_code": "2333.HK",
                "stock_name": "长城汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "A",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "CHANGAN",
        "company_name": "长安汽车",
        "full_name": "重庆长安汽车股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["长安", "Changan"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "000625.SZ",
                "stock_name": "长安汽车",
                "market": "A",
                "exchange": "SZ",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "GEELY",
        "company_name": "吉利汽车",
        "full_name": "吉利汽车控股有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["吉利", "Geely"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "0175.HK",
                "stock_name": "吉利汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "SAIC",
        "company_name": "上汽集团",
        "full_name": "上海汽车集团股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["上汽", "上海汽车", "SAIC"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "600104.SH",
                "stock_name": "上汽集团",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "GAC",
        "company_name": "广汽集团",
        "full_name": "广州汽车集团股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["广汽", "GAC"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "601238.SH",
                "stock_name": "广汽集团",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
            {
                "stock_code": "2238.HK",
                "stock_name": "广汽集团",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "A",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "LIAUTO",
        "company_name": "理想汽车",
        "full_name": "理想汽车",
        "company_type": "美股",
        "country": "CN",
        "aliases": ["理想", "Li Auto"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "LI.US",
                "stock_name": "理想汽车",
                "market": "US",
                "exchange": "NASDAQ",
                "currency": "USD",
                "primary_market": "US",
                "is_primary": 1,
            },
            {
                "stock_code": "2015.HK",
                "stock_name": "理想汽车-W",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "US",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "NIO",
        "company_name": "蔚来",
        "full_name": "蔚来汽车",
        "company_type": "美股",
        "country": "CN",
        "aliases": ["蔚来汽车", "NIO"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "NIO.US",
                "stock_name": "蔚来",
                "market": "US",
                "exchange": "NASDAQ",
                "currency": "USD",
                "primary_market": "US",
                "is_primary": 1,
            },
            {
                "stock_code": "9866.HK",
                "stock_name": "蔚来-SW",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "US",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "XPENG",
        "company_name": "小鹏汽车",
        "full_name": "小鹏汽车",
        "company_type": "美股",
        "country": "CN",
        "aliases": ["小鹏", "Xpeng"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "XPEV.US",
                "stock_name": "小鹏汽车",
                "market": "US",
                "exchange": "NASDAQ",
                "currency": "USD",
                "primary_market": "US",
                "is_primary": 1,
            },
            {
                "stock_code": "9868.HK",
                "stock_name": "小鹏汽车-W",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "US",
                "is_primary": 0,
                "relation_note": "港股二次上市",
            },
        ],
    },
    {
        "company_code": "SERES",
        "company_name": "赛力斯",
        "full_name": "赛力斯集团股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["Seres"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "601127.SH",
                "stock_name": "赛力斯",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 0,
            },
            {
                "stock_code": "9927.HK",
                "stock_name": "赛力斯",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
                "relation_note": "港股上市（2025-11）",
            },
        ],
    },
    {
        "company_code": "CHERY",
        "company_name": "奇瑞汽车",
        "full_name": "奇瑞汽车股份有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["奇瑞", "Chery"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "09973.HK",
                "stock_name": "奇瑞汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
                "relation_note": "港股上市（2025-09）",
            },
        ],
    },
    {
        "company_code": "FAWJIEFANG",
        "company_name": "一汽解放",
        "full_name": "一汽解放集团股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["一汽", "FAW Jiefang"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "000800.SZ",
                "stock_name": "一汽解放",
                "market": "A",
                "exchange": "SZ",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "DONGFENG",
        "company_name": "东风集团",
        "full_name": "东风汽车集团股份有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["东风", "东风汽车", "Dongfeng"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "0489.HK",
                "stock_name": "东风集团股份",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
            {
                "stock_code": "600006.SH",
                "stock_name": "东风汽车",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "HK",
                "is_primary": 0,
                "relation_note": "A股关联标的",
            },
        ],
    },
    {
        "company_code": "BAICBLUEPARK",
        "company_name": "北汽蓝谷",
        "full_name": "北汽蓝谷新能源科技股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["北汽新能源", "北汽", "BAIC BluePark"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "600733.SH",
                "stock_name": "北汽蓝谷",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "JAC",
        "company_name": "江淮汽车",
        "full_name": "安徽江淮汽车集团股份有限公司",
        "company_type": "A股",
        "country": "CN",
        "aliases": ["江淮", "JAC"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "600418.SH",
                "stock_name": "江淮汽车",
                "market": "A",
                "exchange": "SH",
                "currency": "CNY",
                "primary_market": "A",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "VOYAH",
        "company_name": "岚图汽车",
        "full_name": "岚图汽车科技股份有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["岚图", "Voyah"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "07489.HK",
                "stock_name": "岚图汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
                "relation_note": "港股上市（2026-03）",
            },
        ],
    },
    {
        "company_code": "BRILLIANCE",
        "company_name": "华晨中国",
        "full_name": "华晨中国汽车控股有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["华晨", "Brilliance"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "1114.HK",
                "stock_name": "华晨中国",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "LEAPMOTOR",
        "company_name": "零跑汽车",
        "full_name": "浙江零跑科技股份有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["零跑", "Leapmotor"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "9863.HK",
                "stock_name": "零跑汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "XIAOMI",
        "company_name": "小米集团",
        "full_name": "小米集团公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["小米", "Xiaomi"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "1810.HK",
                "stock_name": "小米集团-W",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
        ],
    },
    {
        "company_code": "BAIC",
        "company_name": "北京汽车",
        "full_name": "北京汽车股份有限公司",
        "company_type": "港股",
        "country": "CN",
        "aliases": ["北汽", "BAIC"],
        "is_listed": 1,
        "stocks": [
            {
                "stock_code": "1958.HK",
                "stock_name": "北京汽车",
                "market": "HK",
                "exchange": "HKEX",
                "currency": "HKD",
                "primary_market": "HK",
                "is_primary": 1,
            },
        ],
    },
]


def upsert_dim_company(conn) -> int:
    """幂等插入 dim_company，返回处理条数。"""
    sql = text("""
        INSERT INTO dim_company
            (company_code, company_name, full_name, company_type,
             country, aliases, is_listed)
        VALUES
            (:company_code, :company_name, :full_name, :company_type,
             :country, CAST(:aliases AS JSON), :is_listed)
        ON DUPLICATE KEY UPDATE
            company_name  = VALUES(company_name),
            full_name     = VALUES(full_name),
            company_type  = VALUES(company_type),
            country       = VALUES(country),
            aliases       = VALUES(aliases),
            is_listed     = VALUES(is_listed)
    """)
    count = 0
    for c in COMPANIES:
        conn.execute(sql, {
            "company_code": c["company_code"],
            "company_name": c["company_name"],
            "full_name": c["full_name"],
            "company_type": c["company_type"],
            "aliases": json.dumps(c["aliases"], ensure_ascii=False),
            "country": c["country"],
            "is_listed": c["is_listed"],
        })
        count += 1
    return count


def query_company_id_map(conn) -> dict[str, int]:
    """查询 dim_company，返回 {company_code: id} 映射。"""
    rows = conn.execute(text("SELECT id, company_code FROM dim_company")).fetchall()
    return {row[1]: row[0] for row in rows}


def upsert_dim_stock(conn) -> int:
    """幂等插入 dim_stock，返回处理条数。"""
    sql = text("""
        INSERT INTO dim_stock
            (stock_code, stock_name, market, exchange, currency,
             listed_company_name, is_listed)
        VALUES
            (:stock_code, :stock_name, :market, :exchange, :currency,
             :listed_company_name, :is_listed)
        ON DUPLICATE KEY UPDATE
            stock_name           = VALUES(stock_name),
            market               = VALUES(market),
            exchange             = VALUES(exchange),
            currency             = VALUES(currency),
            listed_company_name  = VALUES(listed_company_name),
            is_listed            = VALUES(is_listed)
    """)
    count = 0
    for c in COMPANIES:
        for s in c["stocks"]:
            conn.execute(sql, {
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "market": s["market"],
                "exchange": s["exchange"],
                "currency": s["currency"],
                "listed_company_name": c["company_name"],
                "is_listed": 1,
            })
            count += 1
    return count


def query_stock_id_map(conn) -> dict[str, int]:
    """查询 dim_stock，返回 {stock_code: id} 映射。"""
    rows = conn.execute(text("SELECT id, stock_code FROM dim_stock")).fetchall()
    return {row[1]: row[0] for row in rows}


def upsert_bridge(
    conn,
    company_id_map: dict[str, int],
    stock_id_map: dict[str, int],
) -> int:
    """幂等插入 bridge_company_stock，返回处理条数。"""
    sql = text("""
        INSERT INTO bridge_company_stock
            (company_id, stock_id, primary_market, is_primary, relation_note)
        VALUES
            (:company_id, :stock_id, :primary_market, :is_primary, :relation_note)
        ON DUPLICATE KEY UPDATE
            primary_market = VALUES(primary_market),
            is_primary     = VALUES(is_primary),
            relation_note  = VALUES(relation_note)
    """)
    count = 0
    missing = []
    for c in COMPANIES:
        company_id = company_id_map.get(c["company_code"])
        if company_id is None:
            missing.append(c["company_code"])
            continue
        for s in c["stocks"]:
            stock_id = stock_id_map.get(s["stock_code"])
            if stock_id is None:
                missing.append(s["stock_code"])
                continue
            conn.execute(sql, {
                "company_id": company_id,
                "stock_id": stock_id,
                "primary_market": s["primary_market"],
                "is_primary": s["is_primary"],
                "relation_note": s.get("relation_note"),
            })
            count += 1
    if missing:
        logger.warning("以下主键映射缺失，已跳过: %s", missing)
    return count


def main() -> None:
    """脚本入口：在单事务内完成三表写入并打印各表处理条数。"""
    settings = get_settings()
    db_url = settings.database_url
    safe_target = db_url.split("@")[-1] if "@" in db_url else "***"
    logger.info("开始写入 dim_/bridge_ 种子数据，目标库主机: %s", safe_target)

    with engine.begin() as conn:
        n1 = upsert_dim_company(conn)
        company_id_map = query_company_id_map(conn)
        n2 = upsert_dim_stock(conn)
        stock_id_map = query_stock_id_map(conn)
        n3 = upsert_bridge(conn, company_id_map, stock_id_map)

    logger.info("dim_company 处理 %d 条", n1)
    logger.info("dim_stock 处理 %d 条", n2)
    logger.info("bridge_company_stock 处理 %d 条", n3)
    print(f"dim_company 处理 {n1} 条，dim_stock 处理 {n2} 条，bridge 处理 {n3} 条")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 脚本入口需要兜底打印
        logger.exception("种子数据写入失败: %s", exc)
        print(f"[FATAL] 种子数据写入失败: {exc}")
        sys.exit(1)
