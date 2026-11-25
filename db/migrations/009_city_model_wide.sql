-- 管理员审阅后单独部署。ETL 不执行本文件；无历史回填、无旧表修改。
-- 部署后保持 ETL_CITY_WIDE_ENABLED=false，完成独立测试及数据处理决策再启用。
CREATE TABLE IF NOT EXISTS dim_city_model (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source VARCHAR(32) NOT NULL DEFAULT '16888_city',
    source_model_name VARCHAR(128) NOT NULL COMMENT '原始车型名仅TRIM；保留进口等身份标记',
    model_code VARCHAR(64) NOT NULL COMMENT '城市独立编码，不复用全国车型ID',
    model_name VARCHAR(128) NOT NULL,
    brand_code VARCHAR(64) DEFAULT NULL,
    brand_name VARCHAR(128) DEFAULT NULL,
    oem_code VARCHAR(64) DEFAULT NULL,
    oem_name VARCHAR(128) DEFAULT NULL,
    production_type ENUM('domestic','imported','unknown') NOT NULL DEFAULT 'unknown',
    import_channel ENUM('official','parallel','unknown') NOT NULL DEFAULT 'unknown',
    energy_type VARCHAR(16) DEFAULT NULL,
    vehicle_type VARCHAR(16) DEFAULT NULL,
    review_status ENUM('pending','confirmed','conflict') NOT NULL DEFAULT 'pending',
    evidence VARCHAR(1000) DEFAULT NULL COMMENT '人工确认依据；不含上市集团归属',
    national_candidates JSON DEFAULT NULL COMMENT '全国ODS候选，未经审核不能作为分组归属',
    first_seen_month DATE DEFAULT NULL COMMENT '已发布城市明细的首次出现月份',
    last_seen_month DATE DEFAULT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_city_model_source (source, source_model_name),
    UNIQUE KEY uk_city_model_code (model_code),
    KEY idx_city_model_brand (brand_code),
    KEY idx_city_model_oem (oem_code),
    KEY idx_city_model_review (review_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
COMMENT='城市专用车型品牌主机厂宽维表；一行一个来源车型身份';

CREATE TABLE IF NOT EXISTS dwd_sales_monthly_city (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    stat_month CHAR(7) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT '16888_city',
    model_name VARCHAR(128) NOT NULL,
    model_name_raw VARCHAR(128) NOT NULL,
    city_model_id BIGINT UNSIGNED NOT NULL,
    city_name VARCHAR(64) NOT NULL,
    city_name_raw VARCHAR(64) NOT NULL,
    area_id BIGINT UNSIGNED NOT NULL,
    subtype_name VARCHAR(32) DEFAULT NULL,
    level_raw VARCHAR(64) DEFAULT NULL,
    vehicle_subtype_id BIGINT UNSIGNED NOT NULL,
    vehicle_type_id BIGINT UNSIGNED NOT NULL,
    sales_volume INT UNSIGNED NOT NULL,
    price_min DECIMAL(10,2) DEFAULT NULL,
    price_max DECIMAL(10,2) DEFAULT NULL,
    rank_no INT UNSIGNED DEFAULT NULL,
    row_hash CHAR(32) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_city_sales_hash (row_hash),
    UNIQUE KEY uk_city_sales_identity (source, stat_month, city_model_id, area_id),
    KEY idx_city_sales_area (area_id),
    CONSTRAINT fk_city_sales_model FOREIGN KEY (city_model_id) REFERENCES dim_city_model(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
COMMENT='独立城市销量明细，不存全国维度ID或上市集团';

-- 未确认的归属始终显式展示，避免报表按NULL分组或过滤时悄悄漏掉销量。
CREATE OR REPLACE VIEW vw_sales_monthly_city_wide AS
SELECT d.*, m.model_code,
    CASE WHEN m.review_status='confirmed' THEN m.brand_code ELSE 'PENDING' END AS brand_code,
    CASE WHEN m.review_status='confirmed' THEN m.brand_name ELSE '归属待确认' END AS brand_name,
    CASE WHEN m.review_status='confirmed' THEN m.oem_code ELSE 'PENDING' END AS oem_code,
    CASE WHEN m.review_status='confirmed' THEN m.oem_name ELSE '归属待确认' END AS oem_name,
    m.production_type, m.import_channel, m.energy_type, m.vehicle_type, m.review_status
FROM dwd_sales_monthly_city d JOIN dim_city_model m ON m.id=d.city_model_id;
