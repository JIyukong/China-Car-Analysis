-- ============================================================
-- ods/01_create_ods_sales_monthly.sql
-- 贴源层：车型月销量贴源表（16888 style / city）
-- 来源：原 01_create_tables.sql 拆分（ods 层）
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS ods_sales_monthly_model_city;
DROP TABLE IF EXISTS ods_sales_monthly_model;
SET FOREIGN_KEY_CHECKS = 1;

-- 1. 车型月销量 贴源表（16888 style）
CREATE TABLE IF NOT EXISTS ods_sales_monthly_model (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    source                ENUM('16888_style','other') NOT NULL DEFAULT '16888_style' COMMENT '数据来源',
    stat_month            DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    rank_no               INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    model_name_raw        VARCHAR(128)    NOT NULL                COMMENT '原始车型名（未清洗）',
    manufacturer_name_raw VARCHAR(128)    NOT NULL                COMMENT '原始主机厂名（未清洗）',
    price_range_raw       VARCHAR(64)     DEFAULT NULL            COMMENT '原始价格区间（未清洗）',
    sales_volume          INT UNSIGNED    DEFAULT NULL            COMMENT '月销量（辆）',
    source_file           VARCHAR(255)    DEFAULT NULL            COMMENT '来源文件路径或文件名',
    source_url            VARCHAR(255)    DEFAULT NULL            COMMENT '来源页面 URL',
    row_hash              CHAR(32)        DEFAULT NULL            COMMENT '行级哈希（用于幂等校验）',
    fetch_time            DATETIME        NOT NULL                COMMENT '采集/入库时间',
    created_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_style_month_model_oem (source, stat_month, model_name_raw, manufacturer_name_raw),
    UNIQUE KEY uk_style_row_hash (row_hash),
    KEY idx_ods_style_month (stat_month),
    KEY idx_ods_style_model (model_name_raw),
    KEY idx_ods_style_manufacturer (manufacturer_name_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型月销量贴源表（16888 style 原始未清洗）';

-- 2. 车型城市月销量 贴源表（16888 city）
CREATE TABLE IF NOT EXISTS ods_sales_monthly_model_city (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    source          ENUM('16888_city','other') NOT NULL DEFAULT '16888_city' COMMENT '数据来源',
    stat_month      DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    rank_no         INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    model_name_raw  VARCHAR(128)    NOT NULL                COMMENT '原始车型名（未清洗）',
    city_name_raw   VARCHAR(64)     NOT NULL                COMMENT '原始城市名（未清洗）',
    level_raw       VARCHAR(64)     DEFAULT NULL            COMMENT '原始车型级别（未清洗）',
    price_range_raw VARCHAR(64)     DEFAULT NULL            COMMENT '原始价格区间（未清洗）',
    sales_volume    INT UNSIGNED    DEFAULT NULL            COMMENT '城市月销量（辆）',
    source_file     VARCHAR(255)    DEFAULT NULL            COMMENT '来源文件路径或文件名',
    source_url      VARCHAR(255)    DEFAULT NULL            COMMENT '来源页面 URL',
    row_hash        CHAR(32)        DEFAULT NULL            COMMENT '行级哈希（用于幂等校验）',
    fetch_time      DATETIME        NOT NULL                COMMENT '采集/入库时间',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_city_month_model_city (source, stat_month, model_name_raw, city_name_raw),
    UNIQUE KEY uk_city_row_hash (row_hash),
    KEY idx_ods_city_month (stat_month),
    KEY idx_ods_city_model (model_name_raw),
    KEY idx_ods_city_city (city_name_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型城市月销量贴源表（16888 city 原始未清洗）';
