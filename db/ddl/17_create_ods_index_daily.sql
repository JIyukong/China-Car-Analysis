-- ============================================================
-- 17_create_ods_index_daily.sql
-- 行业/基准指数日线贴源表 DDL
-- 首期覆盖同花顺行业指数 881125 汽车整车，作为汽车板块基准线
-- 遵循 ODS 设计原则：保留原始自然键快照，不强制外键约束
-- ============================================================

USE dw_auto_stock;

-- ------------------------------------------------------------
-- 1. 指数维度表 dim_index
--    统一管理来自不同编制机构的指数（THS / CSINDEX / WIND 等）
-- ------------------------------------------------------------
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS dim_index;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS dim_index (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    index_code      VARCHAR(32)     NOT NULL                COMMENT '指数代码（如 881125）',
    index_name      VARCHAR(128)    NOT NULL                COMMENT '指数名称（如 汽车整车）',
    index_source    ENUM('THS','CSINDEX','WIND','OTHER') NOT NULL DEFAULT 'THS' COMMENT '指数编制来源',
    market          VARCHAR(16)     DEFAULT NULL            COMMENT '市场标识（CN/HK/US 等）',
    category        VARCHAR(32)     DEFAULT NULL            COMMENT '指数类别（industry/concept/broad_based）',
    description     VARCHAR(255)    DEFAULT NULL            COMMENT '指数描述',
    is_active       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用（1启用/0停用）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_index_code_source (index_code, index_source),
    KEY idx_index_category (category),
    KEY idx_index_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='指数维度表（覆盖同花顺/中证/万得等编制机构的行业与基准指数）';

-- ------------------------------------------------------------
-- 2. 指数日线贴源表 ods_index_daily
--    保留原始快照字段，不强制外键约束，index_id 允许为空
-- ------------------------------------------------------------
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
