"""验证 dim_index 与 ods_index_daily 的入库结果。"""

from __future__ import annotations

from sqlalchemy import text

from base_infra.db import engine


def main() -> None:
    with engine.connect() as conn:
        # 校验 dim_index                                            # 维度种子核对
        dim_rows = conn.execute(
            text(
                "SELECT id, index_code, index_name, index_source, market, category, is_active "
                "FROM dim_index"
            )
        ).fetchall()
        print("=" * 60)
        print("dim_index 记录:")
        for row in dim_rows:
            print(f"  {dict(row._mapping)}")

        # 校验 ods_index_daily 总量与时间范围                       # ODS 数据总览
        summary = conn.execute(
            text(
                "SELECT COUNT(*) AS cnt, MIN(trade_date) AS min_date, "
                "MAX(trade_date) AS max_date, MIN(close) AS min_close, "
                "MAX(close) AS max_close "
                "FROM ods_index_daily WHERE index_code_raw = :code"
            ),
            {"code": "881125"},
        ).one()
        print("=" * 60)
        print("ods_index_daily 881125 概览:")
        print(f"  {dict(summary._mapping)}")

        # 校验 index_id 是否完整回填                                # 映射完整性核对
        null_id = conn.execute(
            text(
                "SELECT COUNT(*) FROM ods_index_daily "
                "WHERE index_code_raw = :code AND index_id IS NULL"
            ),
            {"code": "881125"},
        ).scalar()
        print(f"  index_id IS NULL 行数: {null_id}")

        # 校验唯一键有效性                                          # 幂等键分布
        dup = conn.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "  SELECT index_code_raw, trade_date, source, COUNT(*) AS c "
                "  FROM ods_index_daily GROUP BY index_code_raw, trade_date, source "
                "  HAVING c > 1"
                ") t"
            )
        ).scalar()
        print(f"  唯一键冲突组数: {dup}")

        # 取最新 3 行样本                                           # 数据样本抽查
        samples = conn.execute(
            text(
                "SELECT trade_date, open, high, low, close, volume, amount, "
                "pct_change, change_amount, turnover_rate, source, fetch_time "
                "FROM ods_index_daily WHERE index_code_raw = :code "
                "ORDER BY trade_date DESC LIMIT 3"
            ),
            {"code": "881125"},
        ).fetchall()
        print("=" * 60)
        print("最近 3 个交易日样本:")
        for row in samples:
            print(f"  {dict(row._mapping)}")

        print("=" * 60)


if __name__ == "__main__":
    main()
