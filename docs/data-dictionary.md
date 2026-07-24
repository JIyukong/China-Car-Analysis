# 数据字典

> 口径：以当前仓库真实 DDL 与已落地脚本为准。
> 状态标记：`已落地` / `部分落地` / `规划中`。

## 一、核心维度层

### 1.1 `dim_company`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `company_code` | `VARCHAR(32)` | 上市公司业务编码 |
| `company_name` | `VARCHAR(64)` | 上市公司简称 |
| `full_name` | `VARCHAR(128)` | 上市公司全称 |
| `company_type` | `ENUM` | A股 / 港股 / 美股 / 非上市占位 / 其他 |
| `aliases` | `JSON` | 名称别名集合 |
| `is_listed` | `TINYINT(1)` | 是否上市 |

### 1.2 `dim_oem`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `oem_code` | `VARCHAR(64)` | 主机厂业务编码 |
| `oem_name` | `VARCHAR(128)` | 主机厂名称 |
| `company_id` | `BIGINT UNSIGNED` | 关联上市公司 ID |
| `company_name_snapshot` | `VARCHAR(64)` | 上市公司名称快照 |
| `oem_type` | `ENUM` | 自主 / 合资 / 进口 / 其他 |
| `first_seen_month` | `DATE` | 首次出现月份 |
| `last_seen_month` | `DATE` | 最后出现月份 |
| `is_active` | `TINYINT(1)` | 是否活跃 |

### 1.3 `dim_brand`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `brand_code` | `VARCHAR(64)` | 品牌编码 |
| `brand_name` | `VARCHAR(128)` | 品牌名称 |
| `is_joint_venture_brand` | `TINYINT(1)` | 是否合资品牌 |

### 1.4 `dim_model`（已落地）

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
| `energy_type` | `ENUM` | 燃油 / 新能源 / 其他 |
| `vehicle_type` | `ENUM` | 轿车 / SUV / MPV / 跑车 / 皮卡 / 微面 / 其他 |
| `first_seen_month` | `DATE` | 首次出现月份 |
| `last_seen_month` | `DATE` | 最后出现月份 |
| `is_active` | `TINYINT(1)` | 是否活跃 |

### 1.5 `dim_date`（已落地）

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

### 1.6 `dim_stock`（已落地）

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

### 2.1 `bridge_brand_oem`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `relation_type` | `ENUM` | 主销 / JV授权 / 进口代理 / 其他 |
| `effective_start_date` | `DATE` | 生效开始日期 |
| `effective_end_date` | `DATE` | 生效结束日期 |
| `is_current` | `TINYINT(1)` | 是否当前有效 |

### 2.2 `bridge_company_stock`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `stock_id` | `BIGINT UNSIGNED` | 股票 ID |
| `primary_market` | `ENUM` | 主上市市场 |
| `is_primary` | `TINYINT(1)` | 是否主标的 |

## 三、ODS 贴源层

### 3.1 `ods_sales_monthly_model`（已落地）

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

### 3.2 `ods_sales_monthly_model_city`（已落地）

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

### 3.3 `ods_stock_daily`（部分落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stock_id` | `BIGINT UNSIGNED` | 股票 ID |
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

说明：当前只有表结构与目标口径，未发现实际采集入库脚本。

## 四、DWD 明细层

### 4.1 `dwd_sales_monthly_model`（已落地）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | `BIGINT UNSIGNED` | 主键 ID |
| `stat_month` | `DATE` | 统计月份 |
| `source` | `VARCHAR(32)` | 数据来源 |
| `model_name` | `VARCHAR(128)` | 标准化车型名 |
| `model_name_raw` | `VARCHAR(128)` | 原始车型名 |
| `model_id` | `BIGINT UNSIGNED` | 车型 ID |
| `manufacturer_name` | `VARCHAR(128)` | 标准化主机厂名 |
| `oem_id` | `BIGINT UNSIGNED` | 主机厂 ID |
| `brand_id` | `BIGINT UNSIGNED` | 品牌 ID |
| `company_id` | `BIGINT UNSIGNED` | 上市公司 ID |
| `sales_volume` | `INT UNSIGNED` | 月销量 |
| `price_min` | `DECIMAL(10,2)` | 价格下限 |
| `price_max` | `DECIMAL(10,2)` | 价格上限 |
| `rank_no` | `INT UNSIGNED` | 排名 |
| `row_hash` | `CHAR(32)` | 幂等哈希 |

## 五、DWS 汇总层

### 5.1 `dws_sales_monthly_company`（已落地）

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

### 5.2 `dws_sales_monthly_brand`（已落地）

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

说明：当前未保留“已落地”的 `dws_sales_monthly_model` 或 `dws_sales_monthly_city` 表定义，因为仓库现状仅明确存在上述两张 DWS 表。

## 六、手工维度支线

### 6.1 `dim_energy_vehicle`（已落地）

说明：手工维护能源类型与车型类型权威映射，通过刷新脚本与触发器影响 `dim_model`。

### 6.2 `dim_model_ads_manual`（已落地）

说明：ADS 手工权威存储表，可包含尚未进入 `dim_model` 的车型记录。

### 6.3 `dim_model_ads_zipper`（已落地）

说明：ADS 拉链表，仅接收当前已存在于 `dim_model` 的车型，用于保存供应商 / 模式的历史版本。

## 七、当前未落地但已知规划

以下对象在项目方向中存在，但当前不应写成“已落地”：

- AKShare 股价采集脚本
- 以股价为输入的 DWD / DWS / ADS 应用表
- Power BI 报表资产
- Publish to Web 配置产物
