"""从 16888 ODS 提取车型唯一值写入 dim_model，并更新维度生命周期。

核心规则：
  - dim_oem 已由 Excel 导入脚本维护，此处不再派生
  - 从 ods_sales_monthly_model 提取 model_name_raw 唯一值 → dim_model
  - brand_id 默认指向 OTHER，oem_id 通过 manufacturer_name 匹配
  - 基于 DWD 数据更新 first_seen_month / last_seen_month / is_active

用法:
    python -m pipelines.dim.build_sales_dimensions
"""

from __future__ import annotations

import hashlib
import sys

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.dim.seed_dim_from_excel import CURRENT_EFFECTIVE_END_DATE

logger = get_logger(__name__)

OTHER_BRAND_CODE = "OTHER"
DIM_PLACEHOLDER = "占位"


def normalize_text(value: object) -> str:
    """统一字符串清洗规则。"""
    if value is None:
        return ""
    return str(value).strip()


def make_code(prefix: str, raw_text: str) -> str:
    """基于原始文本生成稳定业务编码。"""
    digest = hashlib.md5(raw_text.strip().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}".upper()


def get_table_columns(conn, table_name: str) -> set[str]:
    """读取指定表的真实字段集合。"""
    rows = conn.execute(text(f"SHOW COLUMNS FROM {table_name}")).fetchall()
    return {str(row[0]).strip() for row in rows}


def render_dim_model_upsert_sql(dim_model_columns: set[str]) -> str:
    """根据真实表结构生成兼容的 dim_model UPSERT SQL。"""
    insert_columns = ["model_code", "model_name", "brand_id", "oem_id"]
    value_columns = [":model_code", ":model_name", ":brand_id", ":oem_id"]
    update_clauses = [
        "brand_id = VALUES(brand_id)",
        "oem_id   = VALUES(oem_id)",
    ]

    if "brand_name" in dim_model_columns:
        insert_columns.insert(3, "brand_name")
        value_columns.insert(3, ":brand_name")
        update_clauses.insert(1, "brand_name = VALUES(brand_name)")
    if "oem_name" in dim_model_columns:
        insert_columns.append("oem_name")
        value_columns.append(":oem_name")
        update_clauses.append("oem_name  = VALUES(oem_name)")
    if "ads_id" in dim_model_columns:
        insert_columns.append("ads_id")
        value_columns.append(":ads_id")
        update_clauses.append("ads_id  = VALUES(ads_id)")
    if "ads_supplier_name" in dim_model_columns:
        insert_columns.append("ads_supplier_name")
        value_columns.append(":ads_supplier_name")
        update_clauses.append("ads_supplier_name = VALUES(ads_supplier_name)")
    if "energy_type" in dim_model_columns:
        insert_columns.append("energy_type")
        value_columns.append(":energy_type")
        update_clauses.append("energy_type = VALUES(energy_type)")
    if "vehicle_type" in dim_model_columns:
        insert_columns.append("vehicle_type")
        value_columns.append(":vehicle_type")
        update_clauses.append("vehicle_type = VALUES(vehicle_type)")

    columns_sql = ", ".join(insert_columns)
    values_sql = ", ".join(value_columns)
    updates_sql = ",\n            ".join(update_clauses)
    return f"""
        INSERT INTO dim_model ({columns_sql})
        VALUES ({values_sql})
        ON DUPLICATE KEY UPDATE
            {updates_sql}
    """


def build_oem_brand_lookup(rows: list[tuple[object, ...]]) -> dict[str, dict[str, object]]:
    """构建 OEM -> 当前品牌映射，若同 OEM 存在多个当前品牌则报错。"""
    lookup: dict[str, dict[str, object]] = {}
    for oem_name, oem_id, company_id, brand_id, brand_name in rows:
        normalized_oem = normalize_text(oem_name)
        if not normalized_oem:
            continue

        existing = lookup.get(normalized_oem)
        current_brand_id = brand_id if brand_id is not None else None
        current_brand_name = normalize_text(brand_name) if brand_name is not None else ""
        if existing is not None and existing["brand_id"] != current_brand_id:
            raise ValueError(f"同一 OEM 存在多个当前品牌关系: {normalized_oem}")

        lookup[normalized_oem] = {
            "oem_id": oem_id,
            "company_id": company_id,
            "brand_id": current_brand_id,
            "brand_name": current_brand_name,
        }

    return lookup


def upsert_models(conn) -> int:
    """从 ODS 提取车型唯一值，插入 dim_model。"""
    other_brand = conn.execute(
        text("SELECT id, brand_name FROM dim_brand WHERE brand_code = :code"),
        {"code": OTHER_BRAND_CODE},
    ).fetchone()
    if other_brand is None:
        raise RuntimeError("dim_brand 中缺少 OTHER 记录，请先执行种子脚本。")
    other_brand_id = other_brand[0]
    other_brand_name = normalize_text(other_brand[1])

    oem_brand_rows = conn.execute(
        text(
            """
            SELECT
                o.oem_name,
                o.id,
                o.company_id,
                b.brand_id,
                b.brand_name_snapshot
            FROM dim_oem o
            LEFT JOIN bridge_brand_oem b
                ON b.oem_id = o.id
               AND b.is_current = 1
               AND b.effective_end_date = :effective_end_date
            """
        ),
        {"effective_end_date": CURRENT_EFFECTIVE_END_DATE},
    ).fetchall()
    oem_brand_lookup = build_oem_brand_lookup(oem_brand_rows)
    dim_model_columns = get_table_columns(conn, "dim_model")

    rows = conn.execute(
        text("""
            SELECT DISTINCT model_name_raw, manufacturer_name_raw
            FROM ods_sales_monthly_model
            WHERE model_name_raw IS NOT NULL
              AND TRIM(model_name_raw) <> ''
              AND manufacturer_name_raw IS NOT NULL
              AND TRIM(manufacturer_name_raw) <> ''
            ORDER BY model_name_raw, manufacturer_name_raw
        """)
    ).fetchall()

    upsert_sql = text(render_dim_model_upsert_sql(dim_model_columns))

    count = 0
    missing_oems: set[str] = set()
    missing_brands: set[str] = set()
    for model_name_raw, manufacturer_name_raw in rows:
        model_name = normalize_text(model_name_raw)
        manufacturer_name = normalize_text(manufacturer_name_raw)
        oem_brand = oem_brand_lookup.get(manufacturer_name)
        if oem_brand is None:
            missing_oems.add(manufacturer_name)
            continue

        oem_id = int(oem_brand["oem_id"])
        brand_id = oem_brand["brand_id"]
        brand_name = normalize_text(oem_brand["brand_name"])
        if brand_id is None:
            missing_brands.add(manufacturer_name)
            brand_id = other_brand_id
            brand_name = other_brand_name

        model_code = make_code("MODEL", f"{model_name}|{manufacturer_name}")
        payload = {
            "model_code": model_code,
            "model_name": model_name,
            "brand_id": brand_id,
            "oem_id": oem_id,
        }
        if "brand_name" in dim_model_columns:
            payload["brand_name"] = brand_name
        if "oem_name" in dim_model_columns:
            payload["oem_name"] = manufacturer_name
        if "ads_id" in dim_model_columns:
            payload["ads_id"] = None
        if "ads_supplier_name" in dim_model_columns:
            payload["ads_supplier_name"] = None
        if "energy_type" in dim_model_columns:
            payload["energy_type"] = DIM_PLACEHOLDER
        if "vehicle_type" in dim_model_columns:
            payload["vehicle_type"] = DIM_PLACEHOLDER

        conn.execute(upsert_sql, payload)
        count += 1

    if missing_oems:
        logger.error("以下主机厂未命中 dim_oem: %s", sorted(missing_oems))
    if missing_brands:
        logger.error("以下主机厂缺少当前品牌桥接关系: %s", sorted(missing_brands))
    if missing_oems or missing_brands:
        raise RuntimeError("dim_model 自动补维存在未闭合的 OEM/品牌映射，请先修复权威映射。")
    return count


def update_dim_lifecycle(conn) -> None:
    """基于 DWD 数据更新 dim_oem / dim_model 的生命周期字段。

    规则：
      - first_seen_month = 该维度在 DWD 中的最小 stat_month
      - last_seen_month  = 该维度在 DWD 中的最大 stat_month
      - is_active = 1 如果 last_seen_month 在最近3个月内，否则 0
      - 无销量数据的记录 is_active = 0
    """
    # 获取数据集中最大月份（基准月）
    base_row = conn.execute(
        text("SELECT MAX(stat_month) FROM dwd_sales_monthly_model")
    ).fetchone()
    base_month = base_row[0]
    if base_month is None:
        logger.warning("DWD 无销量数据，跳过生命周期更新")
        return

    logger.info("基准月（数据集最大月份）：%s", base_month)

    # 活跃阈值 = 基准月 - 3个月
    threshold_sql = text("SELECT DATE_SUB(:base, INTERVAL 3 MONTH)")
    threshold = conn.execute(threshold_sql, {"base": base_month}).fetchone()[0]
    logger.info("活跃阈值：%s（超过此月无销量则标记不活跃）", threshold)

    # --- 更新 dim_oem（有销量数据的）---
    conn.execute(text("""
        UPDATE dim_oem o
        JOIN (
            SELECT oem_id,
                   MIN(stat_month) AS first_month,
                   MAX(stat_month) AS last_month
            FROM dwd_sales_monthly_model
            WHERE oem_id IS NOT NULL
            GROUP BY oem_id
        ) s ON o.id = s.oem_id
        SET o.first_seen_month = s.first_month,
            o.last_seen_month  = s.last_month,
            o.is_active = CASE WHEN s.last_month >= :threshold THEN 1 ELSE 0 END
    """), {"threshold": threshold})

    # --- 更新 dim_oem（无销量数据的 → 标记不活跃）---
    conn.execute(text("""
        UPDATE dim_oem
        SET is_active = 0
        WHERE id NOT IN (
            SELECT DISTINCT oem_id
            FROM dwd_sales_monthly_model
            WHERE oem_id IS NOT NULL
        )
    """))

    # --- 更新 dim_model（有销量数据的）---
    conn.execute(text("""
        UPDATE dim_model m
        JOIN (
            SELECT model_id,
                   MIN(stat_month) AS first_month,
                   MAX(stat_month) AS last_month
            FROM dwd_sales_monthly_model
            WHERE model_id IS NOT NULL
            GROUP BY model_id
        ) s ON m.id = s.model_id
        SET m.first_seen_month = s.first_month,
            m.last_seen_month  = s.last_month,
            m.is_active = CASE WHEN s.last_month >= :threshold THEN 1 ELSE 0 END
    """), {"threshold": threshold})

    # --- 更新 dim_model（无销量数据的 → 标记不活跃）---
    conn.execute(text("""
        UPDATE dim_model
        SET is_active = 0
        WHERE id NOT IN (
            SELECT DISTINCT model_id
            FROM dwd_sales_monthly_model
            WHERE model_id IS NOT NULL
        )
    """))

    logger.info("维度生命周期更新完成")


def main() -> None:
    """脚本入口：派生车型维度。"""
    with engine.begin() as conn:
        model_count = upsert_models(conn)

    logger.info("dim_model 派生/更新 %d 条", model_count)
    print(f"dim_model 派生/更新 {model_count} 条。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("派生维度失败: %s", exc)
        print(f"[FATAL] 派生维度失败: {exc}")
        sys.exit(1)
