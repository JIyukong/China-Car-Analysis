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
- AKShare 股价接口（目标来源，当前采集未落地）

### 2.2 ODS 贴源层（已落地）

代码目录：

- `pipelines/ingest/`

表前缀：

- `ods_`

当前已落地表：

- `ods_sales_monthly_model`
- `ods_sales_monthly_model_city`
- `ods_stock_daily`

说明：

- `ods_stock_daily` 当前只有表结构，不代表采集链已落地
- ODS 的职责是保留原始事实，不强行做业务判断

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

### 2.5 DWS 汇总层（已落地，但自动化未接全）

代码目录：

- `pipelines/serve/`

当前已落地表：

- `dws_sales_monthly_company`
- `dws_sales_monthly_brand`

当前已落地脚本：

- `pipelines/serve/agg_sales_monthly.py`

当前状态：

- 脚本与 DDL 已存在
- 默认初始化链未执行该脚本
- 当前调度器也未自动执行该脚本

### 2.6 ADS / BI 消费层（部分落地）

当前状态：

- Power BI 只读账号 DDL 已存在：`db/ddl/02_create_powerbi_user.sql`
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

### 3.2 月度调度链路

```text
crawl_16888_monthly
    -> load_ods_after_crawl
    -> run_dwd_clean
```

当前入口：

- `scheduler/runner.py`

当前断点：

- 不会自动执行 `build_sales_dimensions`
- 不会自动执行 `agg_sales_monthly`
- 不会自动执行股价采集

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

## 四、DDL 执行顺序

当前推荐的新库初始化顺序：

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
    -> 13_create_ods_stock_finance.sql
    -> 14_create_dim_area.sql
    -> 03_create_dwd_tables.sql
    -> 15_alter_dwd_sales_monthly_model.sql
    -> 16_create_dwd_tables_extend.sql
    -> 04_create_dws_tables.sql
    -> 02_create_powerbi_user.sql
```

依赖说明：

- `12_alter_dim_model_ads_reference.sql` 依赖 `05_create_dim_zipper_tables.sql`
- `13_create_ods_stock_finance.sql` 建 `ods_stock_finance` 贴源表
- `14_create_dim_area.sql` 建 `dim_area` 地区维度，为 `dwd_sales_monthly_model_city.area_id` 提供外键源
- `15_alter_dwd_sales_monthly_model.sql` 依赖 `03_create_dwd_tables.sql`，把 `stat_month` 改为 `CHAR(7)` 并新增 `ads_id / ads_supplier_name`
- `16_create_dwd_tables_extend.sql` 新建 `dwd_sales_monthly_model_city / dwd_stock_daily / dwd_stock_finance`，依赖 `dim_area / dim_vehicle_subtype / dim_stock / dim_model`
- `04_create_dws_tables.sql` 不是初始化脚本当前默认链的一部分，但属于 DWS 运行前置条件
- `02_create_powerbi_user.sql` 不影响主链入库，只影响下游 BI 读取
- `06_alter_dim_model_add_brand_oem_ads_columns.sql` 属于存量兼容补丁，不是新库初始化主链必跑项

## 五、目录与职责映射

| 层级 / 模块 | 目录 | 当前脚本 / 资产 |
|-------------|------|-----------------|
| ODS 贴源 | `pipelines/ingest/` | `load_16888_style_to_ods.py`、`load_16888_city_to_ods.py` |
| DIM / Bridge | `pipelines/dim/` | `seed_dim_date.py`、`seed_dim_company_stock.py`、`seed_dim_oem_brand.py`、`seed_dim_from_excel.py`、`build_sales_dimensions.py` |
| DWD | `pipelines/staging/` | `clean_sales_dwd.py`、`clean_sales_city_dwd.py`、`clean_stock_daily_dwd.py`、`clean_stock_finance_dwd.py` |
| DWS | `pipelines/serve/` | `agg_sales_monthly.py` |
| 调度 | `scheduler/` | `runner.py`、`task_config.py`、`jobs/` |
| DDL | `db/ddl/` | `00`、`01`、`02`、`03`、`04`、`05`、`07~16` |
| 手工刷新 SQL | `db/manual/` | `refresh_dim_energy_vehicle.sql`、`refresh_dim_model_ads_manual.sql`、`refresh_dim_area.sql` |
| 手工数据源 | `data/manual_dimensions/` | `dim_energy_vehicle.csv`、`dim_model_ads_matched.csv`、`dim_area.csv` |

## 六、当前不应误写为“已落地”的内容

以下内容在方向上存在，但当前不应写成现状：

- AKShare 股价采集脚本
- 自动化股价调度任务
- 自动化 `build_sales_dimensions` 调度
- 自动化 `agg_sales_monthly` 调度
- Power BI `.pbix` 报表资产
- Publish to Web 发布配置

## 七、维护要求

- 若新增脚本改变主链顺序，必须同步更新本文件
- 若某张表从“规划中”变为“已落地”，必须同时更新：
  - `README.md`
  - `执行大纲.md`
  - `需求文档.md`
  - `docs/data-dictionary.md`
- 若删除脚本或重命名路径，必须同步修正文档中的路径引用
