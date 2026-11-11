# 数仓分层规范

> 口径：本规范以当前仓库真实代码、DDL 与脚本为准。
> 说明：这里区分“已落地”“部分落地”“规划中”，避免把理想模型误写成现状。

## 一、分层总览

```text
数据源层
    -> ODS 贴源层
    -> DIM / Bridge 维度层
    -> DWD 明细层
    -> DWS 汇总层
    -> ADS / BI 消费层
```

当前实际落地情况：

- 已落地：ODS、DIM / Bridge、DWD、部分 DWS、两条手工维度支线
- 部分落地：ADS / BI 消费层的数据库准备
- 未落地：股价采集后的完整应用层、Power BI 资产

## 二、分层定义

### 2.1 数据源层

当前真实数据源：

- 16888 已抓取的 style / city 文件
- 手工 CSV：
  - `data/manual_dimensions/dim_energy_vehicle.csv`
  - `data/manual_dimensions/dim_model_ads_matched.csv`
- AKShare 股价接口（A 股/H 股日线、A 股财务，已落地）
- AKShare 同花顺行业指数接口（881125 汽车整车，已落地）
- AKShare 新浪财经期货接口（碳酸锂/铜/铝主力连续，已落地）

### 2.2 ODS 贴源层（已落地）

代码目录：

- `pipelines/ingest/`

表前缀：

- `ods_`

当前已落地表：

- `ods_sales_monthly_model`
- `ods_sales_monthly_model_city`
- `ods_stock_daily`
- `ods_stock_finance`
- `ods_index_daily`
- `ods_futures_daily`

说明：

- ODS 的职责是保留原始事实，不强行做业务判断
- `ods_stock_daily` / `ods_stock_finance` / `ods_index_daily` / `ods_futures_daily` 均已有对应采集脚本并接入调度

### 2.3 DIM / Bridge 维度层（已落地）

代码目录：

- `pipelines/dim/`

当前已落地核心表：

- `dim_company`
- `dim_oem`
- `dim_brand`
- `dim_model`
- `dim_date`
- `dim_stock`
- `dim_area`
- `bridge_brand_oem`
- `bridge_company_stock`

设计要点：

- `dim_company` 与 `dim_oem` 解耦
- `dim_model` 从销量主链派生，不承载手工权威源
- 多对多关系通过桥接表维护

### 2.4 DWD 明细层（已落地）

代码目录：

- `pipelines/staging/`

当前已落地表：

- `dwd_sales_monthly_model`（`stat_month` 为 `CHAR(7)` `YYYY-MM`；含 `ads_id / ads_supplier_name` 冗余字段）
- `dwd_sales_monthly_model_city`
- `dwd_stock_daily`
- `dwd_stock_finance`

当前已落地脚本：

- `pipelines/staging/clean_sales_dwd.py`
- `pipelines/staging/clean_sales_city_dwd.py`
- `pipelines/staging/clean_stock_daily_dwd.py`
- `pipelines/staging/clean_stock_finance_dwd.py`

职责：

- 标准化原始文本
- `stat_month` 统一为 `CHAR(7) YYYY-MM`
- 补充维度 ID（含 ads_id / area_id / vehicle_subtype_id / stock_id 等）
- 适度反规范化冗余（如 `ads_supplier_name` / `stock_name` / `market`）
- 解析价格区间、OHLC 校验、财务比率归一化
- 过滤异常值
- 形成可聚合的销量 / 股价 / 财务明细

### 2.5 DWS 汇总层（已落地）

代码目录：

- `pipelines/serve/`

当前已落地表：

- `dws_sales_monthly_company`
- `dws_sales_monthly_brand`

当前已落地脚本：

- `pipelines/serve/agg_sales_monthly.py`

当前状态：

- 脚本与 DDL 已存在
- 已接入调度链路（`run_dws_agg` 任务，由 `run_dwd_clean` 下游触发）

### 2.6 ADS / BI 消费层（部分落地）

当前状态：

- Power BI 只读账号 DDL 已存在：`db/system/01_create_powerbi_user.sql`（权限脚本，需 DBA 手工执行）
- 面向报表的最终应用资产尚未落地
- 当前不应把任何 `.pbix` 或已发布报表写成现状

## 三、真实数据流转规则

### 3.1 首次初始化链路

```text
手工执行 DDL
    -> seed_dim_date
    -> seed_dim_company_stock
    -> seed_dim_oem_brand
    -> seed_dim_from_excel
    -> load_16888_style_to_ods
    -> load_16888_city_to_ods
    -> build_sales_dimensions
    -> clean_sales_dwd           # 可选
    -> update_lifecycle          # 可选
```

当前入口：

- `scripts/init_database.py`

说明：

- `scripts/init_database.py` 当前不会自动执行 `04_create_dws_tables.sql`
- `scripts/init_database.py` 当前不会自动执行 `02_create_powerbi_user.sql`
- 若要使用 DWS 或 Power BI 只读账号，需要先手工完成对应 SQL

### 3.2 调度链路

`runner.py` 遍历 `task_config.py` 注册全部任务，包含三条独立链路：

**style 月链**（每月 12-15 号 03:00 探测）：

```text
crawl_16888_style_monthly -> load_style_ods -> build_sales_dimensions
    -> refresh_ads_zipper -> run_dwd_clean
    -> update_lifecycle（并行）/ run_dws_agg（并行）
```

**city 月链**（每月 20-25 号 03:30 探测）：

```text
crawl_16888_city_monthly -> load_city_ods -> run_dwd_city_clean
```

**日链**（交易日守卫，mon-fri）：

```text
17:30  load_stock_daily / load_futures_daily
17:45  load_index_daily
18:30  load_stock_finance
```

当前入口：

- `scheduler/runner.py`

当前状态：

- 所有任务均已在 `task_config.py` 注册并由 `runner.py` 自动加载
- `build_sales_dimensions`、`agg_sales_monthly`（run_dws_agg）、城市 DWD 清洗均已接入调度
- 股价/指数/期货/财务采集任务均已接入调度
- 待验证项：生产环境连续运行稳定性

### 3.3 手工维度链路

#### 能源 / 车型类型字典

```text
dim_energy_vehicle.csv
    -> refresh_dim_energy_vehicle.py
    -> stg_dim_energy_vehicle
    -> refresh_dim_energy_vehicle.sql
    -> dim_energy_vehicle
    -> 触发器回写 dim_model
```

#### ADS 手工维度

```text
dim_model_ads_matched.csv
    -> refresh_dim_model_ads.py
    -> stg_dim_model_ads_manual
    -> refresh_dim_model_ads_manual.sql
    -> dim_model_ads_manual
    -> sp_refresh_dim_model_ads_all()
    -> dim_model_ads_zipper
    -> 回填 dim_model
```

#### 维度缺失治理

2026-07 测试期间新增 `pipelines/dim/dimension_readiness.py`：

- `build_sales_dimensions` 生成 dim_model 后，新增车型默认 `energy_type='占位'`、`vehicle_type='占位'`、`ads_id=NULL`。
- `run_dwd_clean` 后、`run_dwd_city_clean` 后、DWS 聚合前调用 `check_dimension_readiness()`。
- 缺失率超过阈值（默认 5%）时抛出 `SkipTask`，调度器次日自动重试，同时日志输出可直接复制到手工 CSV 的补维清单。
- 阈值内的少量缺失允许继续入库，但 `scripts/data_quality_check.py` 会标记为 FAIL，提醒人工修复。

## 四、DDL 执行顺序

SQL 已按数仓分层拆分到 `db/<layer>/` 目录，`scripts/init_database.py` 按以下目录顺序自动发现并执行：

```text
system → ods → dim → dwd → dws → ads → scheduler
       → migrations（结构补丁）→ triggers → procedures → events
```

各目录关键文件：

- `db/system/`：`00_create_database.sql`（建库）、`01_create_powerbi_user.sql`（权限，需 DBA 手工执行）
- `db/ods/`：`01_create_ods_sales_monthly.sql`、`02_create_ods_stock_daily.sql`、`03_create_ods_stock_finance.sql`、`04_create_ods_index_daily.sql`、`05_create_ods_futures_daily.sql`
- `db/dim/`：`01_create_dim_core.sql`、`02_create_dim_bridge.sql`、`03_create_dim_zipper.sql`、`04_create_dim_energy_vehicle.sql`、`05_create_dim_model_ads_manual.sql`、`06_create_dim_area.sql`、`07_create_dim_index.sql`、`08_create_dim_commodity.sql`
- `db/dwd/`：`01_create_dwd_sales_monthly_model.sql`、`02_create_dwd_extend.sql`
- `db/dws/`：`01_create_dws_sales_monthly.sql`
- `db/migrations/`：`001_alter_dim_model_add_brand_oem_ads_columns.sql`、`002_alter_dim_model_ads_reference.sql`、`003_alter_dwd_sales_monthly_model.sql`
- `db/triggers/`：`01_create_dim_triggers.sql`
- `db/procedures/`：`01_sp_etl_build_sales_dimensions.sql` ~ `07_sp_refresh_dim_model_ads_all.sql`
- `db/events/`：`01_ev_refresh_dim_model_ads_all.sql`
- `db/manual/`：`refresh_dim_energy_vehicle.sql`、`refresh_dim_model_ads_manual.sql`、`refresh_dim_area.sql`（手工执行）

依赖说明：

- `migrations/002_alter_dim_model_ads_reference.sql` 依赖 `dim/03_create_dim_zipper.sql`
- `dwd/02_create_dwd_extend.sql` 新建 `dwd_sales_monthly_model_city` / `dwd_stock_daily` / `dwd_stock_finance`，依赖 `dim_area` / `dim_stock` / `dim_model`
- `dws/01_create_dws_sales_monthly.sql` 不是初始化脚本默认链的一部分（由 `init_database.py` 自动发现执行），但 DWS 聚合脚本运行前需要该表存在
- `system/01_create_powerbi_user.sql` 不影响主链入库，只影响下游 BI 读取，需 DBA 手工执行
- `migrations/001_alter_dim_model_add_brand_oem_ads_columns.sql` 属于存量兼容补丁，不是新库初始化主链必跑项

## 五、目录与职责映射

| 层级 / 模块 | 目录 | 当前脚本 / 资产 |
|-------------|------|-----------------|
| ODS 贴源 | `pipelines/ingest/` | `load_16888_style_to_ods.py`、`load_16888_city_to_ods.py` |
| DIM / Bridge | `pipelines/dim/` | `seed_dim_date.py`、`seed_dim_company_stock.py`、`seed_dim_oem_brand.py`、`seed_dim_from_excel.py`、`build_sales_dimensions.py` |
| 维度就绪检查 | `pipelines/dim/` | 通过 `dimension_readiness.py` 统一检测 energy_type、vehicle_type、ads、area、vehicle_subtype 缺失率 |
| DWD | `pipelines/staging/` | `clean_sales_dwd.py`、`clean_sales_city_dwd.py`、`clean_stock_daily_dwd.py`、`clean_stock_finance_dwd.py` |
| DWS | `pipelines/serve/` | `agg_sales_monthly.py` |
| 调度 | `scheduler/` | `runner.py`、`task_config.py`、`jobs/` |
| DDL（分层目录） | `db/system/`、`db/ods/`、`db/dim/`、`db/dwd/`、`db/dws/`、`db/ads/`、`db/scheduler/`、`db/migrations/`、`db/triggers/`、`db/procedures/`、`db/events/` | 按 system→ods→dim→dwd→dws→ads→scheduler→migrations→triggers→procedures→events 顺序自动发现执行（见 `scripts/init_database.py`） |
| 手工刷新 SQL | `db/manual/` | `refresh_dim_energy_vehicle.sql`、`refresh_dim_model_ads_manual.sql`、`refresh_dim_area.sql` |
| 手工数据源 | `data/manual_dimensions/` | `dim_energy_vehicle.csv`、`dim_model_ads_matched.csv`、`dim_area.csv` |

## 六、当前不应误写为"已落地"的内容

以下内容在方向上存在，但当前不应写成现状：

- Power BI `.pbix` 报表资产
- Publish to Web 发布配置
- 调度链路生产环境连续运行验证（代码已注册，但尚未在生产环境长期验证）
- 数据质量校验自动化（脚本已存在，但未接入调度链路）

## 七、维护要求

- 若新增脚本改变主链顺序，必须同步更新本文件
- 若某张表从“规划中”变为“已落地”，必须同时更新：
  - `README.md`
  - `执行大纲.md`
  - `需求文档.md`
  - `docs/data-dictionary.md`
- 若删除脚本或重命名路径，必须同步修正文档中的路径引用
