-- ============================================================
-- 13_create_ods_stock_finance.sql
-- 股票财务数据贴源表 DDL
-- 用于存储 akshare 采集的股票财务报表数据
-- 遵循 ODS 设计原则：保留原始自然键快照，不强制外键约束
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS ods_stock_finance;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS ods_stock_finance (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stock_id            BIGINT UNSIGNED DEFAULT NULL            COMMENT '股票ID（可选映射，用于下游使用便利）',
    stock_code_raw      VARCHAR(32)     NOT NULL                COMMENT '原始股票代码（如 002594.SZ / 1211.HK）',
    stock_name_raw      VARCHAR(128)    DEFAULT NULL            COMMENT '原始股票名称快照',
    market_raw          VARCHAR(8)      NOT NULL DEFAULT 'A'    COMMENT '原始市场代码（A/HK）',
    exchange_raw        VARCHAR(16)     DEFAULT NULL            COMMENT '原始交易所代码（SZ/SH/HKEX）',
    source_symbol       VARCHAR(32)     DEFAULT NULL            COMMENT '请求源接口时使用的 symbol 快照',
    report_date         DATE            NOT NULL                COMMENT '报告期',
    report_type         ENUM('Q1','Q2','Q3','Q4','年报') NOT NULL DEFAULT '年报' COMMENT '报告类型',

    -- 收益指标
    eps                 DECIMAL(12,4)   DEFAULT NULL            COMMENT '基本每股收益（元）',
    net_profit          DECIMAL(18,2)   DEFAULT NULL            COMMENT '净利润（元）',

    -- 收入指标
    total_revenue       DECIMAL(18,2)   DEFAULT NULL            COMMENT '营业总收入（元）',
    main_revenue        DECIMAL(18,2)   DEFAULT NULL            COMMENT '主营业务收入（元）',
    total_profit        DECIMAL(18,2)   DEFAULT NULL            COMMENT '利润总额（元）',

    -- 资产负债指标
    total_assets        DECIMAL(18,2)   DEFAULT NULL            COMMENT '资产总计（元）',
    total_liabilities   DECIMAL(18,2)   DEFAULT NULL            COMMENT '负债合计（元）',
    equity              DECIMAL(18,2)   DEFAULT NULL            COMMENT '所有者权益合计（元）',

    -- 比率指标
    roe                 DECIMAL(8,4)    DEFAULT NULL            COMMENT '净资产收益率（%）',
    gross_margin        DECIMAL(8,4)    DEFAULT NULL            COMMENT '销售毛利率（%）',
    debt_ratio          DECIMAL(8,4)    DEFAULT NULL            COMMENT '资产负债率（%）',

    -- 元数据
    source              ENUM('akshare','other') NOT NULL DEFAULT 'akshare' COMMENT '数据来源',
    fetch_time          DATETIME        NOT NULL                COMMENT '采集时间',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_code_report (stock_code_raw, report_date, report_type),
    KEY idx_finance_stock_id (stock_id),
    KEY idx_finance_stock_code (stock_code_raw),
    KEY idx_finance_date (report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='股票财务数据贴源表（原始未清洗，不强制外键约束，保留 stock_id 便于下游使用）';