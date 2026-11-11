-- 由 Python 刷新命令在创建会话临时表后执行，仅新增或更新匹配行。
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
FROM tmp_manual_dim_area
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
