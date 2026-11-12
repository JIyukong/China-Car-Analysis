"""把已校验的 CSV 装进连接私有的暂存表，再增量更新正式维度。"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "manual"
Dimension = Literal["dim_area", "dim_energy_vehicle", "dim_model_ads_manual"]
FEISHU_SOURCE = "feishu-bitable"
_ADS_COLUMNS = (
    "ads_proportion_raw", "ads_proportion_pct", "ads_supplier", "ads_supplier_mode",
    "listed_at_raw", "listed_at_date", "listed_at_granularity",
)


def upsert_dimension(conn: Connection, dimension: Dimension, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("CSV 中没有可导入的记录")

    sql = (SQL_DIR / f"refresh_{dimension}.sql").read_text(encoding="utf-8")
    statements = [
        part.strip()
        for part in "\n".join(line for line in sql.splitlines() if not line.startswith("--")).split(";")
        if part.strip()
    ]
    table = f"tmp_manual_{dimension}"
    columns = ", ".join(rows[0])
    values = ", ".join(f":{column}" for column in rows[0])

    conn.execute(text(f"CREATE TEMPORARY TABLE {table} LIKE stg_{dimension}"))
    try:
        conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), rows)
        for statement in statements:
            conn.execute(text(statement))
    finally:
        # 临时表属于连接，归还池前显式释放。TEMPORARY 不会删除同名正式表。
        conn.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {table}"))


def _postcheck(conn: Connection, sql: str, values: dict) -> None:
    if conn.execute(text(sql), values).scalar_one() != 1:
        raise RuntimeError("飞书补录写入后复查未通过")


_CURRENT_OEM = """
    AND NOT EXISTS (
        SELECT 1 FROM dim_oem legacy JOIN dim_oem canonical
          ON canonical.company_id = legacy.company_id AND canonical.id <> legacy.id
         AND JSON_CONTAINS(canonical.aliases, JSON_QUOTE(legacy.oem_name))
        WHERE legacy.id = m.oem_id
          AND NOT EXISTS (SELECT 1 FROM dwd_sales_monthly_model d WHERE d.oem_id=legacy.id)
          AND NOT EXISTS (SELECT 1 FROM dwd_sales_monthly_model_city d WHERE d.oem_id=legacy.id)
    )
"""


def _model(conn: Connection, key: str) -> dict:
    rows = conn.execute(text(f"""
        SELECT m.id, m.model_name, m.energy_type, m.vehicle_type,
               e.energy_type AS stored_energy_type, e.vehicle_type AS stored_vehicle_type
        FROM dim_model m LEFT JOIN dim_energy_vehicle e ON e.model_name = m.model_name
        WHERE m.model_name = :key {_CURRENT_OEM} FOR UPDATE
    """), {"key": key}).mappings().all()
    if len(rows) != 1 or rows[0]["model_name"] != key:
        raise ValueError("业务键未命中唯一车型，请先修正车型映射")
    return dict(rows[0])


def _apply_energy(conn: Connection, key: str, values: dict) -> None:
    from pipelines.dim.refresh_dim_energy_vehicle import ALLOWED_ENERGY_TYPES, ALLOWED_VEHICLE_TYPES

    model = _model(conn, key)
    merged = {}
    for field, allowed in (("energy_type", ALLOWED_ENERGY_TYPES), ("vehicle_type", ALLOWED_VEHICLE_TYPES)):
        valid = allowed - {"占位"}
        existing = model.get(f"stored_{field}")
        if existing not in valid:
            existing = model[field]
        supplied = values.get(field)
        if supplied is not None and existing in valid and supplied != existing:
            raise ValueError(f"{field} 与已有分类冲突，请核对维度值")
        merged[field] = supplied if supplied is not None else existing
        if merged[field] not in valid:
            raise ValueError(f"现有 {field} 缺失或无效，请补齐缺失分类")
    upsert_dimension(conn, "dim_energy_vehicle", [{"model_name": key, **merged, "source_file": FEISHU_SOURCE}])
    _postcheck(conn, """
        SELECT COUNT(*) FROM dim_model
        WHERE id = :id AND energy_type = :energy_type AND vehicle_type = :vehicle_type
    """, {"id": model["id"], **merged})


def _apply_area(conn: Connection, key: str, values: dict) -> None:
    rows = conn.execute(text("""
        SELECT city_code, city_short_name FROM dim_area
        WHERE city_code = :city_code OR city_short_name = :city_short_name FOR UPDATE
    """), values).mappings().all()
    if any(row["city_code"] != values["city_code"] or row["city_short_name"] != key for row in rows):
        raise ValueError("城市编码与已有城市简称冲突")
    if rows:
        # 既有完全相同的业务键允许重试；补录期间新增的映射由同一业务键更新。
        upsert_dimension(conn, "dim_area", [{**values, "source_file": FEISHU_SOURCE}])
    else:
        # 新键用 INSERT，避免并发写入时 ON DUPLICATE KEY UPDATE 修改其他城市。
        conn.execute(text("""
            INSERT INTO dim_area (
                city_code, city_name, city_short_name, city_level, province_code, province_name,
                province_short_name, province_type, region_code, region_name, source_file
            ) VALUES (
                :city_code, :city_name, :city_short_name, :city_level, :province_code, :province_name,
                :province_short_name, :province_type, :region_code, :region_name, :source_file
            )
        """), {**values, "source_file": FEISHU_SOURCE})
    _postcheck(conn, """
        SELECT COUNT(*) FROM dim_area WHERE city_code = :city_code AND city_short_name = :city_short_name
    """, values)


def _apply_subtype(conn: Connection, key: str, values: dict) -> None:
    type_id = conn.execute(text("""
        SELECT id FROM dim_vehicle_type WHERE type_name = :type_name
    """), values).scalar_one_or_none()
    if type_id is None:
        raise ValueError("type_name 在车型大类维度中不存在")
    payload = {"subtype_code": values["subtype_code"], "subtype_name": key, "type_id": type_id}
    rows = conn.execute(text("""
        SELECT subtype_code, subtype_name, vehicle_type_id FROM dim_vehicle_subtype
        WHERE subtype_code = :subtype_code OR subtype_name = :subtype_name FOR UPDATE
    """), payload).mappings().all()
    if any(
        row["subtype_code"] != payload["subtype_code"] or row["subtype_name"] != key
        or row["vehicle_type_id"] != type_id for row in rows
    ):
        raise ValueError("细分编码、名称或大类与已有映射冲突")
    if not rows:
        conn.execute(text("""
            INSERT INTO dim_vehicle_subtype (subtype_code, subtype_name, vehicle_type_id)
            VALUES (:subtype_code, :subtype_name, :type_id)
        """), payload)
    _postcheck(conn, """
        SELECT COUNT(*) FROM dim_vehicle_subtype
        WHERE subtype_code = :subtype_code AND subtype_name = :subtype_name AND vehicle_type_id = :type_id
    """, payload)


def _complete_ads(conn: Connection, key: str, values: dict) -> dict:
    if all(field in values for field in _ADS_COLUMNS):
        return values
    current = conn.execute(text("""
        SELECT model_name, ads_proportion_raw, ads_proportion_pct, ads_supplier, ads_supplier_mode,
               listed_at_raw, listed_at_date, listed_at_granularity
        FROM dim_model_ads_manual WHERE model_name = :key FOR UPDATE
    """), {"key": key}).mappings().one_or_none()
    if current is None or current["model_name"] != key:
        raise ValueError("现有 ADS 记录缺失或业务键冲突，须补齐 ADS 全部字段")
    merged = {field: values[field] if field in values else current.get(field) for field in _ADS_COLUMNS}
    pct = merged["ads_proportion_pct"]
    if not isinstance(pct, (int, Decimal)) or not Decimal(pct).is_finite() or not 0 <= pct <= 100:
        raise ValueError("现有 ADS 占比缺失或无效，请补齐 ADS 缺失字段")
    for field in ("ads_proportion_raw", "ads_supplier", "ads_supplier_mode", "listed_at_raw"):
        value = merged[field]
        if not isinstance(value, str) or value.strip() in {"", "未知", "占位", "不适用"}:
            raise ValueError("现有 ADS 数据缺失或无效，请补齐 ADS 缺失字段")
    if (
        not isinstance(merged["listed_at_date"], date)
        or merged["listed_at_date"].year < 1000
        or merged["listed_at_granularity"] not in {"day", "month", "quarter", "season"}
    ):
        raise ValueError("现有 ADS 上市时间缺失或无效，请补齐 ADS 缺失字段")
    return merged


def apply_ads(conn: Connection, key: str, values: dict) -> None:
    models = conn.execute(text(f"""
        SELECT m.id, m.model_name FROM dim_model m WHERE m.model_name = :key {_CURRENT_OEM} FOR UPDATE
    """), {"key": key}).mappings().all()
    if len(models) > 1 or (models and models[0]["model_name"] != key):
        raise ValueError("ADS 业务键未命中唯一车型，请先修正车型映射")
    model_id = models[0]["id"] if models else None
    merged = _complete_ads(conn, key, values)
    payload = {
        "model_id": model_id, "model_name": key, "source_file": FEISHU_SOURCE, **merged,
        "matched": int(model_id is not None),
        "missing_reason": "DIM_MODEL_NOT_FOUND" if model_id is None else None,
    }
    current_rows = [] if model_id is None else conn.execute(text("""
        SELECT id, ads_supplier, ads_supplier_mode, effective_start_date
        FROM dim_model_ads_zipper WHERE model_id = :model_id AND is_current = 1 FOR UPDATE
    """), payload).mappings().all()
    if len(current_rows) > 1:
        raise ValueError("该车型存在多个当前 ADS 版本，请先修正拉链记录")
    current = current_rows[0] if current_rows else None
    same_supplier = current is not None and all(
        current[field] == payload[field] for field in ("ads_supplier", "ads_supplier_mode")
    )
    if model_id is not None and not same_supplier:
        _validate_ads_period(conn, payload, current)

    # 欠缺项补录时，只更新此次提交的权威字段，拉链使用合并后的完整值。
    assignments = "".join(f"{field} = VALUES({field}), " for field in _ADS_COLUMNS if field in values)
    conn.execute(text(f"""
        INSERT INTO dim_model_ads_manual (
            model_name, ads_proportion_raw, ads_proportion_pct, ads_supplier, ads_supplier_mode,
            listed_at_raw, listed_at_date, listed_at_granularity, matched_model_id, is_in_dim_model,
            missing_in_dim_model_reason, match_checked_at, source_file
        ) VALUES (
            :model_name, :ads_proportion_raw, :ads_proportion_pct, :ads_supplier, :ads_supplier_mode,
            :listed_at_raw, :listed_at_date, :listed_at_granularity, :model_id, :matched, :missing_reason, NOW(), :source_file
        ) ON DUPLICATE KEY UPDATE
            {assignments}matched_model_id = VALUES(matched_model_id),
            is_in_dim_model = VALUES(is_in_dim_model),
            missing_in_dim_model_reason = VALUES(missing_in_dim_model_reason), match_checked_at = NOW(),
            source_file = VALUES(source_file)
    """), payload)
    if model_id is None:
        _postcheck(conn, """
            SELECT COUNT(*) FROM dim_model_ads_manual
            WHERE model_name = :model_name AND matched_model_id IS NULL AND is_in_dim_model = 0
        """, payload)
        return
    if same_supplier:
        conn.execute(text("""
            UPDATE dim_model_ads_zipper z JOIN dim_model dm ON dm.id = z.model_id
            SET z.model_name_snapshot = :model_name,
                z.brand_name_snapshot = COALESCE(NULLIF(TRIM(dm.brand_name), ''), '占位'),
                z.ads_proportion_pct = :ads_proportion_pct, z.listed_at_raw = :listed_at_raw,
                z.listed_at_date = :listed_at_date, z.listed_at_granularity = :listed_at_granularity,
                z.source_table = 'dim_model_ads_manual', z.source_note = :source_file
            WHERE z.id = :zipper_id
        """), {**payload, "zipper_id": current["id"]})
    else:
        _open_ads_version(conn, payload, current)

    conn.execute(text("""
        UPDATE dim_model dm JOIN dim_model_ads_zipper z ON z.model_id = dm.id AND z.is_current = 1
        SET dm.ads_id = z.id, dm.ads_supplier_name = z.ads_supplier WHERE dm.id = :model_id
    """), payload)
    _postcheck(conn, """
        SELECT COUNT(*) FROM dim_model dm
        JOIN dim_model_ads_zipper z ON z.id = dm.ads_id AND z.model_id = dm.id AND z.is_current = 1
        JOIN dim_model_ads_manual src ON src.matched_model_id = dm.id AND src.is_in_dim_model = 1
        WHERE dm.id = :model_id AND dm.ads_supplier_name = :ads_supplier
          AND z.ads_supplier = :ads_supplier AND z.ads_supplier_mode = :ads_supplier_mode
          AND z.ads_proportion_pct = :ads_proportion_pct AND z.listed_at_date = :listed_at_date
          AND src.model_name = :model_name AND src.ads_supplier = :ads_supplier
    """, payload)


def _validate_ads_period(conn: Connection, payload: dict, current) -> None:
    if current is not None and payload["listed_at_date"] <= current["effective_start_date"]:
        raise ValueError("新 ADS 版本日期必须晚于当前版本生效日")
    if conn.execute(text("""
        SELECT id FROM dim_model_ads_zipper
        WHERE model_id = :model_id AND is_current = 0 AND effective_end_date >= :listed_at_date
        LIMIT 1 FOR UPDATE
    """), payload).first():
        raise ValueError("新 ADS 版本日期与已有历史版本重叠")


def _open_ads_version(conn: Connection, payload: dict, current) -> None:
    if current is not None:
        conn.execute(text("""
            UPDATE dim_model_ads_zipper SET is_current = 0, effective_end_date = DATE_SUB(:listed_at_date, INTERVAL 1 DAY)
            WHERE id = :zipper_id
        """), {**payload, "zipper_id": current["id"]})
    conn.execute(text("""
        INSERT INTO dim_model_ads_zipper (
            model_id, model_name_snapshot, brand_name_snapshot, ads_proportion_pct, ads_supplier,
            ads_supplier_mode, listed_at_raw, listed_at_date, listed_at_granularity, effective_start_date,
            source_table, source_note
        ) SELECT dm.id, :model_name, COALESCE(NULLIF(TRIM(dm.brand_name), ''), '占位'), :ads_proportion_pct,
            :ads_supplier, :ads_supplier_mode, :listed_at_raw, :listed_at_date, :listed_at_granularity,
            :listed_at_date, 'dim_model_ads_manual', :source_file
        FROM dim_model dm WHERE dm.id = :model_id
    """), payload)


def _apply_ownership(conn: Connection, key: str, values: dict) -> None:
    from pipelines.dim.seed_dim_from_excel import CURRENT_EFFECTIVE_START_DATE, make_oem_code

    payload = {"oem_name": key, "oem_code": make_oem_code(key), "source_note": FEISHU_SOURCE, **values}
    oem = conn.execute(text("""
        SELECT o.id, o.oem_name, o.company_id, c.company_code
        FROM dim_oem o JOIN dim_company c ON c.id = o.company_id
        WHERE o.oem_name = :oem_name FOR UPDATE
    """), payload).mappings().one_or_none()
    if oem is not None and oem["oem_name"] != key:
        raise ValueError("主机厂名称与已有归属键冲突")

    if "company_code" in values:
        company = conn.execute(text("""
            SELECT id, company_name FROM dim_company WHERE BINARY company_code = BINARY :company_code
        """), payload).mappings().one_or_none()
    elif oem is not None:
        company = conn.execute(text("""
            SELECT id, company_name FROM dim_company WHERE id = :company_id
        """), {"company_id": oem["company_id"]}).mappings().one_or_none()
    else:
        raise ValueError("主机厂尚未建档，须填写 company_code")
    if company is None:
        raise ValueError("company_code 未命中已有公司")
    if oem is not None and oem["company_id"] != company["id"] and oem["company_code"] != "OTHER":
        raise ValueError("公司归属与已有主机厂记录冲突，须由管理员修正")

    payload.update(company_id=company["id"], company_name=company["company_name"])
    brand = None
    if "brand_code" in values:
        brand = conn.execute(text("""
            SELECT id, brand_name FROM dim_brand WHERE BINARY brand_code = BINARY :brand_code
        """), payload).mappings().one_or_none()
        if brand is None:
            raise ValueError("brand_code 未命中已有品牌")
        payload.update(brand_id=brand["id"], brand_name=brand["brand_name"], starts_on=CURRENT_EFFECTIVE_START_DATE)

    if oem is None:
        if brand is None:
            raise ValueError("主机厂尚未建档，须同时填写 brand_code")
        if not conn.execute(text("""
            SELECT 1 FROM ods_sales_monthly_model WHERE BINARY TRIM(manufacturer_name_raw) = BINARY :oem_name LIMIT 1
        """), payload).first():
            raise ValueError("业务键未命中销量来源中的主机厂")
        inserted = conn.execute(text("""
            INSERT INTO dim_oem (oem_code, oem_name, company_id, company_name_snapshot)
            VALUES (:oem_code, :oem_name, :company_id, :company_name)
        """), payload)
        payload["oem_id"] = inserted.lastrowid
    else:
        payload["oem_id"] = oem["id"]

    bridge = conn.execute(text("""
        SELECT brand_id, company_id, is_current, effective_end_date = '9999-12-31' AS open_ended
        FROM bridge_brand_oem WHERE oem_id = :oem_id FOR UPDATE
    """), payload).mappings().all()
    current = [row for row in bridge if row["is_current"] or row["open_ended"]]
    previous_company = oem["company_id"] if oem is not None else company["id"]
    if any(not row["is_current"] or not row["open_ended"]
           or row["company_id"] not in (previous_company, company["id"]) for row in current):
        raise ValueError("已有品牌归属冲突，须由管理员修正")
    if brand is not None and current and (len(current) != 1 or current[0]["brand_id"] != brand["id"]):
        raise ValueError("已有品牌归属冲突，须由管理员修正")
    if brand is not None and bridge and not current:
        raise ValueError("主机厂已有历史品牌归属，须由管理员确认生效期")

    if previous_company != company["id"]:
        payload["previous_company_id"] = previous_company
        conn.execute(text("""
            UPDATE dim_oem
            SET company_id = :company_id, company_name_snapshot = :company_name
            WHERE id = :oem_id AND company_id = :previous_company_id
        """), payload)
        if any(row["company_id"] == previous_company for row in current):
            conn.execute(text("""
                UPDATE bridge_brand_oem
                SET company_id = :company_id, company_name_snapshot = :company_name
                WHERE oem_id = :oem_id AND company_id = :previous_company_id
                  AND is_current = 1 AND effective_end_date = '9999-12-31'
            """), payload)
    if brand is not None and not current:
        conn.execute(text("""
            INSERT INTO bridge_brand_oem (
                brand_id, brand_name_snapshot, oem_id, oem_name_snapshot, company_id, company_name_snapshot,
                relation_type, effective_start_date, source_note
            ) VALUES (
                :brand_id, :brand_name, :oem_id, :oem_name, :company_id, :company_name, '主销', :starts_on, :source_note
            )
        """), payload)
    _postcheck(conn, """
        SELECT COUNT(*) FROM dim_oem o
        WHERE o.id = :oem_id AND o.oem_name = :oem_name AND o.company_id = :company_id
          AND NOT EXISTS (
              SELECT 1 FROM bridge_brand_oem b WHERE b.oem_id = o.id
                AND b.is_current = 1 AND b.effective_end_date = '9999-12-31'
                AND b.company_id <> :company_id
          )
    """, payload)
    if brand is not None:
        _postcheck(conn, """
            SELECT COUNT(*) FROM bridge_brand_oem
            WHERE oem_id = :oem_id AND company_id = :company_id AND brand_id = :brand_id
              AND is_current = 1 AND effective_end_date = '9999-12-31'
        """, payload)


def apply_feishu_dimension(conn: Connection, kind: str, key: str, values: dict) -> None:
    """写入已校验的补录项，并在同一事务内复查当前业务键。"""
    handlers = {
        "energy": _apply_energy, "ads": apply_ads, "area": _apply_area,
        "subtype": _apply_subtype, "ownership": _apply_ownership,
    }
    handlers[kind](conn, key, values)
