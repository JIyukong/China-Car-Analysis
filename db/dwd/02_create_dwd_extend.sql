-- ============================================================
-- 16_create_dwd_tables_extend.sql
-- DWD 明细层扩展建表脚本（3 张新表）
--   1. dwd_sales_monthly_model_city  月 × 车型 × 城市 销量明细
--   2. dwd_stock_daily               日 × 股票 股价明细
--   3. dwd_stock_finance             报告期 × 股票 财务指标明细
-- 依赖：dim_model / dim_area / dim_vehicle_subtype / dim_vehicle_type / dim_stock / dim_model_ads_zipper 已就位
-- ============================================================

USE dw_auto_stock;

-- ============================================================
-- 1. 车型 × 城市 月销量明细表（DWD 层，来自 ods_sales_monthly_model_city）
-- ============================================================
CREATE TABLE IF NOT EXISTS dwd_sales_monthly_model_city (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stat_month          CHAR(7)         NOT NULL                COMMENT '统计月份（YYYY-MM 格式，如 2026-07）',
    source              VARCHAR(32)     NOT NULL DEFAULT '16888_city' COMMENT '数据来源',
    model_name          VARCHAR(128)    NOT NULL                COMMENT '标准化车型名（TRIM，非空）',
    model_name_raw      VARCHAR(128)    NOT NULL                COMMENT '原始车型名（溯源，非空）',
    model_id            BIGINT UNSIGNED NOT NULL                COMMENT '车型ID → dim_model.id（非空）',
    oem_id              BIGINT UNSIGNED NOT NULL                COMMENT '主机厂ID（由 dim_model 派生，非空）',
    brand_id            BIGINT UNSIGNED NOT NULL                COMMENT '品牌ID（由 dim_model 派生，非空）',
    company_id          BIGINT UNSIGNED NOT NULL                COMMENT '上市公司ID（由 dim_model 派生，非空）',
    ads_id              BIGINT UNSIGNED DEFAULT NULL            COMMENT '智驾 ADS 拉链ID（冗余自 dim_model.ads_id）',
    ads_supplier_name   VARCHAR(128)    DEFAULT NULL            COMMENT '智驾供应商名称快照',
    city_name           VARCHAR(64)     NOT NULL                COMMENT '标准化城市名（TRIM）',
    city_name_raw       VARCHAR(64)     NOT NULL                COMMENT '原始城市名（溯源）',
    area_id             BIGINT UNSIGNED DEFAULT NULL            COMMENT '地区ID → dim_area.id（大区/省/地级市）',
    subtype_name        VARCHAR(32)     DEFAULT NULL            COMMENT '标准化车型细分类型',
    level_raw           VARCHAR(64)     DEFAULT NULL            COMMENT '原始 level_raw 溯源',
    vehicle_subtype_id  BIGINT UNSIGNED DEFAULT NULL            COMMENT '车型细分类型ID → dim_vehicle_subtype.id',
    vehicle_type_id     BIGINT UNSIGNED DEFAULT NULL            COMMENT '车型大类ID → dim_vehicle_type.id',
    sales_volume        INT UNSIGNED    NOT NULL                COMMENT '城市月销量（辆，已排除 NULL/≤0）',
    price_min           DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间下限（万元）',
    price_max           DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间上限（万元）',
    rank_no             INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    row_hash            CHAR(32)        NOT NULL                COMMENT 'ODS 幂等哈希',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dwd_sales_city_hash (row_hash),
    KEY idx_dwd_sales_city_month (stat_month),
    KEY idx_dwd_sales_city_model (model_id),
    KEY idx_dwd_sales_city_oem (oem_id),
    KEY idx_dwd_sales_city_brand (brand_id),
    KEY idx_dwd_sales_city_company (company_id),
    KEY idx_dwd_sales_city_ads (ads_id),
    KEY idx_dwd_sales_city_area (area_id),
    KEY idx_dwd_sales_city_subtype (vehicle_subtype_id),
    KEY idx_dwd_sales_city_vtype (vehicle_type_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型×城市月销量明细表（DWD 层，来自 ods_sales_monthly_model_city）';

-- ============================================================
-- 2. 股价日线明细表（DWD 层，来自 ods_stock_daily）
-- ============================================================
CREATE TABLE IF NOT EXISTS dwd_stock_daily (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    trade_date      DATE            NOT NULL                COMMENT '交易日',
    stock_id        BIGINT UNSIGNED NOT NULL                COMMENT '股票ID → dim_stock.id（未命中丢弃）',
    stock_code      VARCHAR(32)     NOT NULL                COMMENT '标准化股票代码（如 002594.SZ）',
    stock_code_raw  VARCHAR(32)     NOT NULL                COMMENT '原始股票代码（溯源）',
    stock_name      VARCHAR(128)    DEFAULT NULL            COMMENT '证券名称快照',
    market          ENUM('A','HK','US') NOT NULL            COMMENT '市场（来自 dim_stock）',
    exchange        VARCHAR(16)     DEFAULT NULL            COMMENT '交易所（SZ/SH/HKEX/NYSE/NASDAQ）',
    currency        VARCHAR(8)      DEFAULT NULL            COMMENT '币种（CNY/HKD/USD）',
    `open`          DECIMAL(12,4)   DEFAULT NULL            COMMENT '开盘价',
    high            DECIMAL(12,4)   DEFAULT NULL            COMMENT '最高价',
    low             DECIMAL(12,4)   DEFAULT NULL            COMMENT '最低价',
    `close`         DECIMAL(12,4)   DEFAULT NULL            COMMENT '收盘价',
    volume          BIGINT UNSIGNED DEFAULT NULL            COMMENT '成交量（股）',
    amount          DECIMAL(18,2)   DEFAULT NULL            COMMENT '成交额',
    pct_change      DECIMAL(8,4)    DEFAULT NULL            COMMENT '涨跌幅（%，缺失时按前一交易日回补）',
    is_valid_ohlc   TINYINT(1)      NOT NULL DEFAULT 1      COMMENT 'OHLC 校验通过标志：1=有效 0=异常',
    row_hash        CHAR(32)        NOT NULL                COMMENT 'ODS 幂等哈希',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dwd_stock_daily (stock_id, trade_date),
    UNIQUE KEY uk_dwd_stock_daily_hash (row_hash),
    KEY idx_dwd_stock_daily_trade_date (trade_date),
    KEY idx_dwd_stock_daily_stock_id (stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股价日线明细表（DWD 层，来自 ods_stock_daily）';

-- ============================================================
-- 3. 股票财务指标明细表（DWD 层，来自 ods_stock_finance）
-- ============================================================
CREATE TABLE IF NOT EXISTS dwd_stock_finance (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stock_id              BIGINT UNSIGNED NOT NULL                COMMENT '股票ID → dim_stock.id（未命中丢弃）',
    stock_code            VARCHAR(32)     NOT NULL                COMMENT '标准化股票代码',
    stock_code_raw        VARCHAR(32)     NOT NULL                COMMENT '原始股票代码（溯源）',
    stock_name            VARCHAR(128)    DEFAULT NULL            COMMENT '证券名称快照',
    market                ENUM('A','HK','US') NOT NULL            COMMENT '市场',
    report_date           DATE            NOT NULL                COMMENT '报告期',
    report_type           ENUM('Q1','Q2','Q3','Q4','年报') NOT NULL COMMENT '报告类型',
    report_year           SMALLINT UNSIGNED NOT NULL              COMMENT '报告年份（由 report_date 派生）',
    report_quarter        TINYINT UNSIGNED DEFAULT NULL           COMMENT '报告季度 1-4（年报为 4）',
    -- 收益指标
    eps                   DECIMAL(12,4)   DEFAULT NULL            COMMENT '基本每股收益（元）',
    net_profit            DECIMAL(18,2)   DEFAULT NULL            COMMENT '净利润（元）',
    -- 收入指标
    total_revenue         DECIMAL(18,2)   DEFAULT NULL            COMMENT '营业总收入（元）',
    main_revenue          DECIMAL(18,2)   DEFAULT NULL            COMMENT '主营业务收入（元）',
    total_profit          DECIMAL(18,2)   DEFAULT NULL            COMMENT '利润总额（元）',
    -- 资产负债指标
    total_assets          DECIMAL(18,2)   DEFAULT NULL            COMMENT '资产总计（元）',
    total_liabilities     DECIMAL(18,2)   DEFAULT NULL            COMMENT '负债合计（元）',
    equity                DECIMAL(18,2)   DEFAULT NULL            COMMENT '所有者权益合计（元）',
    -- 比率指标（统一百分数：15.00 表示 15%）
    roe                   DECIMAL(8,4)    DEFAULT NULL            COMMENT '净资产收益率（%，统一百分数）',
    gross_margin          DECIMAL(8,4)    DEFAULT NULL            COMMENT '销售毛利率（%，统一百分数）',
    debt_ratio            DECIMAL(8,4)    DEFAULT NULL            COMMENT '资产负债率（%，统一百分数）',
    is_ratio_normalized   TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '比率归一化标志：1=已归一化 0=未归一化',
    row_hash              CHAR(32)        NOT NULL                COMMENT 'ODS 幂等哈希',
    created_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dwd_finance (stock_id, report_date, report_type),
    UNIQUE KEY uk_dwd_finance_hash (row_hash),
    KEY idx_dwd_finance_stock_id (stock_id),
    KEY idx_dwd_finance_report_date (report_date),
    KEY idx_dwd_finance_report_year (report_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股票财务指标明细表（DWD 层，来自 ods_stock_finance）';
