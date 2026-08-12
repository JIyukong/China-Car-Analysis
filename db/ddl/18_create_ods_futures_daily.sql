-- ============================================================
-- 18_create_ods_futures_daily.sql
-- 商品期货主力连续日线贴源表 DDL
-- 首期覆盖碳酸锂 LC0 / 铜 CU0 / 铝 AL0，作为整车金属 BOM 成本基准
-- 遵循 ODS 设计原则：保留原始自然键快照，不强制外键约束
-- ============================================================

USE dw_auto_stock;

-- ------------------------------------------------------------
-- 1. 商品品种维度表 dim_commodity
--    描述期货品种本体（碳酸锂/铜/铝），与 dim_stock 平级
-- ------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS dim_commodity;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS dim_commodity (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    commodity_code  VARCHAR(16)     NOT NULL                COMMENT '品种大写代码（如 LC/CU/AL）',
    commodity_name  VARCHAR(64)     NOT NULL                COMMENT '品种中文名（如 碳酸锂）',
    exchange        VARCHAR(16)     NOT NULL                COMMENT '交易所（GFEX/SHFE/DCE/CZCE/CFFEX/INE）',
    category        VARCHAR(32)     DEFAULT NULL            COMMENT '品种类别（energy_metal/industrial_metal/precious_metal/agricultural/chemical/energy）',
    trading_unit    VARCHAR(64)     DEFAULT NULL            COMMENT '交易单位（如 5吨/手）',
    quote_unit      VARCHAR(32)     DEFAULT NULL            COMMENT '报价单位（如 元/吨）',
    listed_date     DATE            DEFAULT NULL            COMMENT '上市日期',
    main_symbol     VARCHAR(16)     NOT NULL                COMMENT '主力连续 symbol（如 LC0/CU0/AL0），采集入参',
    currency        VARCHAR(8)      NOT NULL DEFAULT 'CNY'  COMMENT '计价货币',
    description     VARCHAR(255)    DEFAULT NULL            COMMENT '品种描述',
    is_active       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用（1启用/0停用）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_commodity_code (commodity_code),
    KEY idx_commodity_exchange (exchange),
    KEY idx_commodity_category (category),
    KEY idx_commodity_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品期货品种维度表（与 dim_stock 平级，描述期货品种本体）';

-- ------------------------------------------------------------
-- 2. 商品期货日线贴源表 ods_futures_daily
--    首期只存主力连续日线；保留原始快照字段，不强制外键约束
--    期货特有字段：open_interest（持仓量）、settle（动态结算价）
-- ------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS ods_futures_daily;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS ods_futures_daily (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    commodity_id        BIGINT UNSIGNED DEFAULT NULL            COMMENT '品种ID（可选映射，便于下游使用）',
    commodity_code_raw  VARCHAR(16)     NOT NULL                COMMENT '原始品种代码（如 LC/CU/AL）',
    commodity_name_raw  VARCHAR(64)     DEFAULT NULL            COMMENT '原始品种名快照',
    main_symbol_raw     VARCHAR(16)     NOT NULL                COMMENT '采集 symbol（如 LC0/CU0/AL0）',
    exchange_raw        VARCHAR(16)     NOT NULL                COMMENT '交易所快照（GFEX/SHFE）',
    contract_type       VARCHAR(16)     NOT NULL DEFAULT 'main_continuous' COMMENT '合约类型（main_continuous/specific）',
    trade_date          DATE            NOT NULL                COMMENT '交易日期',

    -- 行情指标
    open                DECIMAL(14,4)   DEFAULT NULL            COMMENT '开盘价（元/吨）',
    high                DECIMAL(14,4)   DEFAULT NULL            COMMENT '最高价（元/吨）',
    low                 DECIMAL(14,4)   DEFAULT NULL            COMMENT '最低价（元/吨）',
    close               DECIMAL(14,4)   DEFAULT NULL            COMMENT '收盘价（元/吨）',
    volume              BIGINT          DEFAULT NULL            COMMENT '成交量（手）',
    open_interest       BIGINT          DEFAULT NULL            COMMENT '持仓量（手），期货特有',
    settle              DECIMAL(14,4)   DEFAULT NULL            COMMENT '动态结算价（元/吨），期货特有',
    amount              DECIMAL(20,2)   DEFAULT NULL            COMMENT '成交额（元），主连接口不返回时为空',

    -- 元数据
    source              ENUM('akshare','other') NOT NULL DEFAULT 'akshare' COMMENT '数据来源',
    fetch_time          DATETIME        NOT NULL                COMMENT '采集时间',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_commodity_date (commodity_code_raw, trade_date, source, contract_type),
    KEY idx_ods_futures_commodity_id (commodity_id),
    KEY idx_ods_futures_code (commodity_code_raw),
    KEY idx_ods_futures_date (trade_date),
    KEY idx_ods_futures_exchange (exchange_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品期货日线贴源表（原始未清洗，含持仓量与结算价等期货特有字段，不强制外键约束）';
