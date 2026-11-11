from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from base_infra.db import engine
from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# 手工维度缺失率阈值：超过则阻断链路，等待人工补维
# energy_type 为必填项，零容忍：缺 1 个即阻断并告警
DEFAULT_ENERGY_TYPE_THRESHOLD = 0.0
DEFAULT_VEHICLE_TYPE_THRESHOLD = 0.05
DEFAULT_ADS_THRESHOLD = 0.05
DEFAULT_AREA_THRESHOLD = 0.05
DEFAULT_VEHICLE_SUBTYPE_THRESHOLD = 0.05


@dataclass
class DimensionReadinessReport:
    """维度就绪报告，用于在 ETL 关键节点做门控。"""

    total_models: int
    placeholder_energy_type: int
    placeholder_vehicle_type: int
    missing_ads: int
    missing_area_matches: int
    missing_vehicle_subtype_matches: int
    _known_area_rows: int = 0
    _known_subtype_rows: int = 0

    energy_type_threshold: float = DEFAULT_ENERGY_TYPE_THRESHOLD
    vehicle_type_threshold: float = DEFAULT_VEHICLE_TYPE_THRESHOLD
    ads_threshold: float = DEFAULT_ADS_THRESHOLD
    area_threshold: float = DEFAULT_AREA_THRESHOLD
    vehicle_subtype_threshold: float = DEFAULT_VEHICLE_SUBTYPE_THRESHOLD

    missing_energy_models: Sequence[str] | None = None
    missing_vehicle_type_models: Sequence[str] | None = None
    missing_ads_models: Sequence[str] | None = None
    missing_area_cities: Sequence[str] | None = None
    missing_vehicle_subtypes: Sequence[str] | None = None

    @property
    def energy_type_ready_rate(self) -> float:
        return 1.0 - (self.placeholder_energy_type / self.total_models) if self.total_models else 1.0

    @property
    def vehicle_type_ready_rate(self) -> float:
        return 1.0 - (self.placeholder_vehicle_type / self.total_models) if self.total_models else 1.0

    @property
    def ads_ready_rate(self) -> float:
        return 1.0 - (self.missing_ads / self.total_models) if self.total_models else 1.0

    def is_ready(self) -> bool:
        return (
            self.energy_type_ready_rate >= (1.0 - self.energy_type_threshold)
            and self.vehicle_type_ready_rate >= (1.0 - self.vehicle_type_threshold)
            and self.ads_ready_rate >= (1.0 - self.ads_threshold)
            and self.area_ready_rate >= (1.0 - self.area_threshold)
            and self.vehicle_subtype_ready_rate >= (1.0 - self.vehicle_subtype_threshold)
        )

    @property
    def area_ready_rate(self) -> float:
        total = self.missing_area_matches + self._known_area_rows
        if total == 0:
            return 1.0
        return 1.0 - (self.missing_area_matches / total)

    @property
    def vehicle_subtype_ready_rate(self) -> float:
        total = self.missing_vehicle_subtype_matches + self._known_subtype_rows
        if total == 0:
            return 1.0
        return 1.0 - (self.missing_vehicle_subtype_matches / total)

    def to_human(self) -> str:
        lines = [
            f"维度就绪报告 (目标月模型数: {self.total_models})",
            f"  energy_type 占位: {self.placeholder_energy_type} ({self.energy_type_ready_rate:.2%})",
            f"  vehicle_type 占位: {self.placeholder_vehicle_type} ({self.vehicle_type_ready_rate:.2%})",
            f"  ads 缺失: {self.missing_ads} ({self.ads_ready_rate:.2%})",
            f"  area 未匹配: {self.missing_area_matches} ({self.area_ready_rate:.2%})",
            f"  vehicle_subtype 未匹配: {self.missing_vehicle_subtype_matches} ({self.vehicle_subtype_ready_rate:.2%})",
        ]
        if not self.is_ready():
            lines.append("状态: 未就绪，请按下方清单补维后重试")
        else:
            lines.append("状态: 就绪")
        return "\n".join(lines)


def _parse_month_to_first_day(p_month: str | date | None) -> date | None:
    if p_month is None:
        return None
    if isinstance(p_month, date):
        return p_month
    if isinstance(p_month, str):
        return datetime.strptime(p_month[:7], "%Y-%m").date()
    raise TypeError(f"不支持的月份类型: {type(p_month)}")


def check_dimension_readiness(
    target_month: str | date | None = None,
    energy_type_threshold: float = DEFAULT_ENERGY_TYPE_THRESHOLD,
    vehicle_type_threshold: float = DEFAULT_VEHICLE_TYPE_THRESHOLD,
    ads_threshold: float = DEFAULT_ADS_THRESHOLD,
    area_threshold: float = DEFAULT_AREA_THRESHOLD,
    vehicle_subtype_threshold: float = DEFAULT_VEHICLE_SUBTYPE_THRESHOLD,
) -> DimensionReadinessReport:
    """检查目标月维度就绪情况。

    target_month 为 YYYY-MM 字符串或 date；None 表示全量。
    """
    m = _parse_month_to_first_day(target_month)

    with engine.connect() as conn:
        total_models = _count_target_models(conn, m)
        (
            placeholder_energy,
            placeholder_vehicle,
            missing_ads,
            missing_energy_models,
            missing_vehicle_models,
            missing_ads_models,
        ) = _check_model_dimension_readiness(conn, m)
        missing_area, known_area_rows, missing_cities = _check_area_readiness(conn, m)
        missing_subtype, known_subtype_rows, missing_subtypes = _check_vehicle_subtype_readiness(conn, m)

    return DimensionReadinessReport(
        total_models=total_models,
        placeholder_energy_type=placeholder_energy,
        placeholder_vehicle_type=placeholder_vehicle,
        missing_ads=missing_ads,
        missing_area_matches=missing_area,
        missing_vehicle_subtype_matches=missing_subtype,
        energy_type_threshold=energy_type_threshold,
        vehicle_type_threshold=vehicle_type_threshold,
        ads_threshold=ads_threshold,
        area_threshold=area_threshold,
        vehicle_subtype_threshold=vehicle_subtype_threshold,
        missing_energy_models=missing_energy_models,
        missing_vehicle_type_models=missing_vehicle_models,
        missing_ads_models=missing_ads_models,
        missing_area_cities=missing_cities,
        missing_vehicle_subtypes=missing_subtypes,
        _known_area_rows=known_area_rows,
        _known_subtype_rows=known_subtype_rows,
    )


def _count_target_models(conn: Connection, target_month: date | None) -> int:
    month_clause = "AND stat_month = :p_month" if target_month else ""
    row = conn.execute(
        text(
            f"""
            SELECT COUNT(DISTINCT model_id)
            FROM dwd_sales_monthly_model
            WHERE 1=1 {month_clause}
            """
        ),
        {"p_month": target_month},
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _check_model_dimension_readiness(
    conn: Connection,
    target_month: date | None,
) -> tuple[int, int, int, list[str], list[str], list[str]]:
    """返回 (energy占位, vehicle_type占位, ads缺失, 对应车型名列表...)。"""
    month_clause = "AND dwd.stat_month = :p_month" if target_month else ""

    sql = f"""
        SELECT
            COUNT(DISTINCT CASE WHEN dm.energy_type = '占位' THEN dwd.model_id END) AS placeholder_energy,
            COUNT(DISTINCT CASE WHEN dm.vehicle_type = '占位' THEN dwd.model_id END) AS placeholder_vehicle,
            COUNT(DISTINCT CASE WHEN dm.ads_id IS NULL THEN dwd.model_id END) AS missing_ads,
            GROUP_CONCAT(DISTINCT CASE WHEN dm.energy_type = '占位' THEN dwd.model_name END) AS energy_models,
            GROUP_CONCAT(DISTINCT CASE WHEN dm.vehicle_type = '占位' THEN dwd.model_name END) AS vehicle_models,
            GROUP_CONCAT(DISTINCT CASE WHEN dm.ads_id IS NULL THEN dwd.model_name END) AS ads_models
        FROM dwd_sales_monthly_model dwd
        JOIN dim_model dm ON dm.id = dwd.model_id
        WHERE 1=1 {month_clause}
    """
    row = conn.execute(text(sql), {"p_month": target_month}).fetchone()
    if not row:
        return 0, 0, 0, [], [], []
    placeholder_energy = int(row[0] or 0)
    placeholder_vehicle = int(row[1] or 0)
    missing_ads = int(row[2] or 0)
    energy_models = _split_group_concat(row[3])
    vehicle_models = _split_group_concat(row[4])
    ads_models = _split_group_concat(row[5])
    return placeholder_energy, placeholder_vehicle, missing_ads, energy_models, vehicle_models, ads_models


def _check_area_readiness(
    conn: Connection,
    target_month: date | None,
) -> tuple[int, int, list[str]]:
    month_clause = "AND dwd.stat_month = :p_month" if target_month else ""
    sql = f"""
        SELECT
            SUM(CASE WHEN dwd.area_id IS NULL THEN 1 ELSE 0 END) AS missing,
            COUNT(*) AS total,
            GROUP_CONCAT(DISTINCT CASE WHEN dwd.area_id IS NULL THEN dwd.city_name_raw END) AS missing_cities
        FROM dwd_sales_monthly_model_city dwd
        WHERE dwd.city_name_raw IS NOT NULL {month_clause}
    """
    row = conn.execute(text(sql), {"p_month": target_month}).fetchone()
    if not row:
        return 0, 0, []
    missing = int(row[0] or 0)
    total = int(row[1] or 0)
    cities = _split_group_concat(row[2])
    return missing, total - missing, cities


def _check_vehicle_subtype_readiness(
    conn: Connection,
    target_month: date | None,
) -> tuple[int, int, list[str]]:
    month_clause = "AND dwd.stat_month = :p_month" if target_month else ""
    sql = f"""
        SELECT
            SUM(CASE WHEN dwd.vehicle_subtype_id IS NULL THEN 1 ELSE 0 END) AS missing,
            COUNT(*) AS total,
            GROUP_CONCAT(DISTINCT CASE WHEN dwd.vehicle_subtype_id IS NULL THEN dwd.level_raw END) AS missing_subtypes
        FROM dwd_sales_monthly_model_city dwd
        WHERE dwd.level_raw IS NOT NULL {month_clause}
    """
    row = conn.execute(text(sql), {"p_month": target_month}).fetchone()
    if not row:
        return 0, 0, []
    missing = int(row[0] or 0)
    total = int(row[1] or 0)
    subtypes = _split_group_concat(row[2])
    return missing, total - missing, subtypes


def _split_group_concat(value: object | None) -> list[str]:
    if value is None:
        return []
    return [s.strip() for s in str(value).split(",") if s.strip()]


def generate_missing_dimension_csv(
    report: DimensionReadinessReport,
    target_month: str | None = None,
) -> str:
    """生成可直接复制到手工 CSV 的补维清单文本。"""
    lines: list[str] = ["# 待补维清单", f"# 目标月: {target_month or '全量'}"]

    if report.missing_energy_models or report.missing_vehicle_type_models:
        lines.append("\n# dim_energy_vehicle.csv 待补（model_name,energy_type,vehicle_type）")
        models = set(report.missing_energy_models or []) | set(report.missing_vehicle_type_models or [])
        for model_name in sorted(models):
            lines.append(f'{model_name},,')

    if report.missing_ads_models:
        lines.append("\n# dim_model_ads_matched.csv 待补（model_name,ads_proportion,ads_supplier,ads_supplier_mode,上市时间）")
        for model_name in sorted(report.missing_ads_models):
            lines.append(f'{model_name},,,,')

    if report.missing_area_cities:
        lines.append("\n# dim_area.csv 待补城市")
        for city in sorted(report.missing_area_cities):
            lines.append(f"{city},")

    if report.missing_vehicle_subtypes:
        lines.append("\n# dim_vehicle_subtype 待补级别")
        for subtype in sorted(report.missing_vehicle_subtypes):
            lines.append(f"{subtype},")

    return "\n".join(lines)
