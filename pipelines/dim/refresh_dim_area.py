"""刷新地区维度表 dim_area（大区/省/地级市）。

从 data/manual_dimensions/dim_area.csv 装载至 stg_dim_area，
再执行 db/manual/refresh_dim_area.sql 完成主表 UPSERT。
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from sqlalchemy import text

from base_infra.db import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "manual_dimensions" / "dim_area.csv"
DEFAULT_REFRESH_SQL_PATH = PROJECT_ROOT / "db" / "manual" / "refresh_dim_area.sql"

# CSV 白名单校验取值，与 dim_area 表 ENUM 保持一致
ALLOWED_REGION_NAMES = {"华东", "华南", "华北", "华中", "西南", "西北", "东北"}
ALLOWED_CITY_LEVELS = {"地级市", "直辖市", "自治州", "地区", "盟"}
ALLOWED_PROVINCE_TYPES = {"省", "自治区", "直辖市"}

REQUIRED_COLUMNS = {
    "city_code",
    "city_name",
    "city_short_name",
    "city_level",
    "province_code",
    "province_name",
    "province_short_name",
    "province_type",
    "region_code",
    "region_name",
}


def _read_text_with_fallback(path: Path) -> str:
    """按 utf-8-sig → utf-8 → gbk 顺序尝试解码。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_dim_area_csv(csv_path: Path) -> list[dict]:
    """读取 CSV 并做必填列与白名单值校验。"""
    content = _read_text_with_fallback(csv_path)
    with io.StringIO(content, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV_NO_HEADER")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV_MISSING_COLUMNS:{sorted(missing)}")

        rows: list[dict] = []
        invalid_rows: list[tuple] = []
        for row in reader:
            city_code = str(row.get("city_code", "")).strip()
            if not city_code:                                    # 跳过空行
                continue

            city_level = str(row.get("city_level", "")).strip()
            province_type = str(row.get("province_type", "")).strip()
            region_name = str(row.get("region_name", "")).strip()

            # 白名单校验：任一维度不在允许集合内即视为非法
            if (
                city_level not in ALLOWED_CITY_LEVELS
                or province_type not in ALLOWED_PROVINCE_TYPES
                or region_name not in ALLOWED_REGION_NAMES
            ):
                invalid_rows.append((city_code, city_level, province_type, region_name))
                continue

            rows.append(
                {
                    "city_code": city_code,
                    "city_name": str(row.get("city_name", "")).strip(),
                    "city_short_name": str(row.get("city_short_name", "")).strip(),
                    "city_level": city_level,
                    "province_code": str(row.get("province_code", "")).strip(),
                    "province_name": str(row.get("province_name", "")).strip(),
                    "province_short_name": str(row.get("province_short_name", "")).strip(),
                    "province_type": province_type,
                    "region_code": str(row.get("region_code", "")).strip(),
                    "region_name": region_name,
                    "source_file": "data/manual_dimensions/dim_area.csv",
                },
            )

        if invalid_rows:
            raise ValueError(f"CSV_INVALID_ROWS:{invalid_rows[:10]}")

        return rows


def execute_sql_file(sql_path: Path) -> None:
    """按分号拆分执行 SQL 文件，忽略注释与空行。"""
    sql_content = sql_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in sql_content.split(";"):
            lines = statement.split("\n")
            sql_lines = [ln for ln in lines if not ln.strip().startswith("--")]
            clean = "\n".join(sql_lines).strip()
            if clean:
                conn.execute(text(clean))


def refresh_dim_area(
    csv_path: Path = DEFAULT_CSV_PATH,
    refresh_sql_path: Path = DEFAULT_REFRESH_SQL_PATH,
) -> None:
    """执行 dim_area 刷新主流程。"""
    rows = read_dim_area_csv(csv_path)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE stg_dim_area"))       # 清空暂存表
        conn.execute(
            text(
                "INSERT INTO stg_dim_area ("
                "city_code, city_name, city_short_name, city_level, "
                "province_code, province_name, province_short_name, province_type, "
                "region_code, region_name, source_file) "
                "VALUES ("
                ":city_code, :city_name, :city_short_name, :city_level, "
                ":province_code, :province_name, :province_short_name, :province_type, "
                ":region_code, :region_name, :source_file)"
            ),
            rows,
        )

    execute_sql_file(refresh_sql_path)                          # 执行 UPSERT+清理

    with engine.begin() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM dim_area")).scalar_one()
        region_cnt = conn.execute(
            text("SELECT COUNT(DISTINCT region_code) FROM dim_area"),
        ).scalar_one()
        province_cnt = conn.execute(
            text("SELECT COUNT(DISTINCT province_code) FROM dim_area"),
        ).scalar_one()
        print(
            f"REFRESH_DONE dim_area={total} region={region_cnt} province={province_cnt}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="刷新地区维度表 dim_area")
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=str(DEFAULT_CSV_PATH),
        help="手工维护 CSV 路径",
    )
    parser.add_argument(
        "--sql",
        dest="sql_path",
        default=str(DEFAULT_REFRESH_SQL_PATH),
        help="刷新 SQL 脚本路径（默认 db/manual/refresh_dim_area.sql）",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    refresh_dim_area(
        csv_path=Path(args.csv_path),
        refresh_sql_path=Path(args.sql_path),
    )


if __name__ == "__main__":
    main()
