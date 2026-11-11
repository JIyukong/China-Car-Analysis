-- ============================================================
-- ods/04_create_ods_index_daily.sql
-- 指数日线贴源表 ods_index_daily
-- 来源：原 17_create_ods_index_daily.sql 拆分
-- 首期覆盖同花顺行业指数 881125 汽车整车，作为汽车板块基准线
-- 遵循 ODS 设计原则：保留原始自然键快照，不强制外键约束
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS ods_index_daily;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS ods_index_daily (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    index_id            BIGINT UNSIGNED DEFAULT NULL            COMMENT '指数ID（可选映射，用于下游使用便利）',
    index_code_raw      VARCHAR(32)     NOT NULL                COMMENT '原始指数代码（如 881125）',
    index_name_raw      VARCHAR(128)    DEFAULT NULL            COMMENT '原始指数名称快照',
    index_source_raw    VARCHAR(16)     NOT NULL DEFAULT 'THS'  COMMENT '原始指数编制来源快照',
    trade_date          DATE            NOT NULL                COMMENT '交易日期',

    -- 行情指标
    open                DECIMAL(12,4)   DEFAULT NULL            COMMENT '开盘价',
    high                DECIMAL(12,4)   DEFAULT NULL            COMMENT '最高价',
    low                 DECIMAL(12,4)   DEFAULT NULL            COMMENT '最低价',
    close               DECIMAL(12,4)   DEFAULT NULL            COMMENT '收盘价',
    volume              BIGINT          DEFAULT NULL            COMMENT '成交量（手）',
    amount              DECIMAL(18,2)   DEFAULT NULL            COMMENT '成交额（元）',
    pct_change          DECIMAL(8,4)    DEFAULT NULL            COMMENT '涨跌幅（%）',
    change_amount       DECIMAL(12,4)   DEFAULT NULL            COMMENT '涨跌额',
    turnover_rate       DECIMAL(8,4)    DEFAULT NULL            COMMENT '换手率（%）',

    -- 元数据
    source              ENUM('akshare','other') NOT NULL DEFAULT 'akshare' COMMENT '数据来源',
    fetch_time          DATETIME        NOT NULL                COMMENT '采集时间',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_index_code_date (index_code_raw, trade_date, source),
    KEY idx_ods_index_id (index_id),
    KEY idx_ods_index_code (index_code_raw),
    KEY idx_ods_index_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='指数日线贴源表（原始未清洗，不强制外键约束，保留 index_id 便于下游使用）';
