# 数据字典

本页列出主要分析字段和事实表的唯一键。完整字段、约束及默认值以链接中的 DDL 和对应迁移为准。主题的数据流见 [数仓分层](warehouse-spec.md)。

## 事实表与唯一键

| 表 | 主要唯一键 | DDL |
| --- | --- | --- |
| `ods_sales_monthly_model` | 来源、月份、原始车型、原始厂商 | [销量 ODS](../db/ods/01_create_ods_sales_monthly.sql) |
| `ods_sales_monthly_model_city` | 来源、月份、原始车型、原始城市 | [销量 ODS](../db/ods/01_create_ods_sales_monthly.sql) |
| `ods_stock_daily` | 原始证券代码、交易日、来源 | [股票日线 ODS](../db/ods/02_create_ods_stock_daily.sql) |
| `ods_stock_finance` | 原始证券代码、报告期、报告类型 | [股票财务 ODS](../db/ods/03_create_ods_stock_finance.sql) |
| `ods_index_daily` | 原始指数代码、交易日、来源 | [指数 ODS](../db/ods/04_create_ods_index_daily.sql) |
| `ods_futures_daily` | 原始商品代码、交易日、来源、合约类型 | [期货 ODS](../db/ods/05_create_ods_futures_daily.sql) |
| `dwd_sales_monthly_model` | 月份、车型 ID | [车型销量 DWD](../db/dwd/01_create_dwd_sales_monthly_model.sql) |
| `dwd_sales_monthly_model_city` | 行哈希 | [城市销量及股票 DWD](../db/dwd/02_create_dwd_extend.sql) |
| `dwd_stock_daily` | 证券 ID、交易日 | [城市销量及股票 DWD](../db/dwd/02_create_dwd_extend.sql) |
| `dwd_stock_finance` | 证券 ID、报告期、报告类型 | [城市销量及股票 DWD](../db/dwd/02_create_dwd_extend.sql) |
| `dws_sales_monthly_company` / `dws_sales_monthly_brand` | 月份与公司 ID / 品牌 ID | [销量 DWS](../db/dws/01_create_dws_sales_monthly.sql) |

## 一、核心维度层

### 1.1 `dim_company`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `company_code` | `VARCHAR(32)` | 上市公司业务编码 |
| `company_name` | `VARCHAR(64)` | 上市公司简称 |
| `full_name` | `VARCHAR(128)` | 上市公司全称 |
| `company_type` | `ENUM` | A股 / 港股 / 美股 / 非上市占位 / 其他 |
| `aliases` | `JSON` | 名称别名集合 |
| `is_listed` | `TINYINT(1)` | 是否上市 |

### 1.2 `dim_oem`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `oem_code` | `VARCHAR(64)` | 主机厂业务编码 |
| `oem_name` | `VARCHAR(128)` | 主机厂名称 |
| `company_id` | `BIGINT UNSIGNED` | 关联上市公司 ID |
| `company_name_snapshot` | `VARCHAR(64)` | 上市公司名称快照 |
| `aliases` | `JSON` | 主机厂别名数组 |
| `first_seen_month` | `DATE` | 首次出现月份 |
| `last_seen_month` | `DATE` | 最后出现月份 |
| `is_active` | `TINYINT(1)` | 是否活跃 |

### 1.3 `dim_brand`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `brand_code` | `VARCHAR(64)` | 品牌编码 |
| `brand_name` | `VARCHAR(128)` | 品牌名称 |
| `is_joint_venture_brand` | `TINYINT(1)` | 是否合资品牌 |

### 1.4 `dim_model`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `model_code` | `VARCHAR(64)` | 车型编码 |
| `model_name` | `VARCHAR(128)` | 车型名称 |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `brand_name` | `VARCHAR(128)` | 品牌名称快照 |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `oem_name` | `VARCHAR(128)` | 主机厂名称快照 |
| `ads_id` | `BIGINT UNSIGNED` | 当前有效 ADS 拉链记录 ID |
| `ads_supplier_name` | `VARCHAR(128)` | 当前有效 ADS 供应商名称 |
| `energy_type` | `ENUM` | 燃油 / 新能源 / 其他 / 占位 |
| `vehicle_type` | `ENUM` | 轿车 / SUV / MPV / 其他 / 占位 |
| `first_seen_month` | `DATE` | 首次出现月份 |
| `last_seen_month` | `DATE` | 最后出现月份 |
| `is_active` | `TINYINT(1)` | 是否活跃 |

### 1.5 `dim_date`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `date_key` | `INT UNSIGNED` | 日期键 `YYYYMMDD` |
| `full_date` | `DATE` | 自然日期 |
| `year_num` | `SMALLINT UNSIGNED` | 年份 |
| `quarter_num` | `TINYINT UNSIGNED` | 季度 |
| `month_num` | `TINYINT UNSIGNED` | 月份 |
| `month_key` | `INT UNSIGNED` | 月份键 `YYYYMM` |
| `month_label` | `VARCHAR(16)` | 月份标签 |
| `is_month_start` | `TINYINT(1)` | 是否月初 |
| `is_month_end` | `TINYINT(1)` | 是否月末 |

### 1.6 `dim_stock`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stock_code` | `VARCHAR(32)` | 股票统一编码 |
| `stock_name` | `VARCHAR(128)` | 股票名称 |
| `market` | `ENUM` | A / HK / US |
| `exchange` | `VARCHAR(16)` | 交易所 |
| `currency` | `VARCHAR(8)` | 币种 |
| `is_listed` | `TINYINT(1)` | 是否在市 |

## 二、桥接层

### 2.1 `bridge_brand_oem`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `relation_type` | `ENUM` | 主销 / JV授权 / 进口代理 / 其他 |
| `effective_start_date` | `DATE` | 生效开始日期 |
| `effective_end_date` | `DATE` | 生效结束日期 |
| `is_current` | `TINYINT(1)` | 是否当前有效 |

### 2.2 `bridge_company_stock`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `stock_id` | `BIGINT UNSIGNED` | 股票 ID |
| `primary_market` | `ENUM` | 主上市市场 |
| `is_primary` | `TINYINT(1)` | 是否主标的 |

## 三、ODS 贴源层

### 3.1 `ods_sales_monthly_model`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `source` | `ENUM` | 数据来源，默认 `16888_style` |
| `stat_month` | `DATE` | 统计月份 |
| `rank_no` | `INT UNSIGNED` | 排名 |
| `model_name_raw` | `VARCHAR(128)` | 原始车型名 |
| `manufacturer_name_raw` | `VARCHAR(128)` | 原始主机厂名 |
| `price_range_raw` | `VARCHAR(64)` | 原始价格区间 |
| `sales_volume` | `INT UNSIGNED` | 月销量 |
| `source_file` | `VARCHAR(255)` | 来源文件 |
| `source_url` | `VARCHAR(255)` | 来源 URL |
| `row_hash` | `CHAR(32)` | 幂等哈希 |
| `fetch_time` | `DATETIME` | 入库时间 |

### 3.2 `ods_sales_monthly_model_city`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `source` | `ENUM` | 数据来源，默认 `16888_city` |
| `stat_month` | `DATE` | 统计月份 |
| `rank_no` | `INT UNSIGNED` | 排名 |
| `model_name_raw` | `VARCHAR(128)` | 原始车型名 |
| `city_name_raw` | `VARCHAR(64)` | 原始城市名 |
| `level_raw` | `VARCHAR(64)` | 原始级别 |
| `price_range_raw` | `VARCHAR(64)` | 原始价格区间 |
| `sales_volume` | `INT UNSIGNED` | 城市销量 |
| `source_file` | `VARCHAR(255)` | 来源文件 |
| `source_url` | `VARCHAR(255)` | 来源 URL |
| `row_hash` | `CHAR(32)` | 幂等哈希 |
| `fetch_time` | `DATETIME` | 入库时间 |

### 3.3 `ods_stock_daily`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stock_id` | `BIGINT UNSIGNED` | 可选的证券 ID |
| `stock_code_raw` | `VARCHAR(32)` | 原始证券代码 |
| `market_raw` | `VARCHAR(8)` | 原始市场代码 |
| `source_symbol` | `VARCHAR(32)` | 数据源请求使用的证券代码 |
| `trade_date` | `DATE` | 交易日 |
| `open` | `DECIMAL(12,4)` | 开盘价 |
| `high` | `DECIMAL(12,4)` | 最高价 |
| `low` | `DECIMAL(12,4)` | 最低价 |
| `close` | `DECIMAL(12,4)` | 收盘价 |
| `volume` | `BIGINT` | 成交量 |
| `amount` | `DECIMAL(18,2)` | 成交额 |
| `pct_change` | `DECIMAL(8,4)` | 涨跌幅 |
| `source` | `ENUM` | 数据来源，默认 `akshare` |
| `fetch_time` | `DATETIME` | 入库时间 |

采集实现见 [AKShare 股票日线加载器](../pipelines/ingest/stock/load_stock_daily_akshare.py)。

## 四、DWD 明细层

### 4.1 `dwd_sales_monthly_model`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stat_month` | `CHAR(7)` | 统计月份（`YYYY-MM`，如 `2026-07`） |
| `source` | `VARCHAR(32)` | 数据来源 |
| `model_name` | `VARCHAR(128)` | 标准化车型名 |
| `model_name_raw` | `VARCHAR(128)` | 原始车型名 |
| `model_id` | `BIGINT UNSIGNED` | 车型 ID |
| `manufacturer_name` | `VARCHAR(128)` | 标准化主机厂名 |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `ads_id` | `BIGINT UNSIGNED` | 智驾 ADS 拉链 ID（冗余自 `dim_model.ads_id`） |
| `ads_supplier_name` | `VARCHAR(128)` | 智驾供应商名称快照 |
| `sales_volume` | `INT UNSIGNED` | 月销量 |
| `price_min` | `DECIMAL(10,2)` | 价格下限 |
| `price_max` | `DECIMAL(10,2)` | 价格上限 |
| `rank_no` | `INT UNSIGNED` | 排名 |
| `row_hash` | `CHAR(32)` | 幂等哈希 |

### 4.2 `dwd_sales_monthly_model_city`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stat_month` | `CHAR(7)` | 统计月份（`YYYY-MM`） |
| `source` | `VARCHAR(32)` | 数据来源 |
| `model_name` | `VARCHAR(128)` | 标准化车型名 |
| `model_name_raw` | `VARCHAR(128)` | 原始车型名 |
| `model_id` | `BIGINT UNSIGNED` | 车型 ID |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `ads_id` | `BIGINT UNSIGNED` | 智驾 ADS 拉链 ID（冗余自 `dim_model.ads_id`） |
| `ads_supplier_name` | `VARCHAR(128)` | 智驾供应商名称快照 |
| `city_name` | `VARCHAR(64)` | 标准化城市名 |
| `city_name_raw` | `VARCHAR(64)` | 原始城市名 |
| `area_id` | `BIGINT UNSIGNED` | 地区 ID → `dim_area.id` |
| `subtype_name` | `VARCHAR(32)` | 标准化车型细分类型 |
| `level_raw` | `VARCHAR(64)` | 原始级别 |
| `vehicle_subtype_id` | `BIGINT UNSIGNED` | 车型细分类型 ID → `dim_vehicle_subtype.id` |
| `vehicle_type_id` | `BIGINT UNSIGNED` | 车型大类 ID → `dim_vehicle_type.id` |
| `sales_volume` | `INT UNSIGNED` | 城市月销量 |
| `price_min` | `DECIMAL(10,2)` | 价格下限 |
| `price_max` | `DECIMAL(10,2)` | 价格上限 |
| `rank_no` | `INT UNSIGNED` | 排名 |
| `row_hash` | `CHAR(32)` | 幂等哈希 |

### 4.3 `dwd_stock_daily`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `trade_date` | `DATE` | 交易日 |
| `stock_id` | `BIGINT UNSIGNED` | 股票 ID（强制非空） |
| `stock_code` | `VARCHAR(32)` | 标准化股票代码 |
| `stock_code_raw` | `VARCHAR(32)` | 原始股票代码 |
| `stock_name` | `VARCHAR(128)` | 证券名称快照 |
| `market` | `ENUM` | A / HK / US |
| `exchange` | `VARCHAR(16)` | 交易所 |
| `currency` | `VARCHAR(8)` | 币种 |
| `open` | `DECIMAL(12,4)` | 开盘价 |
| `high` | `DECIMAL(12,4)` | 最高价 |
| `low` | `DECIMAL(12,4)` | 最低价 |
| `close` | `DECIMAL(12,4)` | 收盘价 |
| `volume` | `BIGINT UNSIGNED` | 成交量 |
| `amount` | `DECIMAL(18,2)` | 成交额 |
| `pct_change` | `DECIMAL(8,4)` | 涨跌幅（%） |
| `is_valid_ohlc` | `TINYINT(1)` | OHLC 校验通过标志 |
| `row_hash` | `CHAR(32)` | 幂等哈希 |

### 4.4 `dwd_stock_finance`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stock_id` | `BIGINT UNSIGNED` | 股票 ID（强制非空） |
| `stock_code` | `VARCHAR(32)` | 标准化股票代码 |
| `stock_code_raw` | `VARCHAR(32)` | 原始股票代码 |
| `stock_name` | `VARCHAR(128)` | 证券名称快照 |
| `market` | `ENUM` | A / HK / US |
| `report_date` | `DATE` | 报告期 |
| `report_type` | `ENUM` | Q1/Q2/Q3/Q4/年报 |
| `report_year` | `SMALLINT UNSIGNED` | 报告年份 |
| `report_quarter` | `TINYINT UNSIGNED` | 报告季度 1-4 |
| `eps` | `DECIMAL(12,4)` | 基本每股收益（元） |
| `net_profit` | `DECIMAL(18,2)` | 净利润（元） |
| `total_revenue` | `DECIMAL(18,2)` | 营业总收入（元） |
| `main_revenue` | `DECIMAL(18,2)` | 主营业务收入（元） |
| `total_profit` | `DECIMAL(18,2)` | 利润总额（元） |
| `total_assets` | `DECIMAL(18,2)` | 资产总计（元） |
| `total_liabilities` | `DECIMAL(18,2)` | 负债合计（元） |
| `equity` | `DECIMAL(18,2)` | 所有者权益合计（元） |
| `roe` | `DECIMAL(8,4)` | 净资产收益率（%，统一百分数） |
| `gross_margin` | `DECIMAL(8,4)` | 销售毛利率（%，统一百分数） |
| `debt_ratio` | `DECIMAL(8,4)` | 资产负债率（%，统一百分数） |
| `is_ratio_normalized` | `TINYINT(1)` | 比率归一化标志 |
| `row_hash` | `CHAR(32)` | 幂等哈希 |

## 五、DWS 汇总层

### 5.1 `dws_sales_monthly_company`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stat_month` | `DATE` | 统计月份 |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `company_name` | `VARCHAR(64)` | 上市公司名称快照 |
| `total_sales` | `INT UNSIGNED` | 月度总销量 |
| `market_share` | `DECIMAL(8,4)` | 市占率 |
| `yoy_growth` | `DECIMAL(8,2)` | 同比增长率 |
| `mom_growth` | `DECIMAL(8,2)` | 环比增长率 |

### 5.2 `dws_sales_monthly_brand`

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stat_month` | `DATE` | 统计月份 |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `brand_name` | `VARCHAR(128)` | 品牌名称快照 |
| `total_sales` | `INT UNSIGNED` | 月度总销量 |
| `market_share` | `DECIMAL(8,4)` | 市占率 |
| `yoy_growth` | `DECIMAL(8,2)` | 同比增长率 |
| `mom_growth` | `DECIMAL(8,2)` | 环比增长率 |

## 六、手工维度支线

### 6.1 `dim_energy_vehicle`

说明：手工维护能源类型与车型类型权威映射，通过刷新脚本与触发器影响 `dim_model`。

### 6.2 `dim_model_ads_manual`

说明：ADS 手工权威存储表，可包含尚未进入 `dim_model` 的车型记录。

### 6.3 `dim_model_ads_zipper`

说明：ADS 拉链表，仅接收当前已存在于 `dim_model` 的车型，用于保存供应商 / 模式的历史版本。

### 6.4 `dim_area`

说明：手工维护中国大陆行政区划（大区 / 省 / 地级市），承接 16888 city 源的城市自然键，用于后续 `dwd_sales_monthly_model_city` 关联。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `city_code` | `VARCHAR(8)` | 城市编码（GB/T 2260 六位） |
| `city_name` | `VARCHAR(64)` | 地级行政区全称 |
| `city_short_name` | `VARCHAR(32)` | 城市简称 |
| `city_level` | `ENUM` | 地级市 / 直辖市 / 自治州 / 地区 / 盟 |
| `province_code` | `VARCHAR(8)` | 省级编码 |
| `province_name` | `VARCHAR(32)` | 省级全称 |
| `province_type` | `ENUM` | 省 / 自治区 / 直辖市 |
| `region_code` | `VARCHAR(8)` | 大区编码（EC/SC/NC/CC/SW/NW/NE） |
| `region_name` | `ENUM` | 华东 / 华南 / 华北 / 华中 / 西南 / 西北 / 东北 |
