"""汽车之家销量数据 JSON → MySQL 入库子脚本。

读取 ``crawl_sales.py`` 产出的 JSON 文件，将销量数据写入 ``cardb.sales_monthly_model`` 表。

字段映射策略（重要）:
    - 现有 ``sales_monthly_model`` 表无 ``image_url`` 与能源分项明细字段，
      故:
      * ``image_url`` 仅保留在 JSON/CSV，不入库
      * 能源分项展开为多行写入：每行对应一种能源类型 + 对应销量
      * 若车型无能源分项（即 ``energy_breakdown`` 为空），则作为单行写入，
        ``energy_type`` 留 NULL，``sales_volume`` 取车系总销量
    - 这样不改动现有表结构，与 AKShare 来源数据（``source='akshare-*'``）共存。

UPSERT 语义:
    使用 ``INSERT ... ON DUPLICATE KEY UPDATE``，唯一键 ``(model_id, month, source)``，
    其中 ``source='autohome'``。

车型名匹配:
    按 ``model.canonical_name`` 精确匹配；匹配不到的记录打印 warning 并跳过入库
    （不入库但仍保留在 JSON 文件中，不影响后续重新匹配）。

用法:
    # 入库最新一次抓取的 JSON（自动找 data/ 下最新的）
    python scripts/crawl_autohome/import_to_db.py

    # 指定 JSON 文件路径
    python scripts/crawl_autohome/import_to_db.py --file path/to/x.json

依赖:
    - SQLAlchemy >= 2.0
    - PyMySQL（SQLAlchemy 的 MySQL 驱动）
    - python-dotenv（读取 .env 中的 DATABASE_URL）

说明:
    - 本脚本可独立运行，不依赖 backend 包
    - 遵循 seed_companies.py 的代码风格（_load_env / _build_engine 模式）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------

# 数据来源标记：与 AKShare 来源数据区分                                      # 共存于同一张表
SOURCE_TAG = "autohome"

# 能源类型为空时的占位（写入 NULL）                                         # 与表定义一致
NULL_ENERGY = None


# ----------------------------------------------------------------------------
# 环境与引擎
# ----------------------------------------------------------------------------

def _load_env() -> str:
    """从项目根目录的 .env 加载配置并返回 DATABASE_URL。

    :returns: DATABASE_URL 连接串
    :raises SystemExit: 若未配置 DATABASE_URL，打印提示后退出
    """
    project_root = Path(__file__).resolve().parents[2]               # scripts/crawl_autohome/ 的上两级
    env_path = project_root / ".env"
    if env_path.exists():                                            # 存在则加载
        load_dotenv(env_path)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:                                             # 缺失则报错退出
        print("[ERROR] 未配置 DATABASE_URL，请在项目根目录 .env 中设置后重试。")
        print("         可参考 .env.example 模板。")
        raise SystemExit(1)
    return database_url


def _build_engine(database_url: str) -> Engine:
    """根据 DATABASE_URL 构建 SQLAlchemy 引擎。

    :param database_url: 形如 mysql+pymysql://user:pass@host:3306/cardb
    :returns: SQLAlchemy Engine 实例
    """
    engine = create_engine(
        database_url,
        pool_pre_ping=True,                                         # 防止连接断开
        future=True,                                                # 启用 SQLAlchemy 2.0 风格
    )
    return engine


# ----------------------------------------------------------------------------
# 文件定位
# ----------------------------------------------------------------------------

def _data_dir() -> Path:
    """返回 data 目录路径。"""
    return Path(__file__).resolve().parent / "data"


def find_latest_json() -> Path:
    """在 data 目录下查找最新的 JSON 文件。

    :returns: 最新的 JSON 文件路径
    :raises SystemExit: 无 JSON 文件时退出
    """
    data_dir = _data_dir()
    json_files = sorted(
        data_dir.glob("autohome_sales_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,                                               # 最新的在前
    )
    if not json_files:
        print(f"[ERROR] data 目录下未找到任何 autohome_sales_*.json 文件。")
        print(f"         请先运行 crawl_sales.py 抓取数据。")
        raise SystemExit(1)
    return json_files[0]


# ----------------------------------------------------------------------------
# 车型名 → model_id 映射
# ----------------------------------------------------------------------------

def load_model_id_map(engine: Engine) -> dict[str, int]:
    """从 model 主表加载 canonical_name → id 的映射字典。

    :param engine: SQLAlchemy 引擎
    :returns: {canonical_name: id} 字典
    """
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, canonical_name FROM model")).fetchall()
    name_to_id = {row[1]: row[0] for row in rows}
    print(f"[OK] model 主表共 {len(name_to_id)} 条车型记录。")
    return name_to_id


# ----------------------------------------------------------------------------
# 能源类型归类辅助
# ----------------------------------------------------------------------------

def _energy_type_to_segment_key(energy_type: str) -> str:
    """将能源类型归一到 model.energy_type 使用的简短标识。

    汽车之家返回的能源类型有: 纯电动 / 插电混合 / 增程式 / 油电混合 / 汽油 / 柴油 / 天然气
    model.energy_type 字段允许自由字符串，这里直接透传，不做强制映射。

    :param energy_type: 能源类型原始文本
    :returns: 归一后的能源类型字符串
    """
    return energy_type                                              # 透传，保持原始语义


# ----------------------------------------------------------------------------
# 入库主逻辑
# ----------------------------------------------------------------------------

def import_to_db(json_path: Path, engine: Engine) -> None:
    """将 JSON 文件中的销量数据导入 sales_monthly_model 表。

    :param json_path: JSON 文件路径
    :param engine:    SQLAlchemy 引擎
    """
    # 读取 JSON
    payload: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    months_data: list[dict[str, Any]] = payload.get("months", [])
    if not months_data:
        print("[WARN] JSON 文件中无 months 数据，跳过入库。")
        return

    # 加载车型名映射
    model_id_map = load_model_id_map(engine)

    # SQL: UPSERT（INSERT ... ON DUPLICATE KEY UPDATE）
    upsert_sql = text("""
        INSERT INTO sales_monthly_model
            (model_id, model_name, company_id, energy_type, month,
             sales_volume, source)
        VALUES
            (:model_id, :model_name, :company_id, :energy_type, :month,
             :sales_volume, :source)
        ON DUPLICATE KEY UPDATE
            model_name   = VALUES(model_name),
            energy_type  = VALUES(energy_type),
            sales_volume = VALUES(sales_volume)
    """)

    total_inserted = 0                                             # 实际写入/更新的行数
    total_skipped = 0                                              # 因车型名未匹配而跳过的记录数
    total_rows_expanded = 0                                        # 展开后的总行数（含能源分项）

    print(f"[导入] 开始处理 {len(months_data)} 个月份数据 ...")

    for month_data in months_data:
        month = month_data["month"]                                # YYYY-MM 格式
        records = month_data["records"]

        params_batch: list[dict[str, Any]] = []                    # 本月待写入的参数批次

        for rec in records:
            model_name = rec["model_name"]
            model_id = model_id_map.get(model_name)
            if model_id is None:                                   # 车型名未匹配
                total_skipped += 1
                continue

            energy_breakdown: dict[str, int] = rec.get("energy_breakdown", {}) or {}
            sales_volume = rec.get("sales_volume")

            if energy_breakdown:
                # 有能源分项: 展开为多行，每行一种能源类型 + 对应销量
                for energy_type, energy_sales in energy_breakdown.items():
                    params_batch.append({
                        "model_id": model_id,
                        "model_name": model_name,
                        # company_id 为 NOT NULL，但本表无厂家信息，取该 model 的 company_id
                        # 此处用一个子查询占位；为简化，先查 model 表取 company_id
                        "company_id": _get_company_id_cached(engine, model_id),
                        "energy_type": _energy_type_to_segment_key(energy_type),
                        "month": month,
                        "sales_volume": energy_sales,
                        "source": SOURCE_TAG,
                    })
                    total_rows_expanded += 1
            else:
                # 无能源分项: 作为单行写入，energy_type 留 NULL
                if sales_volume is None:
                    total_skipped += 1                              # 销量也缺失则跳过
                    continue
                params_batch.append({
                    "model_id": model_id,
                    "model_name": model_name,
                    "company_id": _get_company_id_cached(engine, model_id),
                    "energy_type": NULL_ENERGY,
                    "month": month,
                    "sales_volume": sales_volume,
                    "source": SOURCE_TAG,
                })
                total_rows_expanded += 1

        # 批量写入本月数据（单事务）
        if params_batch:
            with engine.begin() as conn:
                conn.execute(upsert_sql, params_batch)
            total_inserted += len(params_batch)
            print(f"  {month}: 写入 {len(params_batch)} 行（展开自 {len(records)} 条记录）。")
        else:
            print(f"  {month}: 无可写入数据（全部车型名未匹配或销量缺失）。")

    # 汇总
    print("\n" + "=" * 70)
    print("[完成] 入库汇总")
    print(f"  实际写入/更新行数: {total_inserted}")
    print(f"  展开后总行数:      {total_rows_expanded}")
    print(f"  跳过记录数:        {total_skipped}（车型名未匹配或销量缺失）")
    print("=" * 70)


# ----------------------------------------------------------------------------
# model_id → company_id 缓存（避免每行都查一次 DB）
# ----------------------------------------------------------------------------

_company_id_cache: dict[int, int] = {}


def _get_company_id_cached(engine: Engine, model_id: int) -> int:
    """根据 model_id 查询其所属 company_id，带内存缓存。

    :param engine:  SQLAlchemy 引擎
    :param model_id: 车型 ID
    :returns: 车企 ID
    :raises RuntimeError: model_id 在 model 表中不存在时
    """
    if model_id in _company_id_cache:
        return _company_id_cache[model_id]

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT company_id FROM model WHERE id = :mid"),
            {"mid": model_id},
        ).fetchone()
    if row is None:
        raise RuntimeError(f"model_id={model_id} 在 model 表中不存在，数据不一致。")
    _company_id_cache[model_id] = row[0]
    return row[0]


# ----------------------------------------------------------------------------
# 命令行入口
# ----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="汽车之家销量数据 JSON → MySQL 入库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # 入库最新一次抓取的 JSON（自动找 data/ 下最新的）
  python scripts/crawl_autohome/import_to_db.py

  # 指定 JSON 文件路径
  python scripts/crawl_autohome/import_to_db.py --file data/autohome_sales_20260630_150000.json
""",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="JSON 文件路径（不指定则自动取 data/ 下最新的）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()

        # 定位 JSON 文件
        if args.file:
            json_path: Path = args.file
            if not json_path.exists():
                print(f"[ERROR] 指定的 JSON 文件不存在: {json_path}")
                raise SystemExit(1)
        else:
            json_path = find_latest_json()
        print(f"[1/3] 待入库 JSON: {json_path}")

        # 加载配置与引擎
        database_url = _load_env()
        engine = _build_engine(database_url)
        print("[2/3] 数据库连接成功。")

        # 执行入库
        print("[3/3] 开始入库 ...")
        import_to_db(json_path, engine)

    except SystemExit:
        raise
    except Exception as exc:                                        # 兜底捕获
        print(f"[FATAL] 入库失败: {exc}")
        sys.exit(1)
