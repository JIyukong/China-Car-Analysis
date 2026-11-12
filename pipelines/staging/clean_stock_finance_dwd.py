"""按报告期将验收通过的股票财务 ODS 发布到 DWD。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from base_infra.db import engine
from pipelines.staging.clean_stock_daily_dwd import (
    _STOCK_FIELDS,
    _exact_keys,
    _finite,
    _natural_hash,
    _publish_rows,
    _stock_mapping_valid,
)

_REPORT_TYPES = {(3, 31): {"Q1"}, (6, 30): {"Q2"}, (9, 30): {"Q3"}, (12, 31): {"Q4", "年报"}}
_CRITICAL_FIELDS = ("eps", "net_profit", "total_revenue")
_METRICS = (
    *_CRITICAL_FIELDS, "main_revenue", "total_profit", "total_assets", "total_liabilities", "equity",
    "roe", "gross_margin", "debt_ratio",
)
_COLUMNS = (
    *_STOCK_FIELDS, "report_date", "report_type", "report_year", "report_quarter", *_METRICS,
    "is_ratio_normalized", "row_hash",
)
_METRIC_SELECT = ", ".join(f"ods.{field}" for field in _METRICS)
_SOURCE_SQL = text(f"""
    SELECT ods.stock_id AS ods_stock_id, ods.stock_code_raw, ods.market_raw, ods.exchange_raw,
           ds.id AS stock_id, ds.stock_code, ds.stock_name, ds.market, ds.exchange,
           ods.report_date, ods.report_type, {_METRIC_SELECT}
    FROM ods_stock_finance ods
    LEFT JOIN dim_stock ds ON ds.stock_code = ods.stock_code_raw
    WHERE ods.report_date = :period AND ods.source = 'akshare' AND ods.market_raw = 'A'
    ORDER BY ods.stock_code_raw, ods.report_type
    FOR UPDATE
""")


def publish_finance_period(period: date, expected_keys: set[tuple[str, str]]) -> int:
    """校验并在同一事务内发布、复查指定报告期的全部规范财务报表。"""
    if type(period) is not date or not expected_keys:
        raise ValueError("财务发布需要报告期和非空证券报表清单")
    report_types = _REPORT_TYPES.get((period.month, period.day))
    if not report_types or any(report_type not in report_types for _, report_type in expected_keys):
        raise ValueError("财务报告类型与报告期不一致")

    with engine.begin() as conn:
        rows = conn.execute(_SOURCE_SQL, {"period": period}).mappings().all()
        _exact_keys(rows, lambda row: (row["stock_code_raw"], row["report_type"]), expected_keys, "财务 ODS")

        payload = []
        for row in rows:
            code = row["stock_code_raw"]
            if row["report_date"] != period or row["market"] != "A" or not _stock_mapping_valid(row):
                raise ValueError(f"财务业务键未匹配报告期或股票维度：{code}")
            if not any(row[field] is not None for field in _CRITICAL_FIELDS):
                raise ValueError(f"财务行缺少每股收益、净利润和营业总收入：{code}")
            if any(row[field] is not None and not _finite(row[field]) for field in _METRICS):
                raise ValueError(f"财务行含无效数值：{code}")

            # ODS 比率列定义为百分数，低于 1 的数值也按百分数保留。
            values = {column: row[column] for column in (*_STOCK_FIELDS, "report_date", "report_type", *_METRICS)}
            values.update(
                report_year=period.year, report_quarter=period.month // 3, is_ratio_normalized=1,
                row_hash=_natural_hash(row["stock_id"], period, row["report_type"]),
            )
            payload.append(values)
        return _publish_rows(
            conn, "dwd_stock_finance", period, "market = 'A'", _COLUMNS,
            ("stock_id", "report_date", "report_type"), payload,
        )


def main() -> None:
    raise SystemExit("请通过 python -m scheduler.runner --chain daily --step load_stock_finance 发布到期财务数据。")


if __name__ == "__main__":
    main()
