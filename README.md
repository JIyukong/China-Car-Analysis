# 中国乘用车投资分析平台

> 当前口径：以仓库已落地代码为准，不把未来规划写成当前能力。
> 当前主线：16888 自抓销量文件 -> 云 MySQL 数仓 -> Power BI 消费。
> 当前真实状态：M0/M1 已完成，M2/M3 部分完成，M4-M6 未开始。

## 项目概览

这是一个面向汽车投资分析的数据项目，当前重点不是自研前端，而是先把数据主链跑通：

- 销量数据：来自 `16888` 已落地的 `style` / `city` CSV、JSON 文件
- 数仓链路：`ODS -> DIM -> DWD -> DWS`
- 手工维度支线：
  - `dim_energy_vehicle`
  - `dim_model_ads_manual -> dim_model_ads_zipper`
- 服务层：FastAPI 健康检查 + APScheduler 定时调度
- 可视化目标：后续由 Power BI 读取云 MySQL

当前需要特别注意的断点：

- 调度链目前自动跑到 `DWD`，不会自动执行 `build_sales_dimensions`
- `agg_sales_monthly.py` 已落地，但未接入初始化脚本或调度器
- `ods_stock_daily` 表已建，AKShare 股价采集脚本尚未落地

## 建议阅读顺序

1. 本文 `README.md`：先看怎么初始化、怎么增量跑、哪里是断点
2. `执行大纲.md`：再看当前做到哪一里程碑、后面缺什么
3. `需求文档.md`：最后看产品定位、技术边界和后续路线
4. `docs/warehouse-spec.md`：补充数仓分层与数据流转口径
5. `docs/data-dictionary.md`：查字段定义

## 目录结构

```text
CarApp/
├── base_infra/                    # 配置、数据库连接、日志
├── backend/                       # FastAPI 健康检查入口
├── pipelines/
│   ├── ingest/                    # 数据采集 / ODS 入库
│   ├── dim/                       # 维度种子、维度派生、手工维度刷新
│   ├── staging/                   # DWD 清洗
│   └── serve/                     # DWS 聚合
├── scheduler/                     # APScheduler 定时任务
├── scripts/                       # 初始化脚本
├── db/
│   ├── ddl/                       # 建库建表 / 触发器 / 存储过程
│   └── manual/                    # 手工维度刷新 SQL
├── data/manual_dimensions/        # 手工维护的 CSV 维度源
├── docs/                          # 辅助文档
├── tests/                         # 测试
├── requirements.txt               # Python 依赖
├── pyproject.toml                 # 项目元数据
├── .env.example                   # 环境变量模板
├── 执行大纲.md
├── 需求文档.md
└── README.md
```

## 数仓分层与命名

| 前缀 | 类型 | 说明 |
|------|------|------|
| `dim_` | 维度层 | 上市公司、主机厂、品牌、车型、日期、股票等主数据 |
| `bridge_` | 桥接层 | 多对多关系，如品牌-主机厂、公司-股票 |
| `ods_` | 贴源层 | 保留原始采集字段与溯源信息 |
| `dwd_` | 明细层 | 标准化、去重、补充维度 ID |
| `dws_` | 汇总层 | 按月份按主体聚合 |
| `ads_` | 应用层 | 面向下游消费的应用表或视图 |
| `tmp_` | 临时层 | 中间计算结果 |

当前真实落地情况：

- 已落地主表：`dim_*`、`bridge_*`、`ods_*`、`dwd_sales_monthly_model`
- 已落地 DWS 表：`dws_sales_monthly_company`、`dws_sales_monthly_brand`
- 已落地手工 ADS 支线表：`dim_model_ads_manual`、`dim_model_ads_zipper`
- 未落地自动股价采集：仅 `ods_stock_daily` 表已存在

## 系统主链

```text
16888 style/city 文件
    -> ODS 入库
    -> DIM 种子与 dim_model 派生
    -> DWD 清洗
    -> DWS 聚合
    -> Power BI 消费
```

对应代码入口：

- ODS 入库：
  - `pipelines/ingest/sales/load_16888_style_to_ods.py`
  - `pipelines/ingest/sales/load_16888_city_to_ods.py`
- 维度派生：`pipelines/dim/build_sales_dimensions.py`
- DWD 清洗：`pipelines/staging/clean_sales_dwd.py`
- DWS 聚合：`pipelines/serve/agg_sales_monthly.py`

## DDL 使用顺序

### 1. 新库初始化推荐顺序

对于全新的云 MySQL 实例，建议先手工执行 SQL，再执行 Python 初始化脚本。

```text
00_create_database.sql
    -> 01_create_tables.sql
    -> 05_create_dim_zipper_tables.sql
    -> 07_create_dim_energy_vehicle.sql
    -> 08_create_dim_energy_vehicle_triggers.sql
    -> 09_create_dim_model_ads_manual.sql
    -> 10_create_dim_model_ads_sync.sql
    -> 11_create_dim_model_ads_views.sql
    -> 12_alter_dim_model_ads_reference.sql
    -> 03_create_dwd_tables.sql
    -> 04_create_dws_tables.sql           # 如需运行 DWS 聚合，建议一并执行
    -> 02_create_powerbi_user.sql         # 仅在需要 Power BI 只读账号时执行
```

说明：

- `12_alter_dim_model_ads_reference.sql` 依赖 `05_create_dim_zipper_tables.sql`
- `04_create_dws_tables.sql` 当前未被初始化脚本自动执行，但 `agg_sales_monthly.py` 依赖它
- `02_create_powerbi_user.sql` 不影响主链入库，只影响下游 BI 只读访问
- `06_alter_dim_model_add_brand_oem_ads_columns.sql` 属于存量兼容补丁，不是新库初始化主链必跑项

### 2. 推荐执行命令

```bash
mysql -h <host> -P 3306 -u root -p < db/ddl/00_create_database.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/01_create_tables.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/05_create_dim_zipper_tables.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/07_create_dim_energy_vehicle.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/08_create_dim_energy_vehicle_triggers.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/09_create_dim_model_ads_manual.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/10_create_dim_model_ads_sync.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/11_create_dim_model_ads_views.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/12_alter_dim_model_ads_reference.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/03_create_dwd_tables.sql
mysql -h <host> -P 3306 -u root -p < db/ddl/04_create_dws_tables.sql

# 替换占位符后执行
mysql -h <host> -P 3306 -u root -p < db/ddl/02_create_powerbi_user.sql
```

## 首次初始化使用顺序

### 1. 准备环境

- Python `3.10+`
- 云 MySQL `8.0+`
- 已将当前本机 IP 加入数据库白名单
- 已准备写账号和 root 或高权限账号

### 2. 安装依赖

```bash
pip install -r requirements.txt
pip install -e .
```

### 3. 配置环境变量

```bash
copy .env.example .env
```

填写以下核心配置：

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `POWERBI_READONLY_USER`
- `POWERBI_READONLY_PASSWORD`

说明：数据库连接串由 `base_infra/config.py` 内部拼装，无需手写 `DATABASE_URL`。

### 4. 执行 Python 初始化主链

对于已完成建库建表的环境，推荐执行：

```bash
python scripts/init_database.py --skip-ddl --with-dwd
```

该脚本当前真实顺序如下：

```text
scripts/init_database.py
    -> seed_dim_date
    -> seed_dim_company_stock
    -> seed_dim_oem_brand
    -> seed_dim_from_excel
    -> load_16888_style_to_ods
    -> load_16888_city_to_ods
    -> build_sales_dimensions
    -> clean_sales_dwd              # 仅 --with-dwd
    -> update_lifecycle             # 仅 --with-dwd
```

### 5. 启动后端健康检查

```bash
uvicorn backend.app.main:app --reload
```

验证地址：

- `http://localhost:8000/`
- `http://localhost:8000/health`

期望健康检查返回：

```json
{"status": "ok", "database": "connected"}
```

## 月度调度运行顺序

当前自动调度链路由 `scheduler/runner.py` 注册，真实顺序如下：

```text
每月 2 号 03:00
    -> crawl_16888_monthly
       -> crawl_16888_style/crawl_all_with_retry.py
       -> crawl_16888_city/crawl_2025_full_year.py

每月 2 号 05:00
    -> load_ods_after_crawl
       -> load_16888_style_to_ods.main()
       -> load_16888_city_to_ods.main()

每月 2 号 06:00
    -> run_dwd_clean
```

启动方式：

```bash
python -m scheduler.runner
```

当前未自动接线的步骤：

- `pipelines/dim/build_sales_dimensions.py`
- `pipelines/serve/agg_sales_monthly.py`
- 任何股价采集任务

这意味着：

- 新抓取的 ODS 数据不会自动刷新 `dim_model`
- DWS 聚合不会自动更新
- 股价数据仍需后续实现

## 手工维度刷新顺序

### 1. 能源类型 / 车型类型支线

数据源：

- `data/manual_dimensions/dim_energy_vehicle.csv`

执行入口：

```bash
python -m pipelines.dim.refresh_dim_energy_vehicle
```

流程：

```text
dim_energy_vehicle.csv
    -> stg_dim_energy_vehicle
    -> db/manual/refresh_dim_energy_vehicle.sql
    -> dim_energy_vehicle
    -> 触发器同步 dim_model.energy_type / vehicle_type
```

### 2. ADS 手工权威存储支线

数据源：

- `data/manual_dimensions/dim_model_ads_matched.csv`

执行入口：

```bash
python -m pipelines.dim.refresh_dim_model_ads
```

流程：

```text
dim_model_ads_matched.csv
    -> stg_dim_model_ads_manual
    -> db/manual/refresh_dim_model_ads_manual.sql
    -> dim_model_ads_manual
    -> sp_refresh_dim_model_ads_all()
    -> dim_model_ads_zipper
    -> 回填 dim_model.ads_id / ads_supplier_name
```

注意：

- `dim_model_ads_manual` 是权威存储表，可包含尚未在 `dim_model` 中出现的车型
- `dim_model_ads_zipper` 只接收当前已存在于 `dim_model` 的车型

## 当前进度与断点

| 模块 | 当前状态 | 说明 |
|------|----------|------|
| M0 脚手架与数据库 | 已完成 | 基础包、主 DDL、健康检查已落地 |
| M1 销量数据主链 | 已完成 | 16888 ODS、DIM 派生、DWD、部分 DWS 已实现 |
| M2 股价采集 | 部分完成 | 表与维度映射已建，采集脚本缺失 |
| M3 调度服务 | 部分完成 | 调度器框架已接销量主链，但未覆盖全链路 |
| M4 Power BI 看板 | 未开始 | 仅只读账号 DDL 已有 |
| M5 Power BI 关联分析 | 未开始 | 未见报表文件 |
| M6 Publish to Web | 未开始 | 未见发布资产 |

## 常用命令

```bash
# 首次初始化（DDL 已手工执行完成）
python scripts/init_database.py --skip-ddl --with-dwd

# 仅启动健康检查服务
uvicorn backend.app.main:app --reload

# 启动调度器
python -m scheduler.runner

# 手工刷新能源 / 车型类型字典
python -m pipelines.dim.refresh_dim_energy_vehicle

# 手工刷新 ADS 支线
python -m pipelines.dim.refresh_dim_model_ads

# 手工执行 DWS 聚合
python -m pipelines.serve.agg_sales_monthly
```

## 只读账号验证

使用 Power BI 只读账号连接数据库后，以下行为应符合预期：

- `SELECT * FROM dw_auto_stock.dim_company LIMIT 1;` 成功
- `INSERT INTO dw_auto_stock.dim_company(...) VALUES(...);` 被拒绝

## 参考文档

- `执行大纲.md`
- `需求文档.md`
- `docs/warehouse-spec.md`
- `docs/data-dictionary.md`
