-- ============================================================
-- 04_create_dws_tables.sql
-- DWS 汇总层建表脚本
-- 按上市公司/品牌/车型多粒度月度聚合，含同比、环比、市占率
-- ============================================================

USE dw_auto_stock;

-- ============================================================
-- DWS 汇总层
-- ============================================================

-- 1. 上市公司月度销量汇总表
CREATE TABLE IF NOT EXISTS dws_sales_monthly_company (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stat_month      DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    company_id      BIGINT UNSIGNED NOT NULL                COMMENT '上市公司ID（外键→dim_company.id）',
    company_name    VARCHAR(64)     NOT NULL                COMMENT '上市公司名称快照',
    total_sales     INT UNSIGNED    NOT NULL                COMMENT '月度总销量（辆）',
    market_share    DECIMAL(8,4)    DEFAULT NULL            COMMENT '市占率（%）',
    yoy_growth      DECIMAL(8,2)    DEFAULT NULL            COMMENT '同比增长率（%）',
    mom_growth      DECIMAL(8,2)    DEFAULT NULL            COMMENT '环比增长率（%）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dws_company_month (stat_month, company_id),
    KEY idx_dws_company_month (stat_month),
    KEY idx_dws_company_id (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上市公司月度销量汇总表（DWS层）';

-- 2. 品牌月度销量汇总表
CREATE TABLE IF NOT EXISTS dws_sales_monthly_brand (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stat_month      DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    brand_id        BIGINT UNSIGNED NOT NULL                COMMENT '品牌ID（外键→dim_brand.id）',
    brand_name      VARCHAR(128)    NOT NULL                COMMENT '品牌名称快照',
    total_sales     INT UNSIGNED    NOT NULL                COMMENT '月度总销量（辆）',
    market_share    DECIMAL(8,4)    DEFAULT NULL            COMMENT '市占率（%）',
    yoy_growth      DECIMAL(8,2)    DEFAULT NULL            COMMENT '同比增长率（%）',
    mom_growth      DECIMAL(8,2)    DEFAULT NULL            COMMENT '环比增长率（%）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dws_brand_month (stat_month, brand_id),
    KEY idx_dws_brand_month (stat_month),
    KEY idx_dws_brand_id (brand_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='品牌月度销量汇总表（DWS层）';
