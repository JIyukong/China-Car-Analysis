# CarApp 项目 Code Wiki

> 本 Wiki 以仓库当前真实代码为准，描述项目架构、模块职责、关键类/函数、依赖关系及运行方式。
> 生成日期：2026-08-12
> 项目定位：中国乘用车投资分析平台 —— 以销量因子为主链、股价/指数/期货因子为补充的数据分析底座。

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构](#2-整体架构)
3. [目录结构与模块职责](#3-目录结构与模块职责)
4. [关键类与函数说明](#4-关键类与函数说明)
5. [数据仓库分层与表说明](#5-数据仓库分层与表说明)
6. [核心数据链路](#6-核心数据链路)
7. [调度任务体系](#7-调度任务体系)
8. [依赖关系](#8-依赖关系)
9. [配置说明](#9-配置说明)
10. [项目运行方式](#10-项目运行方式)
11. [测试与运维](#11-测试与运维)
12. [附录：常用入口速查](#12-附录常用入口速查)

---

## 1. 项目概览

### 1.1 项目目标

构建面向汽车行业投资分析的数据底座，将销量、股价、行业指数、商品期货等因子整理为可持续更新、可被 BI 消费的规范化数仓。

### 1.2 当前真实范围

| 状态 | 内容 |
|------|------|
| 已落地 | 云 MySQL 数仓 DDL、16888 销量主链（ODS → DIM → DWD）、部分 DWS、FastAPI 健康检查、APScheduler 调度框架、手工维度支线 |
| 已落地 | AKShare A 股 / H 股日线、A 股财务、同花顺 881125 行业指数、商品期货 LC0/CU0/AL0 主力连续采集 |
| 部分落地 | 调度器尚未完全接入维度派生、DWS 聚合、股票/城市 DWD 清洗 |
| 未落地 | Power BI 报表资产、Publish to Web、自研前端 |

### 1.3 技术栈

| 层级 | 技术 |
|------|------|
| 数据服务层 | FastAPI、SQLAlchemy 2.0、PyMySQL、APScheduler |
| 数据采集 | Playwright（16888 / 汽车之家 / 华为 ADS）、AKShare（股价 / 指数 / 期货） |
| 数据存储 | 云 MySQL 8.0+ |
| 配置管理 | pydantic-settings、python-dotenv |
| 下游消费 | Power BI（规划中） |

---

## 2. 整体架构

### 2.1 架构分层

```text
数据源层
├── 16888 style/city 文件（已抓取 CSV/JSON）
├── AKShare 股价 / 指数 / 期货接口
└── 手工 CSV 维度文件

数据采集 / ODS 贴源层  ->  pipelines/ingest/
维度层 / 桥接层        ->  pipelines/dim/
明细层（DWD）          ->  pipelines/staging/
汇总层（DWS）          ->  pipelines/serve/
应用 / 消费层          ->  Power BI（未落地）

服务层                ->  backend/  (FastAPI 健康检查)
调度层                ->  scheduler/ (APScheduler 任务编排)
基础设施              ->  base_infra/ (配置 / 数据库 / 日志)
```

### 2.2 数据主链

```text
16888 style/city 文件
    -> ODS 入库 (pipelines/ingest/sales/)
    -> DIM 派生 (pipelines/dim/build_sales_dimensions.py)
    -> DWD 清洗 (pipelines/staging/clean_sales_dwd.py)
    -> DWS 聚合 (pipelines/serve/agg_sales_monthly.py)
    -> Power BI 消费

AKShare 股价接口
    -> A 股 / H 股日线接入 ods_stock_daily
    -> A 股财务接入 ods_stock_finance
    -> DWD 清洗 (clean_stock_daily_dwd / clean_stock_finance_dwd)
    -> 暂不抓取 US 数据

AKShare 同花顺行业指数
    -> 881125 汽车整车日线接入 ods_index_daily

AKShare 新浪期货
    -> LC0 / CU0 / AL0 主力连续日线接入 ods_futures_daily
```

---

## 3. 目录结构与模块职责

### 3.1 顶层目录

| 目录 | 职责 |
|------|------|
| [base_infra/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/base_infra) | 基础设施：配置管理、数据库引擎、日志 |
| [backend/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/backend) | FastAPI 服务入口，当前仅提供健康检查 |
| [pipelines/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines) | ETL 主链：数据采集、维度构建、DWD 清洗、DWS 聚合 |
| [scheduler/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scheduler) | APScheduler 调度器、任务编排、告警 |
| [scripts/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts) | 初始化与运维脚本 |
| [db/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db) | 数仓 DDL、存储过程、触发器、迁移脚本 |
| [data/manual_dimensions/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/data/manual_dimensions) | 手工维护的 CSV 维度源 |
| [tests/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests) | 单元测试与集成测试 |
| [docs/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/docs) | 项目文档（含本 Wiki） |
| [deploy/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/deploy) | 部署脚本（Windows NSSM 服务注册） |

### 3.2 pipelines 子目录

| 子目录 | 职责 |
|--------|------|
| [pipelines/common/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/common) | 公共工具：upsert、表存在检查、空值率 / 唯一性 / 行数校验 |
| [pipelines/ingest/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest) | 数据采集与 ODS 加载：销量爬虫、股价/指数/期货采集 |
| [pipelines/dim/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim) | 维度种子、维度派生、手工维度刷新、生命周期更新 |
| [pipelines/staging/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/staging) | DWD 明细层清洗 |
| [pipelines/serve/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/serve) | DWS 聚合与下游服务 |

### 3.3 db 子目录

| 子目录 | 职责 |
|--------|------|
| [db/system/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/system) | 建库、Power BI 只读账号 |
| [db/ods/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/ods) | ODS 贴源层建表 |
| [db/dim/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/dim) | 维度层与桥接层建表 |
| [db/dwd/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/dwd) | DWD 明细层建表 |
| [db/dws/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/dws) | DWS 汇总层建表 |
| [db/ads/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/ads) | 应用层视图 |
| [db/scheduler/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/scheduler) | 调度器元数据表 |
| [db/migrations/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/migrations) | 结构补丁 |
| [db/triggers/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/triggers) | 触发器 |
| [db/procedures/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/procedures) | ETL 存储过程 |
| [db/events/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/events) | 定时事件 |
| [db/manual/](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/db/manual) | 手工维度刷新 SQL |

---

## 4. 关键类与函数说明

### 4.1 base_infra 基础设施

#### [base_infra/config.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/base_infra/config.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `Settings` | class | 基于 pydantic-settings 的全局配置类，从 `.env` 或环境变量读取 |
| `Settings.database_url` | property | 将 DB_HOST/PORT/USER/PASSWORD/NAME 拼接为 `mysql+pymysql` 连接串 |
| `get_settings()` | function | 使用 `@lru_cache` 返回配置单例 |

#### [base_infra/db.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/base_infra/db.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `engine` | object | 全局 SQLAlchemy 引擎，启用 `pool_pre_ping=True`、`pool_recycle=3600` |
| `SessionLocal` | object | 会话工厂，`autoflush=False`、`autocommit=False` |
| `check_database_connection()` | function | 执行 `SELECT 1` 探针，供健康检查接口调用 |

#### [base_infra/logging_config.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/base_infra/logging_config.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `ColoredFormatter` | class | 彩色日志格式化器 |
| `setup_logging()` | function | 初始化根日志（控制台 + 按大小轮转文件） |
| `get_logger(name)` | function | 获取已配置 logger |
| `setup_uvicorn_logging()` | function | 返回 uvicorn 日志配置字典 |

### 4.2 backend 服务层

#### [backend/app/main.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/backend/app/main.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `app` | FastAPI | FastAPI 应用实例 |
| `root()` | route | `GET /`，返回欢迎信息 |
| `health()` | route | `GET /health`，探测数据库连接，正常 200 / 异常 503 |

### 4.3 scheduler 调度层

#### [scheduler/task_config.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scheduler/task_config.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `TASK_CONFIG` | dict | 任务注册字典，定义 cron、超时、重试、下游依赖、交易日历、业务键 |

#### [scheduler/runtime.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scheduler/runtime.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `TaskResult` | class | 任务结果封装 |
| `SkipTask` / `_WorkerError` | exception | 任务跳过 / 工作进程异常 |
| `SchedulerHolder` | class | 持有全局 scheduler 实例 |
| `_worker_entry()` / `_run_in_subprocess()` | function | 子进程执行入口与超时 / 进程树强杀 |
| `_insert_run_log()` / `_update_status()` | function | 写入 `scheduler_run_log` 与状态更新 |
| `is_trade_day()` / `build_business_key()` | function | 交易日判断与业务键生成 |
| `task(task_id, cfg)` | decorator | 核心装饰器，封装超时、重试、下游触发、日志、告警 |
| `run_recovery(scheduler)` | function | 启动前恢复：清理僵尸记录、补缺口 |
| `start_heartbeat_thread()` | function | 心跳线程 |

#### [scheduler/runner.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scheduler/runner.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `_resolve_callable(dotted)` | function | 按 `module:function` 动态导入业务入口 |
| `_on_scheduler_event(event)` | function | 监听 `JOB_ERROR / JOB_MISSED / JOB_MAX_INSTANCES` 并告警 |
| `create_scheduler()` | function | 创建 `BlockingScheduler`，按 `TASK_CONFIG` 注册任务 |
| `main()` | function | 调度器启动入口 |

#### [scheduler/alert.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scheduler/alert.py)

| 名称 | 类型 | 说明 |
|------|------|------|
| `send_alert(...)` | function | 发送飞书 / 企业微信告警，10 分钟去重 |

### 4.4 pipelines 公共工具

#### [pipelines/common/sql_utils.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/common/sql_utils.py)

| 名称 | 说明 |
|------|------|
| `upsert_batch(...)` | 批量 UPSERT（`ON DUPLICATE KEY UPDATE`） |
| `table_exists(...)` | 判断表是否存在 |
| `get_row_count(...)` | 获取表行数 |
| `safe_execute(...)` | 安全执行 SQL 并记录日志 |

#### [pipelines/common/quality.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/common/quality.py)

| 名称 | 说明 |
|------|------|
| `check_row_count(...)` | 行数达标检查 |
| `check_null_rate(...)` | 空值率检查 |
| `check_unique(...)` | 唯一性检查 |

### 4.5 维度层关键入口

| 文件 | 关键函数 | 说明 |
|------|----------|------|
| [pipelines/dim/seed_dim_date.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/seed_dim_date.py) | `main()` | 生成并写入 `dim_date` |
| [pipelines/dim/seed_dim_company_stock.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/seed_dim_company_stock.py) | `upsert_dim_company()`、`upsert_dim_stock()`、`upsert_bridge()`、`main()` | 初始化公司、股票维度及桥接表 |
| [pipelines/dim/seed_dim_oem_brand.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/seed_dim_oem_brand.py) | `upsert_dim_brand()`、`main()` | 初始化品牌维度 |
| [pipelines/dim/seed_dim_from_excel.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/seed_dim_from_excel.py) | `main()` | 从 Excel 映射 OEM / 品牌 / 公司桥接 |
| [pipelines/dim/build_sales_dimensions.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/build_sales_dimensions.py) | `run_build_sales_dimensions()`、`main()` | 从销量 ODS 派生 `dim_model` |
| [pipelines/dim/update_lifecycle.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/update_lifecycle.py) | `run_update_lifecycle()`、`main()` | 更新 `dim_oem` / `dim_model` 活跃生命周期 |
| [pipelines/dim/update_vehicle_type.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/update_vehicle_type.py) | `update_vehicle_type()`、`main()` | 更新车型大类 / 细分映射 |
| [pipelines/dim/refresh_dim_energy_vehicle.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/refresh_dim_energy_vehicle.py) | `refresh_dim_energy_vehicle()`、`main()` | 从 CSV 刷新能源车型维度 |
| [pipelines/dim/refresh_dim_model_ads.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/refresh_dim_model_ads.py) | `refresh_dim_model_ads()`、`main()` | 刷新 ADS 车型广告占比拉链 |
| [pipelines/dim/refresh_dim_area.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/dim/refresh_dim_area.py) | `refresh_dim_area()`、`main()` | 刷新 `dim_area` 地区维度 |

### 4.6 ODS 采集关键入口

| 文件 | 关键函数 | 说明 |
|------|----------|------|
| [pipelines/ingest/sales/load_16888_style_to_ods.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/sales/load_16888_style_to_ods.py) | `discover_files()`、`read_rows()`、`build_row_hash()`、`upsert_rows()`、`main()` | 车型 CSV → `ods_sales_monthly_model` |
| [pipelines/ingest/sales/load_16888_city_to_ods.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/sales/load_16888_city_to_ods.py) | 同上 | 城市 CSV → `ods_sales_monthly_model_city` |
| [pipelines/ingest/sales/crawl_16888_style/crawl_style.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/sales/crawl_16888_style/crawl_style.py) | `crawl_one_month()`、`crawl()` | Playwright 车型销量爬虫 |
| [pipelines/ingest/sales/crawl_16888_city/crawl_city.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/sales/crawl_16888_city/crawl_city.py) | `crawl_one_month()`、`crawl()` | Playwright 城市上牌爬虫 |
| [pipelines/ingest/stock/load_stock_daily_akshare.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/stock/load_stock_daily_akshare.py) | `run_daily_ingest()`、`main()` | A 股 / H 股日线采集 |
| [pipelines/ingest/stock/load_stock_finance_akshare.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/stock/load_stock_finance_akshare.py) | `run_finance_ingest()`、`main()` | A 股财务数据采集 |
| [pipelines/ingest/index/load_ths_index_daily_akshare.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/index/load_ths_index_daily_akshare.py) | `run_index_ingest()`、`main()` | 同花顺 881125 指数日线 |
| [pipelines/ingest/futures/load_futures_main_sina.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/ingest/futures/load_futures_main_sina.py) | `run_futures_ingest()`、`main()` | LC0 / CU0 / AL0 期货主力连续日线 |

### 4.7 DWD / DWS 关键入口

| 文件 | 关键函数 | 说明 |
|------|----------|------|
| [pipelines/staging/clean_sales_dwd.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/staging/clean_sales_dwd.py) | `run_clean_sales_dwd()`、`main()` | `ods_sales_monthly_model` → `dwd_sales_monthly_model` |
| [pipelines/staging/clean_sales_city_dwd.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/staging/clean_sales_city_dwd.py) | `run_clean_sales_city_dwd()`、`main()` | 城市销量 ODS → DWD |
| [pipelines/staging/clean_stock_daily_dwd.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/staging/clean_stock_daily_dwd.py) | `run_clean_stock_daily_dwd()`、`main()` | 股票日线 ODS → DWD |
| [pipelines/staging/clean_stock_finance_dwd.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/staging/clean_stock_finance_dwd.py) | `run_clean_stock_finance_dwd()`、`main()` | 股票财务 ODS → DWD |
| [pipelines/serve/agg_sales_monthly.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pipelines/serve/agg_sales_monthly.py) | `run_agg_sales_monthly()`、`main()` | 公司 / 品牌粒度月度聚合 |

### 4.8 初始化与运维脚本

| 文件 | 关键函数 | 说明 |
|------|----------|------|
| [scripts/init_database.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/init_database.py) | `discover_sql_files()`、`run_sql_file()`、`run_step()`、`main()` | 一键初始化数仓 |
| [scripts/run_sql_file.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/run_sql_file.py) | `main()` | 单 SQL 文件执行 |
| [scripts/data_quality_check.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/data_quality_check.py) | — | 数据质量检查 |
| [scripts/sync_trade_calendar.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/sync_trade_calendar.py) | — | 同步交易日历 |
| [scripts/check_scheduler_heartbeat.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/check_scheduler_heartbeat.py) | — | 检查调度器心跳 |

---

## 5. 数据仓库分层与表说明

### 5.1 分层前缀规范

| 前缀 | 类型 | 说明 |
|------|------|------|
| `dim_` | 维度层 | 上市公司、主机厂、品牌、车型、日期、股票等主数据 |
| `bridge_` | 桥接层 | 多对多关系，如品牌-主机厂、公司-股票 |
| `ods_` | 贴源层 | 保留原始采集字段与溯源信息 |
| `dwd_` | 明细层 | 标准化、去重、补充维度 ID |
| `dws_` | 汇总层 | 按月份按主体聚合 |
| `ads_` | 应用层 | 面向下游消费的应用表或视图 |

### 5.2 核心维度表

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| `dim_company` | 上市公司主体 | `company_code`、`company_name`、`full_name`、`company_type`、`is_listed` |
| `dim_oem` | 销量主机厂主体 | `oem_code`、`oem_name`、`company_id`、`oem_type`、`first_seen_month`、`last_seen_month`、`is_active` |
| `dim_brand` | 品牌主体 | `brand_code`、`brand_name`、`is_joint_venture_brand` |
| `dim_model` | 车型主体（从销量 ODS 派生） | `model_code`、`model_name`、`brand_id`、`oem_id`、`ads_id`、`energy_type`、`vehicle_type`、`is_active` |
| `dim_date` | 日期维度 | `date_key`、`full_date`、`year_num`、`quarter_num`、`month_num`、`month_key` |
| `dim_stock` | 股票维度 | `stock_code`、`stock_name`、`market`、`exchange`、`currency` |
| `dim_index` | 指数维度 | 首期覆盖同花顺 881125 汽车整车 |
| `dim_commodity` | 商品期货品种维度 | 首期 LC0 / CU0 / AL0 |
| `dim_area` | 地区维度 | 大区 / 省 / 地级市三级 |
| `dim_car_level` | 车型级别维度 | 手工维护 |
| `dim_energy_vehicle` | 新能源车型字典 | 手工维护，触发器回写 `dim_model` |

### 5.3 桥接表

| 表名 | 说明 |
|------|------|
| `bridge_brand_oem` | 品牌与主机厂多对多关系 |
| `bridge_company_stock` | 上市公司与股票多对多关系，含 `primary_market` |

### 5.4 ODS 表

| 表名 | 说明 |
|------|------|
| `ods_sales_monthly_model` | 车型月销量贴源 |
| `ods_sales_monthly_model_city` | 城市车型月销量贴源 |
| `ods_stock_daily` | 股票日线贴源 |
| `ods_stock_finance` | 股票财务贴源 |
| `ods_index_daily` | 行业指数日线贴源 |
| `ods_futures_daily` | 商品期货日线贴源 |

### 5.5 DWD / DWS 表

| 表名 | 说明 |
|------|------|
| `dwd_sales_monthly_model` | 销量明细标准化表 |
| `dwd_sales_monthly_model_city` | 城市销量明细标准化表 |
| `dwd_stock_daily` | 股票日线清洗表 |
| `dwd_stock_finance` | 股票财务清洗表 |
| `dws_sales_monthly_company` | 公司月度销量聚合 |
| `dws_sales_monthly_brand` | 品牌月度销量聚合 |

---

## 6. 核心数据链路

### 6.1 首次初始化链路

```text
手工执行 DDL
    -> seed_dim_date
    -> seed_dim_company_stock
    -> seed_dim_index
    -> seed_dim_commodity
    -> seed_dim_oem_brand
    -> seed_dim_from_excel
    -> load_16888_style_to_ods
    -> load_16888_city_to_ods
    -> build_sales_dimensions
    -> clean_sales_dwd           # --with-dwd
    -> update_lifecycle           # --with-dwd
```

### 6.2 月度调度链路（当前状态）

```text
每月 2 号 03:00  crawl_16888_style_monthly
    -> load_style_ods
    -> build_sales_dimensions
    -> refresh_ads_zipper
    -> run_dwd_clean
    -> update_lifecycle / run_dws_agg

每月 20-25 号 03:30  crawl_16888_city_monthly
    -> load_city_ods
    -> run_dwd_city_clean

交易日 17:30  load_stock_daily / load_futures_daily
交易日 17:45  load_index_daily
交易日 18:30  load_stock_finance
```

### 6.3 手工维度刷新链路

```text
dim_energy_vehicle.csv
    -> refresh_dim_energy_vehicle.py
    -> stg_dim_energy_vehicle
    -> db/manual/refresh_dim_energy_vehicle.sql
    -> dim_energy_vehicle
    -> 触发器同步 dim_model.energy_type / vehicle_type

dim_model_ads_matched.csv
    -> refresh_dim_model_ads.py
    -> stg_dim_model_ads_manual
    -> dim_model_ads_manual
    -> sp_refresh_dim_model_ads_all()
    -> dim_model_ads_zipper
    -> 回填 dim_model.ads_id / ads_supplier_name

dim_area.csv
    -> refresh_dim_area.py
    -> dim_area
```

---

## 7. 调度任务体系

### 7.1 任务配置字段含义

| 字段 | 说明 |
|------|------|
| `callable` | `module.path:function_name` 形式的业务入口 |
| `cron` | APScheduler CronTrigger 参数；`None` 表示事件触发 |
| `timeout_minutes` | 进程级超时（装饰器会强杀子进程） |
| `retry_times` | 失败后的重试次数 |
| `retry_interval_minutes` | 重试调度间隔 |
| `downstream` | 上游成功后触发的下游 task_id 列表 |
| `trade_calendar` | 交易日历市场（SSE/SHFE），执行前做交易日守卫 |
| `business_key_fn` | 业务周期键生成器：`last_month` / `today` |
| `kind` | 任务类型：`subprocess` 表示入口本身是 Popen 包装 |

### 7.2 当前任务清单

| task_id | 调度方式 | 说明 |
|---------|----------|------|
| `crawl_16888_style_monthly` | cron（每月 12-15 号 03:00） | 车型月销量爬虫 |
| `load_style_ods` | 事件触发 | 车型 CSV → ODS |
| `build_sales_dimensions` | 事件触发 | 派生 dim_model |
| `refresh_ads_zipper` | 事件触发 | 刷新 ADS 拉链 |
| `run_dwd_clean` | 事件触发 | 销量 DWD 清洗 |
| `update_lifecycle` | 事件触发 | 更新生命周期 |
| `run_dws_agg` | 事件触发 | DWS 聚合 |
| `crawl_16888_city_monthly` | cron（每月 20-25 号 03:30） | 城市销量爬虫 |
| `load_city_ods` | 事件触发 | 城市 CSV → ODS |
| `run_dwd_city_clean` | 事件触发 | 城市销量 DWD 清洗 |
| `load_stock_daily` | cron（交易日 17:30） | 股票日线采集 |
| `load_stock_finance` | cron（交易日 18:30） | 股票财务采集 |
| `load_index_daily` | cron（交易日 17:45） | 行业指数采集 |
| `load_futures_daily` | cron（交易日 17:30） | 商品期货采集 |

---

## 8. 依赖关系

### 8.1 Python 依赖

见 [requirements.txt](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/requirements.txt) 与 [pyproject.toml](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/pyproject.toml)：

| 依赖 | 用途 |
|------|------|
| `fastapi>=0.110.0` | 服务层框架 |
| `uvicorn[standard]>=0.27.0` | ASGI 服务器 |
| `SQLAlchemy>=2.0.0` | ORM 与数据库连接 |
| `PyMySQL>=1.1.0` | MySQL 驱动 |
| `pydantic-settings>=2.1.0` | 配置管理 |
| `python-dotenv>=1.0.0` | `.env` 文件加载 |
| `akshare>=1.11.0` | 股价 / 指数 / 期货数据采集 |
| `APScheduler>=3.10,<4.0` | 定时调度 |
| `playwright>=1.40.0` | 浏览器爬虫 |

### 8.2 模块依赖图

```text
backend/app/main.py
    -> base_infra.db.check_database_connection
    -> base_infra.logging_config.get_logger

scheduler/runner.py
    -> scheduler.task_config.TASK_CONFIG
    -> scheduler.runtime.task (decorator)
    -> scheduler.runtime.run_recovery
    -> scheduler.runtime.start_heartbeat_thread
    -> scheduler.alert.send_alert
    -> base_infra.config.get_settings
    -> base_infra.logging_config.get_logger

scheduler/jobs/*.py
    -> pipelines.* 各业务入口
    -> scheduler.runtime 包装

pipelines/*/*.py
    -> base_infra.db.engine / SessionLocal
    -> base_infra.config.get_settings
    -> pipelines.common.sql_utils
    -> pipelines.common.quality

scripts/init_database.py
    -> base_infra.db.engine
    -> base_infra.logging_config.get_logger
    -> 通过 subprocess 调用 pipelines.* 模块
```

### 8.3 数据表依赖关系

```text
ods_sales_monthly_model
    -> dim_model（派生）
    -> dwd_sales_monthly_model

dim_model + dim_oem + dim_brand + dim_date
    -> dwd_sales_monthly_model

ods_sales_monthly_model_city + dim_model + dim_area
    -> dwd_sales_monthly_model_city

ods_stock_daily + dim_stock
    -> dwd_stock_daily

ods_stock_finance + dim_stock
    -> dwd_stock_finance

dwd_sales_monthly_model
    -> dws_sales_monthly_company
    -> dws_sales_monthly_brand
    -> update_lifecycle (dim_oem / dim_model)
```

---

## 9. 配置说明

### 9.1 环境变量

从 [base_infra/config.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/base_infra/config.py) 与 [`.env.example`](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/.env.example) 读取：

| 变量 | 必填 | 说明 |
|------|------|------|
| `DB_HOST` | 是 | 数据库地址 |
| `DB_PORT` | 是 | 数据库端口 |
| `DB_USER` | 是 | 写账号用户名 |
| `DB_PASSWORD` | 是 | 写账号密码 |
| `DB_NAME` | 否 | 库名，默认 `dw_auto_stock` |
| `POWERBI_READONLY_USER` | 否 | Power BI 只读账号 |
| `POWERBI_READONLY_PASSWORD` | 否 | Power BI 只读密码 |
| `APP_ENV` | 否 | 运行环境，默认 `local` |
| `APP_DEBUG` | 否 | 调试模式，默认 `False` |
| `BACKEND_HOST` | 否 | 后端监听地址，默认 `127.0.0.1` |
| `BACKEND_PORT` | 否 | 后端监听端口，默认 `8000` |
| `ALERT_WEBHOOK_URL` | 否 | 调度器告警 Webhook |
| `ALERT_WEBHOOK_TYPE` | 否 | 告警类型：`feishu` / `wecom`，默认 `feishu` |

### 9.2 重要约定

- 环境变量名大小写敏感（`case_sensitive=True`）。
- 数据库连接串由 `Settings.database_url` 自动拼接，无需手写 `DATABASE_URL`。
- `.env` 文件位于项目根目录，参考 `.env.example`。

---

## 10. 项目运行方式

### 10.1 环境准备

```bash
# Python 3.11+
# 云 MySQL 8.0+
# 本机 IP 已加入数据库白名单
```

### 10.2 安装依赖

```bash
pip install -r requirements.txt
pip install -e .

# 若需浏览器爬虫
playwright install chromium
```

### 10.3 配置环境

```bash
copy .env.example .env
# 编辑 .env 填写数据库连接信息
```

### 10.4 初始化数仓

```bash
# 一键完整初始化（含 DDL）
python scripts/init_database.py

# 表已存在时只跑种子 / ODS / DIM
python scripts/init_database.py --skip-ddl

# 表已存在时跑全链（含 DWD 与生命周期）
python scripts/init_database.py --skip-ddl --with-dwd
```

### 10.5 启动服务

```bash
# 启动 FastAPI 健康检查
uvicorn backend.app.main:app --reload

# 访问验证
# http://localhost:8000/
# http://localhost:8000/health
```

### 10.6 启动调度器

```bash
python -m scheduler.runner
```

### 10.7 手工触发任务

```bash
# 手工刷新能源 / 车型类型字典
python -m pipelines.dim.refresh_dim_energy_vehicle

# 手工刷新 ADS 支线
python -m pipelines.dim.refresh_dim_model_ads

# 手工执行 DWS 聚合
python -m pipelines.serve.agg_sales_monthly

# 单 SQL 文件执行
python scripts/run_sql_file.py db/migrations/003_alter_dwd_sales_monthly_model.sql
```

---

## 11. 测试与运维

### 11.1 测试文件

| 文件 | 说明 |
|------|------|
| [tests/test_huawei_ads_model_scraper.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_huawei_ads_model_scraper.py) | 华为 ADS 车型爬虫测试 |
| [tests/test_refresh_dim_energy_vehicle_command.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_refresh_dim_energy_vehicle_command.py) | 新能源维度刷新命令测试 |
| [tests/test_refresh_dim_model_ads_command.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_refresh_dim_model_ads_command.py) | ADS 维度刷新命令测试 |
| [tests/test_sales_dimension_rules.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_sales_dimension_rules.py) | 销量维度规则测试 |
| [tests/test_script_entrypoint_imports.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_script_entrypoint_imports.py) | 脚本入口导入测试 |
| [tests/test_stock_akshare_networking.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/tests/test_stock_akshare_networking.py) | AKShare 网络联通性测试 |

### 11.2 运维脚本

| 文件 | 说明 |
|------|------|
| [scripts/check_innodb_trx.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/check_innodb_trx.py) | 检查 InnoDB 事务 |
| [scripts/check_scheduler_heartbeat.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/check_scheduler_heartbeat.py) | 检查调度器心跳 |
| [scripts/data_quality_check.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/data_quality_check.py) | 数据质量检查 |
| [scripts/dry_run_scheduler.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/dry_run_scheduler.py) | 调度器空跑 |
| [scripts/dwd_baseline_check.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/dwd_baseline_check.py) | DWD 基线检查 |
| [scripts/sync_trade_calendar.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/sync_trade_calendar.py) | 同步交易日历 |
| [scripts/verify_ods_futures_daily.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/verify_ods_futures_daily.py) | 验证期货 ODS |
| [scripts/verify_ods_index_daily.py](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/scripts/verify_ods_index_daily.py) | 验证指数 ODS |

---

## 12. 附录：常用入口速查

### 12.1 数据链路入口

| 目标 | 命令 |
|------|------|
| 初始化数仓 | `python scripts/init_database.py --skip-ddl --with-dwd` |
| 车型 ODS 入库 | `python -m pipelines.ingest.sales.load_16888_style_to_ods` |
| 城市 ODS 入库 | `python -m pipelines.ingest.sales.load_16888_city_to_ods` |
| 派生车型维度 | `python -m pipelines.dim.build_sales_dimensions` |
| 销量 DWD 清洗 | `python -m pipelines.staging.clean_sales_dwd` |
| 城市 DWD 清洗 | `python -m pipelines.staging.clean_sales_city_dwd` |
| DWS 聚合 | `python -m pipelines.serve.agg_sales_monthly` |

### 12.2 数据采集入口

| 目标 | 命令 |
|------|------|
| 股票日线 | `python -m pipelines.ingest.stock.load_stock_daily_akshare` |
| 股票财务 | `python -m pipelines.ingest.stock.load_stock_finance_akshare` |
| 同花顺指数 | `python -m pipelines.ingest.index.load_ths_index_daily_akshare` |
| 商品期货 | `python -m pipelines.ingest.futures.load_futures_main_sina` |
| 车型销量爬虫 | `python -m pipelines.ingest.sales.crawl_16888_style.crawl_style` |
| 城市销量爬虫 | `python -m pipelines.ingest.sales.crawl_16888_city.crawl_city` |

### 12.3 服务与调度入口

| 目标 | 命令 |
|------|------|
| 健康检查服务 | `uvicorn backend.app.main:app --reload` |
| 启动调度器 | `python -m scheduler.runner` |

---

## 13. 参考文档

- [README.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/README.md)
- [需求文档.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/需求文档.md)
- [执行大纲.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/执行大纲.md)
- [docs/warehouse-spec.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/docs/warehouse-spec.md)
- [docs/data-dictionary.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/docs/data-dictionary.md)
- [docs/metric-definition.md](file:///c:/Users/Administrator/Desktop/TraeProjects/MainProjects/CarApp/docs/metric-definition.md)
