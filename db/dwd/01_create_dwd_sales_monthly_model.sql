-- ============================================================
-- 01_create_dwd_sales_monthly_model.sql
-- DWD 明细层：销量明细表（清洗后，关联维度ID）
--
-- 唯一约束：
--   uk_dwd_sales_month_model (stat_month, model_id)
--     业务主键：同一月份同一车型只能有一条记录，从数据库层面防止重复入库
--   uk_dwd_sales_hash (row_hash)
--     技术主键：ODS 幂等哈希，支持 NOT EXISTS 去重和增量重跑
--
-- 外键约束（ON DELETE RESTRICT / ON UPDATE CASCADE）：
--   fk_dwd_sales_model   model_id   → dim_model.id
--   fk_dwd_sales_oem     oem_id     → dim_oem.id
--   fk_dwd_sales_brand   brand_id   → dim_brand.id
--   fk_dwd_sales_company company_id → dim_company.id
-- ============================================================

USE dw_auto_stock;

CREATE TABLE IF NOT EXISTS dwd_sales_monthly_model (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stat_month        CHAR(7)         NOT NULL                COMMENT '统计月份（YYYY-MM 格式，如 2026-07）',
    source            VARCHAR(32)     NOT NULL DEFAULT '16888_style' COMMENT '数据来源',
    model_name        VARCHAR(128)    NOT NULL                COMMENT '标准化车型名（已TRIM，非空）',
    model_name_raw    VARCHAR(128)    NOT NULL                COMMENT '原始车型名（保留溯源，非空）',
    model_id          BIGINT UNSIGNED NOT NULL                COMMENT '关联车型维度ID（外键→dim_model.id，非空）',
    manufacturer_name VARCHAR(128)    NOT NULL                COMMENT '标准化主机厂名（已TRIM，非空）',
    oem_id            BIGINT UNSIGNED NOT NULL                COMMENT '关联主机厂维度ID（外键→dim_oem.id，非空）',
    brand_id          BIGINT UNSIGNED NOT NULL                COMMENT '关联品牌维度ID（外键→dim_brand.id，非空）',
    company_id        BIGINT UNSIGNED NOT NULL                COMMENT '关联上市公司维度ID（外键→dim_company.id，非空）',
    ads_id            BIGINT UNSIGNED DEFAULT NULL            COMMENT '智驾 ADS 拉链ID（冗余自 dim_model.ads_id）',
    ads_supplier_name VARCHAR(128)    DEFAULT NULL            COMMENT '智驾供应商名称快照（冗余自 dim_model.ads_supplier_name）',
    sales_volume      INT UNSIGNED    NOT NULL                COMMENT '月销量（辆），已排除NULL和<=0',
    price_min         DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间下限（万元）',
    price_max         DECIMAL(10,2)   DEFAULT NULL            COMMENT '价格区间上限（万元）',
    rank_no           INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    row_hash          CHAR(32)        NOT NULL                COMMENT 'ODS幂等哈希（用于去重）',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dwd_sales_month_model (stat_month, model_id),
    UNIQUE KEY uk_dwd_sales_hash (row_hash),
    KEY idx_dwd_sales_month (stat_month),
    KEY idx_dwd_sales_model (model_id),
    KEY idx_dwd_sales_oem (oem_id),
    KEY idx_dwd_sales_company (company_id),
    KEY idx_dwd_sales_brand (brand_id),
    KEY idx_dwd_sales_ads (ads_id),
    CONSTRAINT fk_dwd_sales_model   FOREIGN KEY (model_id)   REFERENCES dim_model(id)   ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_dwd_sales_oem     FOREIGN KEY (oem_id)     REFERENCES dim_oem(id)     ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_dwd_sales_brand   FOREIGN KEY (brand_id)   REFERENCES dim_brand(id)   ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_dwd_sales_company FOREIGN KEY (company_id) REFERENCES dim_company(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='销量明细表（DWD层，清洗后关联维度ID）';
