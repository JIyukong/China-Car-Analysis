"""验证 dim_commodity 与 ods_futures_daily 的入库结果。

覆盖碳酸锂 LC0 / 铜 CU0 / 铝 AL0 三个主力连续品种，
并对期货特有字段（open_interest 持仓量、settle 结算价）做空值率校验。
"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine

# 首期落地的三个品种及其上市起始日（用于判断历史长度是否合理）
EXPECTED_COMMODITIES = ["LC", "CU", "AL"]
LISTED_DATE = {
    "LC": "2023-07-21",
    "CU": "1999-06-18",
    "AL": "1999-08-04",
}


def main() -> None:
    with engine.connect() as conn:
        # 校验 dim_commodity                                        # 维度种子核对
        dim_rows = conn.execute(
            text(
                "SELECT id, commodity_code, commodity_name, exchange, category, "
                "trading_unit, quote_unit, listed_date, main_symbol, is_active "
                "FROM dim_commodity ORDER BY commodity_code"
            )
        ).fetchall()
        print("=" * 60)
        print("dim_commodity 记录:")
        for row in dim_rows:
            print(f"  {dict(row._mapping)}")

        # 逐品种校验 ods_futures_daily                              # ODS 数据总览
        print("=" * 60)
        for code in EXPECTED_COMMODITIES:
            summary = conn.execute(
                text(
                    "SELECT COUNT(*) AS cnt, MIN(trade_date) AS min_date, "
                    "MAX(trade_date) AS max_date, MIN(close) AS min_close, "
                    "MAX(close) AS max_close "
                    "FROM ods_futures_daily WHERE commodity_code_raw = :code"
                ),
                {"code": code},
            ).one()
            print(f"ods_futures_daily {code} 概览: {dict(summary._mapping)}")

            # commodity_id 是否完整回填                             # 映射完整性核对
            null_id = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ods_futures_daily "
                    "WHERE commodity_code_raw = :code AND commodity_id IS NULL"
                ),
                {"code": code},
            ).scalar()
            print(f"  commodity_id IS NULL 行数: {null_id}")

            # 期货特有字段空值率：close / settle / open_interest    # 期货字段质量
            missing = conn.execute(
                text(
                    "SELECT "
                    "SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) AS close_null, "
                    "SUM(CASE WHEN settle IS NULL THEN 1 ELSE 0 END) AS settle_null, "
                    "SUM(CASE WHEN open_interest IS NULL THEN 1 ELSE 0 END) AS oi_null "
                    "FROM ods_futures_daily WHERE commodity_code_raw = :code"
                ),
                {"code": code},
            ).one()
            print(
                f"  空值数 close={missing.close_null}, "
                f"settle={missing.settle_null}, open_interest={missing.oi_null}"
            )

        # 校验唯一键有效性（含 contract_type）                       # 幂等键分布
        dup = conn.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "  SELECT commodity_code_raw, trade_date, source, contract_type, COUNT(*) AS c "
                "  FROM ods_futures_daily "
                "  GROUP BY commodity_code_raw, trade_date, source, contract_type "
                "  HAVING c > 1"
                ") t"
            )
        ).scalar()
        print("=" * 60)
        print(f"唯一键冲突组数: {dup}")

        # 取每个品种最新 2 行样本                                    # 数据样本抽查
        print("=" * 60)
        for code in EXPECTED_COMMODITIES:
            samples = conn.execute(
                text(
                    "SELECT commodity_code_raw, trade_date, open, high, low, close, "
                    "volume, open_interest, settle, exchange_raw, source, fetch_time "
                    "FROM ods_futures_daily WHERE commodity_code_raw = :code "
                    "ORDER BY trade_date DESC LIMIT 2"
                ),
                {"code": code},
            ).fetchall()
            print(f"{code} 最近 2 个交易日样本:")
            for row in samples:
                print(f"  {dict(row._mapping)}")

        print("=" * 60)


if __name__ == "__main__":
    main()
