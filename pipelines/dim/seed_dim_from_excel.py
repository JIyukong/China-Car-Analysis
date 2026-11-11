"""根据人工维护 Excel 初始化主机厂与品牌归属关系。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from pipelines.dim.seed_dim_oem_brand import make_brand_code

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXCEL_PATH = PROJECT_ROOT / "汽车品牌主机厂信息表.xlsx"
REQUIRED_COLUMNS = ("主机厂名称", "品牌", "上市公司")
CURRENT_EFFECTIVE_START_DATE = "1900-01-01"
CURRENT_EFFECTIVE_END_DATE = "9999-12-31"
OTHER_COMPANY_CODE = "OTHER"


def normalize_text(value: object) -> str:
    """统一字符串清洗规则。"""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def make_oem_code(oem_name: str) -> str:
    """基于主机厂名称生成稳定编码。"""
    digest = hashlib.md5(oem_name.strip().encode("utf-8")).hexdigest()[:10]
    return f"OEM_{digest}".upper()


def load_excel() -> pd.DataFrame:
    """读取并校验 Excel。"""
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {EXCEL_PATH}")

    df = pd.read_excel(EXCEL_PATH, sheet_name=0)
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必需列: {missing}")

    cleaned = df.loc[:, REQUIRED_COLUMNS].copy()
    for column in REQUIRED_COLUMNS:
        cleaned[column] = cleaned[column].map(normalize_text)

    cleaned = cleaned[
        (cleaned["主机厂名称"] != "")
        & (cleaned["品牌"] != "")
        & (cleaned["上市公司"] != "")
    ].copy()

    duplicates = cleaned["主机厂名称"][cleaned["主机厂名称"].duplicated()].tolist()
    if duplicates:
        raise ValueError(f"Excel 中存在重复主机厂名称: {duplicates}")

    return cleaned


def build_company_lookup(conn) -> tuple[dict[str, tuple[int, str]], tuple[int, str]]:
    """构建 上市公司名称/别名 -> (company_id, company_name) 映射。"""
    rows = conn.execute(
        text(
            """
            SELECT id, company_code, company_name, aliases
            FROM dim_company
            """
        )
    ).fetchall()

    company_lookup: dict[str, tuple[int, str]] = {}
    other_company: tuple[int, str] | None = None

    for company_id, company_code, company_name, aliases in rows:
        normalized_name = normalize_text(company_name)
        company_lookup[normalized_name] = (company_id, normalized_name)
        if normalize_text(company_code).upper() == OTHER_COMPANY_CODE:
            other_company = (company_id, normalized_name)

        if aliases:
            alias_values = json.loads(aliases) if isinstance(aliases, str) else aliases
            for alias in alias_values:
                normalized_alias = normalize_text(alias)
                if normalized_alias:
                    company_lookup[normalized_alias] = (company_id, normalized_name)

    if other_company is None:
        raise RuntimeError("dim_company 缺少 OTHER 占位记录。")

    return company_lookup, other_company


def upsert_missing_brands(conn, df: pd.DataFrame) -> int:
    """补齐 Excel 中出现但 dim_brand 尚不存在的品牌。"""
    existing_rows = conn.execute(text("SELECT brand_name FROM dim_brand")).fetchall()
    existing_names = {normalize_text(row[0]) for row in existing_rows}

    insert_sql = text(
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
    for brand_name in sorted(set(df["品牌"].tolist())):
        normalized_brand = normalize_text(brand_name)
        if not normalized_brand or normalized_brand in existing_names:
            continue

        conn.execute(
            insert_sql,
            {
                "brand_code": make_brand_code(normalized_brand),
                "brand_name": normalized_brand,
                "brand_origin_country": "CN",
                "is_joint_venture_brand": 0,
            },
        )
        existing_names.add(normalized_brand)
        count += 1

    return count


def build_brand_lookup(conn) -> dict[str, tuple[int, str]]:
    """构建 品牌名称 -> (brand_id, brand_name) 映射。"""
    rows = conn.execute(text("SELECT id, brand_name FROM dim_brand")).fetchall()
    return {normalize_text(brand_name): (brand_id, normalize_text(brand_name)) for brand_id, brand_name in rows}


def upsert_oems(
    conn,
    df: pd.DataFrame,
    company_lookup: dict[str, tuple[int, str]],
    other_company: tuple[int, str],
) -> int:
    """写入主机厂维度。"""
    sql = text(
        """
        INSERT INTO dim_oem (
            oem_code,
            oem_name,
            company_id,
            company_name_snapshot
        )
        VALUES (
            :oem_code,
            :oem_name,
            :company_id,
            :company_name_snapshot
        )
        ON DUPLICATE KEY UPDATE
            oem_code = VALUES(oem_code),
            company_id = VALUES(company_id),
            company_name_snapshot = VALUES(company_name_snapshot)
        """
    )

    count = 0
    for row in df.itertuples(index=False):
        oem_name = normalize_text(row.主机厂名称)
        listed_company = normalize_text(row.上市公司)
        company_id, company_name = company_lookup.get(listed_company, other_company)
        if listed_company not in company_lookup:
            logger.warning("Excel 上市公司未命中 dim_company，回退 OTHER: %s", listed_company)

        conn.execute(
            sql,
            {
                "oem_code": make_oem_code(oem_name),
                "oem_name": oem_name,
                "company_id": company_id,
                "company_name_snapshot": company_name,
            },
        )
        count += 1

    return count


def build_oem_lookup(conn) -> dict[str, tuple[int, str, int, str]]:
    """构建 OEM 名称 -> (oem_id, oem_name, company_id, company_name) 映射。"""
    rows = conn.execute(
        text(
            """
            SELECT id, oem_name, company_id, company_name_snapshot
            FROM dim_oem
            """
        )
    ).fetchall()
    return {
        normalize_text(oem_name): (
            oem_id,
            normalize_text(oem_name),
            company_id,
            normalize_text(company_name_snapshot),
        )
        for oem_id, oem_name, company_id, company_name_snapshot in rows
    }


def build_bridge_payload(
    *,
    brand_id: int,
    brand_name: str,
    oem_id: int,
    oem_name: str,
    company_id: int,
    company_name: str,
    relation_type: str = "主销",
    effective_start_date: str = CURRENT_EFFECTIVE_START_DATE,
) -> dict[str, object]:
    """构造桥接表写入载荷。"""
    return {
        "brand_id": brand_id,
        "brand_name_snapshot": normalize_text(brand_name),
        "oem_id": oem_id,
        "oem_name_snapshot": normalize_text(oem_name),
        "company_id": company_id,
        "company_name_snapshot": normalize_text(company_name),
        "relation_type": relation_type,
        "effective_start_date": effective_start_date,
        "effective_end_date": CURRENT_EFFECTIVE_END_DATE,
        "is_current": 1,
        "source_note": EXCEL_PATH.name,
    }


def refresh_bridge_brand_oem(
    conn,
    df: pd.DataFrame,
    brand_lookup: dict[str, tuple[int, str]],
    oem_lookup: dict[str, tuple[int, str, int, str]],
) -> int:
    """按业务键补充或更新关系，保留已有历史和未出现在 Excel 中的关系。"""

    current_sql = text(
        """
        SELECT brand_id, effective_start_date
        FROM bridge_brand_oem
        WHERE oem_id = :oem_id AND is_current = 1
          AND effective_end_date = :effective_end_date
        FOR UPDATE
        """
    )
    sql = text(
        """
        INSERT INTO bridge_brand_oem (
            brand_id,
            brand_name_snapshot,
            oem_id,
            oem_name_snapshot,
            company_id,
            company_name_snapshot,
            relation_type,
            effective_start_date,
            effective_end_date,
            is_current,
            source_note
        )
        VALUES (
            :brand_id,
            :brand_name_snapshot,
            :oem_id,
            :oem_name_snapshot,
            :company_id,
            :company_name_snapshot,
            :relation_type,
            :effective_start_date,
            :effective_end_date,
            :is_current,
            :source_note
        )
        ON DUPLICATE KEY UPDATE
            brand_name_snapshot = IF(is_current = 1, VALUES(brand_name_snapshot), brand_name_snapshot),
            oem_name_snapshot = IF(is_current = 1, VALUES(oem_name_snapshot), oem_name_snapshot),
            company_id = IF(is_current = 1, VALUES(company_id), company_id),
            company_name_snapshot = IF(is_current = 1, VALUES(company_name_snapshot), company_name_snapshot),
            source_note = IF(is_current = 1, VALUES(source_note), source_note)
        """
    )

    count = 0
    for row in df.itertuples(index=False):
        brand_name = normalize_text(row.品牌)
        oem_name = normalize_text(row.主机厂名称)

        brand = brand_lookup.get(brand_name)
        if brand is None:
            raise RuntimeError(f"品牌未命中 dim_brand: {brand_name}")

        oem = oem_lookup.get(oem_name)
        if oem is None:
            raise RuntimeError(f"主机厂未命中 dim_oem: {oem_name}")

        brand_id, normalized_brand = brand
        oem_id, normalized_oem, company_id, company_name = oem
        current = conn.execute(
            current_sql, {"oem_id": oem_id, "effective_end_date": CURRENT_EFFECTIVE_END_DATE}
        ).one_or_none()
        if current is not None and current.brand_id != brand_id:
            raise ValueError(f"主机厂 {normalized_oem} 的品牌已变更，需确认生效日期后单独维护关系")
        payload = build_bridge_payload(
            brand_id=brand_id,
            brand_name=normalized_brand,
            oem_id=oem_id,
            oem_name=normalized_oem,
            company_id=company_id,
            company_name=company_name,
            effective_start_date=str(current.effective_start_date) if current else CURRENT_EFFECTIVE_START_DATE,
        )
        conn.execute(sql, payload)
        count += 1

    return count


def validate_single_current_brand_per_oem(conn) -> None:
    """校验同一 OEM 仅存在一条当前有效品牌关系。"""
    rows = conn.execute(
        text(
            """
            SELECT oem_id
            FROM bridge_brand_oem
            WHERE is_current = 1
              AND effective_end_date = :effective_end_date
            GROUP BY oem_id
            HAVING COUNT(*) > 1
            """
        ),
        {"effective_end_date": CURRENT_EFFECTIVE_END_DATE},
    ).fetchall()
    if rows:
        duplicated = [str(row[0]) for row in rows]
        raise RuntimeError(f"存在多个当前有效品牌关系的 OEM: {duplicated}")


def main() -> None:
    """脚本入口。"""
    df = load_excel()

    with engine.begin() as conn:
        company_lookup, other_company = build_company_lookup(conn)
        brand_inserted = upsert_missing_brands(conn, df)
        oem_count = upsert_oems(conn, df, company_lookup, other_company)
        brand_lookup = build_brand_lookup(conn)
        oem_lookup = build_oem_lookup(conn)
        bridge_count = refresh_bridge_brand_oem(conn, df, brand_lookup, oem_lookup)
        validate_single_current_brand_per_oem(conn)

    logger.info("dim_brand 新增 %d 条", brand_inserted)
    logger.info("dim_oem 处理 %d 条", oem_count)
    logger.info("bridge_brand_oem 写入 %d 条", bridge_count)
    print(
        f"dim_brand 新增 {brand_inserted} 条，"
        f"dim_oem 处理 {oem_count} 条，"
        f"bridge_brand_oem 写入 {bridge_count} 条。"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Excel 导入维度失败: %s", exc)
        print(f"[FATAL] Excel 导入维度失败: {exc}")
        sys.exit(1)
