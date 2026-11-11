from __future__ import annotations

import argparse
import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from base_infra.db import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "manual_dimensions" / "dim_model_ads_matched.csv"
DEFAULT_REFRESH_SQL_PATH = PROJECT_ROOT / "db" / "manual" / "refresh_dim_model_ads_manual.sql"
DEFAULT_SOURCE_FILE = "data/manual_dimensions/dim_model_ads_matched.csv"

REQUIRED_COLUMNS = {"model_name", "ads_proportion", "ads_supplier", "ads_supplier_mode", "上市时间"}
EN_MONTH_FORMAT = "%b-%y"
QUARTER_MONTH_MAP = {"一": 1, "二": 4, "三": 7, "四": 10}
SEASON_MONTH_MAP = {"春": 3, "夏": 6, "秋": 9, "冬": 12}


def _read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_ads_proportion(value: str) -> float:
    """将占比原文统一转为百分比数值。"""
    cleaned = str(value).strip().replace("%", "")
    if not cleaned:
        raise ValueError("ADS_PROPORTION_EMPTY")
    return float(cleaned)


def normalize_listed_at(value: str) -> dict[str, object]:
    """将上市时间原文解析为日期和粒度。"""
    raw = str(value).strip()
    if not raw:
        raise ValueError("LISTED_AT_EMPTY")

    if re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", raw):
        parsed = datetime.strptime(raw.replace("-", "/"), "%Y/%m/%d").date()
        return {"listed_at_raw": raw, "listed_at_date": parsed, "listed_at_granularity": "day"}

    if re.fullmatch(r"[A-Za-z]{3}-\d{2}", raw):
        parsed = datetime.strptime(raw.title(), EN_MONTH_FORMAT).date().replace(day=1)
        return {"listed_at_raw": raw, "listed_at_date": parsed, "listed_at_granularity": "month"}

    # 兼容 YY-Mon 格式（如 21-Jun -> 2021-06-01）
    yy_mon_match = re.fullmatch(r"(\d{2})-([A-Za-z]{3})", raw)
    if yy_mon_match:
        year_num = 2000 + int(yy_mon_match.group(1))
        month_num = int(datetime.strptime(yy_mon_match.group(2).title(), "%b").month)
        return {
            "listed_at_raw": raw,
            "listed_at_date": date(year_num, month_num, 1),
            "listed_at_granularity": "month",
        }

    month_match = re.fullmatch(r"(\d{4})年(\d{1,2})月", raw)
    if month_match:
        year_num = int(month_match.group(1))
        month_num = int(month_match.group(2))
        return {
            "listed_at_raw": raw,
            "listed_at_date": date(year_num, month_num, 1),
            "listed_at_granularity": "month",
        }

    quarter_match = re.fullmatch(r"(\d{4})年([一二三四])季度", raw)
    if quarter_match:
        year_num = int(quarter_match.group(1))
        month_num = QUARTER_MONTH_MAP[quarter_match.group(2)]
        return {
            "listed_at_raw": raw,
            "listed_at_date": date(year_num, month_num, 1),
            "listed_at_granularity": "quarter",
        }

    season_match = re.fullmatch(r"(\d{4})年([春夏秋冬])季", raw)
    if season_match:
        year_num = int(season_match.group(1))
        month_num = SEASON_MONTH_MAP[season_match.group(2)]
        return {
            "listed_at_raw": raw,
            "listed_at_date": date(year_num, month_num, 1),
            "listed_at_granularity": "season",
        }

    raise ValueError(f"LISTED_AT_UNSUPPORTED:{raw}")


def read_dim_model_ads_csv(csv_path: Path) -> list[dict]:
    """读取并校验 ADS 手工维护 CSV。"""
    content = _read_text_with_fallback(csv_path)
    with io.StringIO(content, newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        if reader.fieldnames is None:
            raise ValueError("CSV_NO_HEADER")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV_MISSING_COLUMNS:{sorted(missing)}")

        rows: list[dict] = []
        invalid_rows: list[str] = []
        for row in reader:
            model_name = str(row.get("model_name", "")).strip()
            ads_proportion_raw = str(row.get("ads_proportion", "")).strip()
            ads_supplier = str(row.get("ads_supplier", "")).strip()
            ads_supplier_mode = str(row.get("ads_supplier_mode", "")).strip()
            listed_at_raw = str(row.get("上市时间", "")).strip()

            if not model_name:
                continue

            try:
                normalize_ads_proportion(ads_proportion_raw)
                normalize_listed_at(listed_at_raw)
            except ValueError as exc:
                invalid_rows.append(f"{model_name}:{exc}")
                continue

            if not ads_supplier or not ads_supplier_mode:
                invalid_rows.append(f"{model_name}:SUPPLIER_EMPTY")
                continue

            rows.append(
                {
                    "model_name": model_name,
                    "ads_proportion_raw": ads_proportion_raw,
                    "ads_supplier": ads_supplier,
                    "ads_supplier_mode": ads_supplier_mode,
                    "listed_at_raw": listed_at_raw,
                    "source_file": DEFAULT_SOURCE_FILE,
                },
            )

        if invalid_rows:
            raise ValueError(f"CSV_INVALID_ROWS:{invalid_rows[:10]}")

        return rows


def execute_sql_file(sql_path: Path) -> None:
    """执行刷新 SQL 文件。"""
    sql_content = sql_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in sql_content.split(";"):
            lines = statement.split("\n")
            sql_lines = [line for line in lines if not line.strip().startswith("--")]
            clean = "\n".join(sql_lines).strip()
            if clean:
                conn.execute(text(clean))


def refresh_dim_model_ads(
    csv_path: Path = DEFAULT_CSV_PATH,
    refresh_sql_path: Path = DEFAULT_REFRESH_SQL_PATH,
) -> None:
    """装载 CSV 到暂存表，并执行 SQL 刷新。"""
    rows = read_dim_model_ads_csv(csv_path)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE stg_dim_model_ads_manual"))
        conn.execute(
            text(
                """
                INSERT INTO stg_dim_model_ads_manual (
                    model_name,
                    ads_proportion_raw,
                    ads_supplier,
                    ads_supplier_mode,
                    listed_at_raw,
                    source_file
                ) VALUES (
                    :model_name,
                    :ads_proportion_raw,
                    :ads_supplier,
                    :ads_supplier_mode,
                    :listed_at_raw,
                    :source_file
                )
                """
            ),
            rows,
        )

    execute_sql_file(refresh_sql_path)

    with engine.begin() as conn:
        manual_count = conn.execute(text("SELECT COUNT(*) FROM dim_model_ads_manual")).scalar_one()
        missing_count = conn.execute(
            text("SELECT COUNT(*) FROM dim_model_ads_manual WHERE is_in_dim_model = 0"),
        ).scalar_one()
        zipper_current_count = conn.execute(
            text("SELECT COUNT(*) FROM dim_model_ads_zipper WHERE is_current = 1"),
        ).scalar_one()
        print(
            "REFRESH_DONE "
            f"dim_model_ads_manual={manual_count} "
            f"dim_model_ads_manual_missing={missing_count} "
            f"dim_model_ads_zipper_current={zipper_current_count}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="刷新 ADS 手工权威存储表及其 dim_model 子维度拉链")
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
        help="刷新 SQL 脚本路径（默认 db/manual/refresh_dim_model_ads_manual.sql）",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    refresh_dim_model_ads(csv_path=Path(args.csv_path), refresh_sql_path=Path(args.sql_path))


if __name__ == "__main__":
    main()
