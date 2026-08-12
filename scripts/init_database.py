"""一键初始化数仓：DDL → 维度种子 → ODS 入库 → 车型维度构建。

用法:
    python scripts/init_database.py              # 默认初始化到 DIM
    python scripts/init_database.py --skip-ddl   # 跳过建表（表已存在）
    python scripts/init_database.py --skip-ods   # 跳过 ODS 入库
    python scripts/init_database.py --with-dwd   # 显式开启 DWD 与生命周期更新
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from base_infra.logging_config import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def split_sql_statements(sql_content: str) -> list[str]:
    """按当前分隔符切分 SQL，兼容 DELIMITER 语法。"""
    statements: list[str] = []
    delimiter = ";"
    buffer: list[str] = []

    for raw_line in sql_content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        if stripped.upper().startswith("DELIMITER "):
            candidate = "\n".join(buffer).strip()
            if candidate:
                statements.append(candidate)
            buffer = []
            delimiter = stripped.split(maxsplit=1)[1]
            continue

        buffer.append(raw_line)
        candidate = "\n".join(buffer).rstrip()
        if candidate.endswith(delimiter):
            statements.append(candidate[: -len(delimiter)].strip())
            buffer = []

    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return [statement for statement in statements if statement]


def run_step(description: str, module: str) -> None:
    """执行一个 Python 模块步骤。

    Args:
        description: 步骤描述（用于日志）。
        module: 要执行的模块路径（如 ``pipelines.dim.seed_dim_date``）。
    """
    logger.info("===== %s =====", description)
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        logger.error("%s 失败（exit code=%d）", description, result.returncode)
        sys.exit(result.returncode)


def run_sql_file(sql_file: str) -> None:
    """通过 SQLAlchemy 执行 SQL 文件。

    Args:
        sql_file: SQL 文件名（相对于 db/ddl/）。
    """
    from base_infra.db import engine
    from sqlalchemy import text

    sql_path = PROJECT_ROOT / "db" / "ddl" / sql_file
    logger.info("执行 SQL: %s", sql_path.name)
    sql_content = sql_path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in split_sql_statements(sql_content):
            conn.execute(text(statement))


def main() -> None:
    """脚本入口。                                       """
    parser = argparse.ArgumentParser(description="一键初始化数仓")
    parser.add_argument("--skip-ddl", action="store_true", help="跳过 DDL 建表")
    parser.add_argument("--skip-ods", action="store_true", help="跳过 ODS 入库")
    parser.add_argument("--skip-dwd", action="store_true", help="跳过 DWD 清洗（兼容旧参数）")
    parser.add_argument("--with-dwd", action="store_true", help="显式执行 DWD 清洗与生命周期更新")
    args = parser.parse_args()

    # Step 1: DDL 建表（严格按文件编号升序执行；02 为权限脚本，单独手工执行不在此列）
    if not args.skip_ddl:
        ddl_files = [
            "00_create_database.sql",
            "01_create_tables.sql",
            "03_create_dwd_tables.sql",
            "04_create_dws_tables.sql",
            "05_create_dim_zipper_tables.sql",
            "06_alter_dim_model_add_brand_oem_ads_columns.sql",
            "07_create_dim_energy_vehicle.sql",
            "08_create_dim_energy_vehicle_triggers.sql",
            "09_create_dim_model_ads_manual.sql",
            "10_create_dim_model_ads_sync.sql",
            "11_create_dim_model_ads_views.sql",
            "12_alter_dim_model_ads_reference.sql",
            "13_create_ods_stock_finance.sql",
            "14_create_dim_area.sql",
            "15_alter_dwd_sales_monthly_model.sql",
            "16_create_dwd_tables_extend.sql",
            "17_create_ods_index_daily.sql",
            "18_create_ods_futures_daily.sql",
            "19_create_scheduler_tables.sql",
            "20_create_procedures_etl.sql",
        ]
        for ddl_file in ddl_files:
            run_sql_file(ddl_file)
        logger.info("DDL 建表完成")
    else:
        logger.info("跳过 DDL 建表")

    # Step 2: 初始化维度表
    run_step("初始化 dim_date", "pipelines.dim.seed_dim_date")
    run_step("初始化 dim_company/dim_stock", "pipelines.dim.seed_dim_company_stock")
    run_step("初始化 dim_index", "pipelines.dim.seed_dim_index")
    run_step("初始化 dim_commodity", "pipelines.dim.seed_dim_commodity")
    run_step("初始化 dim_brand 种子", "pipelines.dim.seed_dim_oem_brand")
    run_step("从 Excel 导入 dim_oem/bridge", "pipelines.dim.seed_dim_from_excel")

    # Step 3: ODS 入库
    if not args.skip_ods:
        run_step("加载 16888 style → ODS", "pipelines.ingest.sales.load_16888_style_to_ods")
        run_step("加载 16888 city → ODS", "pipelines.ingest.sales.load_16888_city_to_ods")
    else:
        logger.info("跳过 ODS 入库")

    # Step 4: 派生车型维度
    run_step("派生 dim_model", "pipelines.dim.build_sales_dimensions")

    should_run_dwd = args.with_dwd and not args.skip_dwd

    # Step 5: DWD 清洗
    if should_run_dwd:
        run_step("DWD 销量数据清洗", "pipelines.staging.clean_sales_dwd")
    else:
        logger.info("当前默认冻结 DWD，跳过 DWD 清洗")

    # Step 6: 更新维度生命周期（基于 DWD 销量数据）
    if should_run_dwd:
        run_step("更新维度生命周期", "pipelines.dim.update_lifecycle")
    else:
        logger.info("跳过生命周期更新")

    logger.info("===== 初始化完成 =====")
    print("数仓初始化完成。")


if __name__ == "__main__":
    main()
