USE dw_auto_stock;

-- 使用说明：
-- 1. 先将 data/manual_dimensions/dim_area.csv 导入 stg_dim_area。
-- 2. 再执行本 SQL，完成主存储表 UPSERT 与孤立行清理。

-- 非法必填项检查：若有结果，请先修正 CSV 再继续。
SELECT city_code, city_name, province_code, region_code
FROM stg_dim_area
WHERE city_code IS NULL
   OR TRIM(city_code) = ''
   OR city_name IS NULL
   OR TRIM(city_name) = ''
   OR province_code IS NULL
   OR TRIM(province_code) = ''
   OR region_code IS NULL
   OR TRIM(region_code) = '';

INSERT INTO dim_area (
    city_code,
    city_name,
    city_short_name,
    city_level,
    province_code,
    province_name,
    province_short_name,
    province_type,
    region_code,
    region_name,
    source_file
)
SELECT
    TRIM(city_code)           AS city_code,
    TRIM(city_name)           AS city_name,
    TRIM(city_short_name)     AS city_short_name,
    TRIM(city_level)          AS city_level,
    TRIM(province_code)       AS province_code,
    TRIM(province_name)       AS province_name,
    TRIM(province_short_name) AS province_short_name,
    TRIM(province_type)       AS province_type,
    TRIM(region_code)         AS region_code,
    TRIM(region_name)         AS region_name,
    COALESCE(NULLIF(TRIM(source_file), ''), 'data/manual_dimensions/dim_area.csv') AS source_file
FROM stg_dim_area
WHERE city_code IS NOT NULL
  AND TRIM(city_code) <> ''
ON DUPLICATE KEY UPDATE
    city_name           = VALUES(city_name),
    city_short_name     = VALUES(city_short_name),
    city_level          = VALUES(city_level),
    province_code       = VALUES(province_code),
    province_name       = VALUES(province_name),
    province_short_name = VALUES(province_short_name),
    province_type       = VALUES(province_type),
    region_code         = VALUES(region_code),
    region_name         = VALUES(region_name),
    source_file         = VALUES(source_file);

-- 清理 stg 中不再存在的行，保证 CSV 为权威快照
DELETE area
FROM dim_area area
LEFT JOIN (
    SELECT DISTINCT TRIM(city_code) AS city_code
    FROM stg_dim_area
    WHERE city_code IS NOT NULL
      AND TRIM(city_code) <> ''
) stg
    ON stg.city_code = area.city_code
WHERE stg.city_code IS NULL;

TRUNCATE TABLE stg_dim_area;
