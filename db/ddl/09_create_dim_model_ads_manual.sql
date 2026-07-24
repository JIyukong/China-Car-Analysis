USE dw_auto_stock;

-- ADS 手工权威存储表：
-- 1. dim_model_ads_manual 保留所有手工维护车型，包括 dim_model 尚不存在的车型。
-- 2. stg_dim_model_ads_manual 作为 CSV 装载暂存区。

CREATE TABLE IF NOT EXISTS dim_model_ads_manual (
    id                         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    model_name                 VARCHAR(128)    NOT NULL                COMMENT '车型名称（手工维护业务键）',
    ads_proportion_raw         VARCHAR(16)     NOT NULL                COMMENT 'ADS 搭载占比原文（如 95%）',
    ads_proportion_pct         DECIMAL(5,2)    DEFAULT NULL            COMMENT 'ADS 搭载占比标准化数值（百分比）',
    ads_supplier               VARCHAR(64)     NOT NULL                COMMENT 'ADS 供应商（如 华为ADS）',
    ads_supplier_mode          VARCHAR(64)     NOT NULL                COMMENT 'ADS 供应商模式（如 鸿蒙智行/HI模式）',
    listed_at_raw              VARCHAR(32)     NOT NULL                COMMENT '上市时间原文（保留 CSV 原始表达）',
    listed_at_date             DATE            DEFAULT NULL            COMMENT '上市时间标准化日期（模糊粒度取区间首日）',
    listed_at_granularity      VARCHAR(16)     NOT NULL DEFAULT 'unknown' COMMENT '上市时间粒度（day/month/quarter/season/unknown）',
    matched_model_id           BIGINT UNSIGNED DEFAULT NULL            COMMENT '命中的 dim_model.id，仅作关联缓存，不建外键',
    is_in_dim_model            TINYINT(1)      NOT NULL DEFAULT 0      COMMENT '是否命中 dim_model：1=命中 0=未命中',
    missing_in_dim_model_reason VARCHAR(64)    DEFAULT NULL            COMMENT 'dim_model 未命中原因（如 DIM_MODEL_NOT_FOUND）',
    match_checked_at           DATETIME        DEFAULT NULL            COMMENT '最近一次匹配检查时间',
    source_file                VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_model_ads_matched.csv' COMMENT '手工维护文件来源',
    created_at                 DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at                 DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dim_model_ads_manual_model_name (model_name),
    KEY idx_ads_manual_is_in_dim_model (is_in_dim_model),
    KEY idx_ads_manual_matched_model_id (matched_model_id),
    KEY idx_ads_manual_supplier_mode (ads_supplier, ads_supplier_mode)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ADS 手工权威存储表';

CREATE TABLE IF NOT EXISTS stg_dim_model_ads_manual (
    model_name          VARCHAR(128)    NOT NULL                COMMENT '车型名称（来自手工 CSV）',
    ads_proportion_raw  VARCHAR(16)     NOT NULL                COMMENT 'ADS 搭载占比原文',
    ads_supplier        VARCHAR(64)     NOT NULL                COMMENT 'ADS 供应商',
    ads_supplier_mode   VARCHAR(64)     NOT NULL                COMMENT 'ADS 供应商模式',
    listed_at_raw       VARCHAR(32)     NOT NULL                COMMENT '上市时间原文',
    source_file         VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_model_ads_matched.csv' COMMENT '手工维护文件来源',
    loaded_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '导入时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ADS 手工权威存储表导入暂存表';
