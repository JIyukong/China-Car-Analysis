from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from base_infra.db import engine
from sqlalchemy import text

from pipelines.dim.build_sales_dimensions import (
    build_oem_brand_lookup,
    render_dim_model_upsert_sql,
)
from pipelines.dim.seed_dim_company_stock import COMPANIES
from pipelines.dim.seed_dim_from_excel import (
    CURRENT_EFFECTIVE_END_DATE,
    build_bridge_payload,
)
from pipelines.dim.seed_dim_oem_brand import BRANDS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCEL_PATH = PROJECT_ROOT / "汽车品牌主机厂信息表.xlsx"
DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "01_create_tables.sql"
DIM_ENERGY_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "07_create_dim_energy_vehicle.sql"
TRIGGER_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "08_create_dim_energy_vehicle_triggers.sql"
ADS_MANUAL_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "09_create_dim_model_ads_manual.sql"
ADS_SYNC_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "10_create_dim_model_ads_sync.sql"
ADS_VIEW_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "11_create_dim_model_ads_views.sql"
ADS_DIM_MODEL_REF_DDL_PATH = PROJECT_ROOT / "db" / "ddl" / "12_alter_dim_model_ads_reference.sql"
MANUAL_DIM_CSV_PATH = PROJECT_ROOT / "data" / "manual_dimensions" / "dim_energy_vehicle.csv"
MANUAL_ADS_CSV_PATH = PROJECT_ROOT / "data" / "manual_dimensions" / "dim_model_ads_matched.csv"
LEGACY_DIM_CSV_PATH = PROJECT_ROOT / "dim_energy_vehicle.csv"


def read_csv_with_fallback(csv_path: Path) -> pd.DataFrame:
    """按项目约定兼容 UTF-8/GBK 手工 CSV。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return pd.read_csv(csv_path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", b"", 0, 1, f"无法解码文件: {csv_path}")


class SalesDimensionRulesTest(unittest.TestCase):
    def test_manual_dimension_csv_should_live_under_data_manual_dimensions(self) -> None:
        """手工维护 CSV 应迁移到专用目录，避免污染项目根目录。"""
        self.assertTrue(MANUAL_DIM_CSV_PATH.exists())
        self.assertFalse(LEGACY_DIM_CSV_PATH.exists())

    def test_manual_dimension_csv_should_have_required_columns(self) -> None:
        """手工维护 CSV 必须包含车型名、能源类型、车型类型三列。"""
        df = read_csv_with_fallback(MANUAL_DIM_CSV_PATH)
        required = {"model_name", "energy_type", "vehicle_type"}
        self.assertTrue(required.issubset(set(df.columns)))

    def test_manual_ads_csv_should_have_required_columns(self) -> None:
        """ADS 手工维护 CSV 必须包含车型、占比、供应商、模式、上市时间。"""
        df = read_csv_with_fallback(MANUAL_ADS_CSV_PATH)
        required = {"model_name", "ads_proportion", "ads_supplier", "ads_supplier_mode", "上市时间"}
        self.assertTrue(required.issubset(set(df.columns)))

    def test_brands_seed_should_contain_other(self) -> None:
        """公司种子和品牌维表都必须保留 OTHER 占位记录。"""
        company_codes = {item["company_code"] for item in COMPANIES}
        self.assertIn("OTHER", company_codes)
        brand_codes = {item["brand_code"] for item in BRANDS}
        self.assertIn("OTHER", brand_codes)

        with engine.connect() as conn:
            other_brand = conn.execute(
                text("SELECT COUNT(*) FROM dim_brand WHERE brand_code = 'OTHER'"),
            ).scalar_one()
        self.assertGreaterEqual(other_brand, 1)

    def test_excel_should_have_required_columns(self) -> None:
        """Excel 文件必须包含三列：主机厂名称、品牌、上市公司。"""
        if not EXCEL_PATH.exists():
            self.skipTest("历史 Excel 文件不存在，跳过列结构校验")
        df = pd.read_excel(EXCEL_PATH, sheet_name=0)
        required = {"主机厂名称", "品牌", "上市公司"}
        self.assertTrue(required.issubset(set(df.columns)))

    def test_excel_oem_names_should_be_unique(self) -> None:
        """Excel 中主机厂名称不应有重复。"""
        if not EXCEL_PATH.exists():
            self.skipTest("历史 Excel 文件不存在，跳过主机厂唯一性校验")
        df = pd.read_excel(EXCEL_PATH, sheet_name=0)
        names = df["主机厂名称"].dropna().astype(str).str.strip()
        duplicates = names[names.duplicated()].tolist()
        self.assertEqual(len(duplicates), 0, f"重复主机厂：{duplicates}")

    def test_dim_model_upsert_sql_should_include_snapshot_columns_when_present(self) -> None:
        """真实库存在快照字段时，补维 SQL 必须显式写入。"""
        sql = render_dim_model_upsert_sql(
            {
                "model_code",
                "model_name",
                "brand_id",
                "brand_name",
                "oem_id",
                "oem_name",
                "ads_id",
                "ads_supplier_name",
                "energy_type",
                "vehicle_type",
            },
        )
        self.assertIn("brand_name", sql)
        self.assertIn("oem_name", sql)
        self.assertIn("ads_id", sql)
        self.assertIn("ads_supplier_name", sql)
        self.assertIn("energy_type", sql)
        self.assertIn("vehicle_type", sql)

    def test_dim_model_upsert_sql_should_keep_base_columns_when_snapshot_columns_absent(self) -> None:
        """旧版表结构下，补维 SQL 仍应保持最小字段集。"""
        sql = render_dim_model_upsert_sql(
            {"model_code", "model_name", "brand_id", "oem_id"},
        )
        self.assertNotIn("brand_name", sql)
        self.assertNotIn("oem_name", sql)
        self.assertNotIn("ads_id", sql)
        self.assertNotIn("ads_supplier_name", sql)

    def test_dim_model_schema_should_define_snapshot_and_ads_reference_columns(self) -> None:
        """DDL 必须显式定义 dim_model 的名称冗余字段和 ADS 引用字段。"""
        ddl = DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("brand_name", ddl)
        self.assertIn("oem_name", ddl)
        self.assertIn("ads_id            BIGINT UNSIGNED", ddl)
        self.assertIn("ads_supplier_name", ddl)
        self.assertIn("energy_type", ddl)
        self.assertIn("vehicle_type", ddl)

    def test_dim_energy_vehicle_schema_files_should_exist(self) -> None:
        """应提供独立的维度表 DDL 和触发器 DDL。"""
        self.assertTrue(DIM_ENERGY_DDL_PATH.exists())
        self.assertTrue(TRIGGER_DDL_PATH.exists())

    def test_dim_model_ads_schema_files_should_exist(self) -> None:
        """应提供 ADS 手工表 DDL 和同步 SQL DDL。"""
        self.assertTrue(ADS_MANUAL_DDL_PATH.exists())
        self.assertTrue(ADS_SYNC_DDL_PATH.exists())
        self.assertTrue(ADS_VIEW_DDL_PATH.exists())
        self.assertTrue(ADS_DIM_MODEL_REF_DDL_PATH.exists())

    def test_dim_energy_vehicle_schema_should_define_main_and_staging_tables(self) -> None:
        """DDL 必须显式定义主表和暂存表。"""
        ddl = DIM_ENERGY_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("dim_energy_vehicle", ddl)
        self.assertIn("stg_dim_energy_vehicle", ddl)

    def test_trigger_ddl_should_define_dim_model_backfill_triggers(self) -> None:
        """触发器 DDL 必须覆盖 dim_energy_vehicle 和 dim_model 的补位逻辑。"""
        ddl = TRIGGER_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE TRIGGER trg_dim_energy_vehicle_ai", ddl)
        self.assertIn("CREATE TRIGGER trg_dim_energy_vehicle_au", ddl)
        self.assertIn("CREATE TRIGGER trg_dim_energy_vehicle_ad", ddl)
        self.assertIn("CREATE TRIGGER trg_dim_model_bi", ddl)
        self.assertIn("CREATE TRIGGER trg_dim_model_bu", ddl)

    def test_dim_model_ads_manual_schema_should_define_main_and_staging_tables(self) -> None:
        """ADS 手工表 DDL 必须显式定义主表和暂存表。"""
        ddl = ADS_MANUAL_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("dim_model_ads_manual", ddl)
        self.assertIn("stg_dim_model_ads_manual", ddl)
        self.assertIn("is_in_dim_model", ddl)
        self.assertIn("missing_in_dim_model_reason", ddl)

    def test_dim_model_ads_sync_sql_should_define_procedures_and_event(self) -> None:
        """ADS 同步 SQL 必须包含匹配、拉链刷新和事件定义。"""
        ddl = ADS_SYNC_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("sp_refresh_dim_model_ads_manual_match", ddl)
        self.assertIn("sp_refresh_dim_model_ads_zipper", ddl)
        self.assertIn("sp_refresh_dim_model_ads_all", ddl)
        self.assertIn("ev_refresh_dim_model_ads_all", ddl)
        self.assertIn("UPDATE dim_model dm", ddl)
        self.assertIn("dm.ads_id = current_zip.id", ddl)
        self.assertIn("dm.ads_supplier_name = current_zip.ads_supplier", ddl)

    def test_dim_model_ads_view_sql_should_define_missing_models_view(self) -> None:
        """ADS 视图 DDL 必须提供未命中 dim_model 的只读清单视图。"""
        ddl = ADS_VIEW_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE OR REPLACE VIEW vw_dim_model_ads_manual_missing_models", ddl)
        self.assertIn("FROM dim_model_ads_manual", ddl)
        self.assertIn("is_in_dim_model = 0", ddl)
        self.assertIn("missing_in_dim_model_reason", ddl)

    def test_dim_model_ads_reference_ddl_should_define_fk_to_zipper(self) -> None:
        """dim_model 的 ADS 引用字段必须收敛为拉链表外键。"""
        ddl = ADS_DIM_MODEL_REF_DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE dim_model MODIFY COLUMN ads_id BIGINT UNSIGNED NULL", ddl)
        self.assertIn("ALTER TABLE dim_model MODIFY COLUMN ads_supplier_name VARCHAR(128) NULL", ddl)
        self.assertIn("FOREIGN KEY (ads_id) REFERENCES dim_model_ads_zipper(id)", ddl)

    def test_dim_model_ads_zipper_schema_should_define_ads_snapshot_columns(self) -> None:
        """ADS 子维度拉链表必须包含 ADS 快照与当前有效结束哨兵。"""
        ddl = (PROJECT_ROOT / "db" / "ddl" / "05_create_dim_zipper_tables.sql").read_text(encoding="utf-8")
        self.assertIn("ads_proportion_pct", ddl)
        self.assertIn("listed_at_raw", ddl)
        self.assertIn("listed_at_date", ddl)
        self.assertIn("listed_at_granularity", ddl)
        self.assertIn("9999-12-31", ddl)

    def test_init_database_should_load_dim_energy_vehicle_sql_files(self) -> None:
        """初始化入口必须接入新维度表和触发器 DDL。"""
        init_script = (PROJECT_ROOT / "scripts" / "init_database.py").read_text(encoding="utf-8")
        self.assertIn('run_sql_file("07_create_dim_energy_vehicle.sql")', init_script)
        self.assertIn('run_sql_file("08_create_dim_energy_vehicle_triggers.sql")', init_script)

    def test_init_database_should_load_dim_model_ads_sql_files(self) -> None:
        """初始化入口必须接入 ADS 手工表和同步 SQL。"""
        init_script = (PROJECT_ROOT / "scripts" / "init_database.py").read_text(encoding="utf-8")
        self.assertIn('run_sql_file("05_create_dim_zipper_tables.sql")', init_script)
        self.assertIn('run_sql_file("09_create_dim_model_ads_manual.sql")', init_script)
        self.assertIn('run_sql_file("10_create_dim_model_ads_sync.sql")', init_script)
        self.assertIn('run_sql_file("11_create_dim_model_ads_views.sql")', init_script)
        self.assertIn('run_sql_file("12_alter_dim_model_ads_reference.sql")', init_script)

    def test_bridge_payload_should_use_snapshot_fields_and_9999_end_date(self) -> None:
        """桥接表导入载荷必须写入分析快照字段和有效时间哨兵值。"""
        payload = build_bridge_payload(
            brand_id=11,
            brand_name="比亚迪",
            oem_id=22,
            oem_name="比亚迪汽车",
            company_id=33,
            company_name="比亚迪",
        )
        self.assertEqual(payload["brand_name_snapshot"], "比亚迪")
        self.assertEqual(payload["oem_name_snapshot"], "比亚迪汽车")
        self.assertEqual(payload["company_id"], 33)
        self.assertEqual(payload["company_name_snapshot"], "比亚迪")
        self.assertEqual(payload["effective_end_date"], CURRENT_EFFECTIVE_END_DATE)
        self.assertEqual(payload["is_current"], 1)

    def test_build_oem_brand_lookup_should_build_unique_current_brand_mapping(self) -> None:
        """一个 OEM 只有一个当前主品牌时，应构造出稳定映射。"""
        lookup = build_oem_brand_lookup(
            [
                ("上汽大众", 10, 1, 100, "大众"),
                ("比亚迪汽车", 20, 2, 200, "比亚迪"),
            ],
        )
        self.assertEqual(lookup["上汽大众"]["brand_id"], 100)
        self.assertEqual(lookup["上汽大众"]["brand_name"], "大众")
        self.assertEqual(lookup["比亚迪汽车"]["oem_id"], 20)

    def test_build_oem_brand_lookup_should_reject_duplicate_current_brand(self) -> None:
        """同一 OEM 同时存在多个当前品牌时，必须显式报错而非静默覆盖。"""
        with self.assertRaises(ValueError):
            build_oem_brand_lookup(
                [
                    ("上汽通用", 30, 3, 300, "别克"),
                    ("上汽通用", 30, 3, 301, "雪佛兰"),
                ],
            )

    def test_bridge_brand_oem_schema_should_define_company_snapshots_and_9999_end_date(self) -> None:
        """DDL 必须显式定义桥接表快照字段和有效时间结束哨兵值。"""
        ddl = DDL_PATH.read_text(encoding="utf-8")
        self.assertIn("brand_name_snapshot", ddl)
        self.assertIn("oem_name_snapshot", ddl)
        self.assertIn("company_id", ddl)
        self.assertIn("company_name_snapshot", ddl)
        self.assertIn("9999-12-31", ddl)


if __name__ == "__main__":
    unittest.main()
