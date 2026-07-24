from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from sqlalchemy import text

from base_infra.db import engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "manual_dimensions" / "dim_energy_vehicle.csv"
DEFAULT_REFRESH_SQL_PATH = PROJECT_ROOT / "db" / "manual" / "refresh_dim_energy_vehicle.sql"


ALLOWED_ENERGY_TYPES = {"燃油", "新能源", "其他", "占位"}
ALLOWED_VEHICLE_TYPES = {"轿车", "SUV", "MPV", "其他", "占位"}


def _read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_dim_energy_vehicle_csv(csv_path: Path) -> list[dict]:
    content = _read_text_with_fallback(csv_path)
    with io.StringIO(content, newline="") as f:
        reader = csv.DictReader(f)
        required = {"model_name", "energy_type", "vehicle_type"}
        if reader.fieldnames is None:
            raise ValueError("CSV_NO_HEADER")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV_MISSING_COLUMNS:{sorted(missing)}")

        rows: list[dict] = []
        invalid_rows: list[tuple[str, str, str]] = []
        for row in reader:
            model_name = str(row.get("model_name", "")).strip()
            energy_type = str(row.get("energy_type", "")).strip()
            vehicle_type = str(row.get("vehicle_type", "")).strip()

            if not model_name:
                continue

            if energy_type not in ALLOWED_ENERGY_TYPES or vehicle_type not in ALLOWED_VEHICLE_TYPES:
                invalid_rows.append((model_name, energy_type, vehicle_type))
                continue

            rows.append(
                {
                    "model_name": model_name,
                    "energy_type": energy_type,
                    "vehicle_type": vehicle_type,
                    "source_file": "data/manual_dimensions/dim_energy_vehicle.csv",
                },
            )

        if invalid_rows:
            raise ValueError(f"CSV_INVALID_ROWS:{invalid_rows[:10]}")

        return rows


def execute_sql_file(sql_path: Path) -> None:
    sql_content = sql_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in sql_content.split(";"):
            lines = statement.split("\n")
            sql_lines = [ln for ln in lines if not ln.strip().startswith("--")]
            clean = "\n".join(sql_lines).strip()
            if clean:
                conn.execute(text(clean))


def refresh_dim_energy_vehicle(
    csv_path: Path = DEFAULT_CSV_PATH,
    refresh_sql_path: Path = DEFAULT_REFRESH_SQL_PATH,
) -> None:
    rows = read_dim_energy_vehicle_csv(csv_path)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE stg_dim_energy_vehicle"))
        conn.execute(
            text(
                "INSERT INTO stg_dim_energy_vehicle (model_name, energy_type, vehicle_type, source_file) "
                "VALUES (:model_name, :energy_type, :vehicle_type, :source_file)",
            ),
            rows,
        )

    execute_sql_file(refresh_sql_path)

    with engine.begin() as conn:
        dim_count = conn.execute(text("SELECT COUNT(*) FROM dim_energy_vehicle")).scalar_one()
        placeholder_count = conn.execute(
            text("SELECT COUNT(*) FROM dim_model WHERE energy_type='占位' OR vehicle_type='占位'"),
        ).scalar_one()
        print(f"REFRESH_DONE dim_energy_vehicle={dim_count} dim_model_placeholder={placeholder_count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="刷新车型能源/车型类型手工维度表")
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
        help="刷新 SQL 脚本路径（默认 db/manual/refresh_dim_energy_vehicle.sql）",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    refresh_dim_energy_vehicle(csv_path=Path(args.csv_path), refresh_sql_path=Path(args.sql_path))


if __name__ == "__main__":
    main()
