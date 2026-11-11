-- ============================================================
-- ods/05_create_ods_futures_daily.sql
-- 商品期货主力连续日线贴源表 ods_futures_daily
-- 来源：原 18_create_ods_futures_daily.sql 拆分
-- 首期覆盖碳酸锂 LC0 / 铜 CU0 / 铝 AL0，作为整车金属 BOM 成本基准
-- 遵循 ODS 设计原则：保留原始自然键快照，不强制外键约束
-- 期货特有字段：open_interest（持仓量）、settle（动态结算价）
-- ============================================================

USE dw_auto_stock;

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
