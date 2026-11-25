"""Normalize the published manufacturer label to the current OEM name."""

from sqlalchemy import text

from scheduler.monthly_publish import _publication_transaction


def normalize_oem_names():
    with _publication_transaction() as conn:
        totals = text("SELECT COUNT(*), SUM(sales_volume) FROM dwd_sales_monthly_model")
        before = conn.execute(totals).one()
        updated = conn.execute(text("""
            UPDATE dwd_sales_monthly_model d JOIN dim_oem o ON o.id=d.oem_id
            SET d.manufacturer_name=o.oem_name
            WHERE NOT (BINARY d.manufacturer_name <=> BINARY o.oem_name)
        """)).rowcount
        remaining = conn.execute(text("""
            SELECT COUNT(*) FROM dwd_sales_monthly_model d LEFT JOIN dim_oem o ON o.id=d.oem_id
            WHERE o.id IS NULL OR NOT (BINARY d.manufacturer_name <=> BINARY o.oem_name)
        """)).scalar_one()
        if remaining or conn.execute(totals).one() != before:
            raise ValueError("DWD 主机厂名称或销量核对失败")
        return updated
