-- ============================================================
-- 03_create_dwd_tables.sql
-- DWD 明细层建表脚本
-- 清洗后的明细数据，关联维度ID，供 DWS/ADS 聚合使用
-- ============================================================

USE dw_auto_stock;

-- ============================================================
-- DWD 明细层
-- ============================================================

-- 1. 销量明细表（清洗后，关联维度ID）
CREATE TABLE IF NOT EXISTS dwd_sales_monthly_model (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stat_month        DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    source            VARCHAR(32)     NOT NULL DEFAULT '16888_style' COMMENT '数据来源',
    model_name        VARCHAR(128)    NOT NULL                COMMENT '标准化车型名（已TRIM）',
    model_name_raw    VARCHAR(128)    NOT NULL                COMMENT '原始车型名（保留溯源）',
    model_id          BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联车型维度ID（外键→dim_model.id）',
    manufacturer_name VARCHAR(128)    NOT NULL                COMMENT '标准化主机厂名（已TRIM）',
    oem_id            BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联主机厂维度ID（外键→dim_oem.id）',
    brand_id          BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联品牌维度ID（外键→dim_brand.id）',
    company_id        BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联上市公司维度ID（外键→dim_company.id）',
    sales_volume      INT UNSIGNED    NOT NULL                COMMENT '月销量（辆），已排除NULL和<=0',
    price_min         DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间下限（万元）',
    price_max         DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间上限（万元）',
    rank_no           INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    row_hash          CHAR(32)        NOT NULL                COMMENT 'ODS幂等哈希（用于去重）',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dwd_sales_hash (row_hash),
    KEY idx_dwd_sales_month (stat_month),
    KEY idx_dwd_sales_model (model_id),
    KEY idx_dwd_sales_oem (oem_id),
    KEY idx_dwd_sales_company (company_id),
    KEY idx_dwd_sales_brand (brand_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='销量明细表（DWD层，清洗后关联维度ID）';
