USE dw_auto_stock;

-- ============================================================
-- 14_create_dim_area.sql
-- 地区维度表（中国大陆行政区划：大区 → 省级 → 地级）
-- 参照 dim_energy_vehicle 手工维度模式：主表 + 暂存表
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_area (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    city_code           VARCHAR(8)      NOT NULL                COMMENT '城市统一编码（业务主键，GB/T 2260 六位行政区划代码，如 330100）',
    city_name           VARCHAR(64)     NOT NULL                COMMENT '地级行政区全称（如 "宁波市"/"恩施土家族苗族自治州"）',
    city_short_name     VARCHAR(32)     NOT NULL                COMMENT '城市简称（如 "宁波"），用于与 ods 城市名对齐',
    city_level          ENUM('地级市','直辖市','自治州','地区','盟') NOT NULL COMMENT '地级行政区类型',
    province_code       VARCHAR(8)      NOT NULL                COMMENT '省级行政区编码（GB/T 2260 前两位+0000，如 330000）',
    province_name       VARCHAR(32)     NOT NULL                COMMENT '省级行政区全称（如 "浙江省"）',
    province_short_name VARCHAR(16)     NOT NULL                COMMENT '省级简称（如 "浙江"）',
    province_type       ENUM('省','自治区','直辖市') NOT NULL   COMMENT '省级行政区类型',
    region_code         VARCHAR(8)      NOT NULL                COMMENT '大区编码（EC/SC/NC/CC/SW/NW/NE）',
    region_name         ENUM('华东','华南','华北','华中','西南','西北','东北') NOT NULL COMMENT '大区名称',
    source_file         VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_area.csv' COMMENT '手工维护文件来源',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dim_area_city_code (city_code),
    UNIQUE KEY uk_dim_area_city_short_name (city_short_name),
    KEY idx_dim_area_province_code (province_code),
    KEY idx_dim_area_region_code (region_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='地区维度表（大区/省/地级市三级）';

CREATE TABLE IF NOT EXISTS stg_dim_area (
    city_code           VARCHAR(8)      NOT NULL                COMMENT '城市统一编码原始值',
    city_name           VARCHAR(64)     NOT NULL                COMMENT '地级行政区全称原始值',
    city_short_name     VARCHAR(32)     NOT NULL                COMMENT '城市简称原始值',
    city_level          VARCHAR(16)     NOT NULL                COMMENT '地级行政区类型原始值',
    province_code       VARCHAR(8)      NOT NULL                COMMENT '省级行政区编码原始值',
    province_name       VARCHAR(32)     NOT NULL                COMMENT '省级行政区全称原始值',
    province_short_name VARCHAR(16)     NOT NULL                COMMENT '省级简称原始值',
    province_type       VARCHAR(16)     NOT NULL                COMMENT '省级行政区类型原始值',
    region_code         VARCHAR(8)      NOT NULL                COMMENT '大区编码原始值',
    region_name         VARCHAR(16)     NOT NULL                COMMENT '大区名称原始值',
    source_file         VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_area.csv' COMMENT '手工维护文件来源',
    loaded_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '导入时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='地区维度导入暂存表';
