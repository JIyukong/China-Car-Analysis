-- ============================================================
-- ods/02_create_ods_stock_daily.sql
-- 贴源层：股价日线贴源表
-- 来源：原 01_create_tables.sql 拆分（ods 层）
-- ============================================================

USE dw_auto_stock;

-- 股价日线 贴源表
CREATE TABLE IF NOT EXISTS ods_stock_daily (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stock_id          BIGINT UNSIGNED DEFAULT NULL              COMMENT '股票ID（可选映射，用于下游使用便利）',
    stock_code_raw    VARCHAR(32)     NOT NULL                  COMMENT '原始股票代码（如 002594.SZ / 1211.HK / NIO.US）',
    stock_name_raw    VARCHAR(128)    DEFAULT NULL              COMMENT '原始股票名称快照',
    market_raw        VARCHAR(8)      NOT NULL                  COMMENT '原始市场代码（A/HK/US）',
    exchange_raw      VARCHAR(16)     DEFAULT NULL              COMMENT '原始交易所代码（SZ/SH/HKEX/NYSE/NASDAQ）',
    source_symbol     VARCHAR(32)     DEFAULT NULL              COMMENT '请求源接口时使用的 symbol 快照',
    trade_date        DATE            NOT NULL                  COMMENT '交易日',
    open              DECIMAL(12,4)   DEFAULT NULL              COMMENT '开盘价',
    high              DECIMAL(12,4)   DEFAULT NULL              COMMENT '最高价',
    low               DECIMAL(12,4)   DEFAULT NULL              COMMENT '最低价',
    close             DECIMAL(12,4)   DEFAULT NULL              COMMENT '收盘价',
    volume            BIGINT          DEFAULT NULL              COMMENT '成交量（股）',
    amount            DECIMAL(18,2)   DEFAULT NULL              COMMENT '成交额（元/港元/美元）',
    pct_change        DECIMAL(8,4)    DEFAULT NULL              COMMENT '涨跌幅（%，如 2.35 表示 +2.35%）',
    source            ENUM('akshare','other') NOT NULL DEFAULT 'akshare' COMMENT '数据来源',
    fetch_time        DATETIME        NOT NULL                  COMMENT '采集时间',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_code_date (stock_code_raw, trade_date, source),
    KEY idx_ods_stock_id (stock_id),
    KEY idx_ods_stock_code_date (stock_code_raw, trade_date),
    KEY idx_ods_stock_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股价日线贴源表（原始未清洗，不强制外键约束，保留 stock_id 便于下游使用）';
