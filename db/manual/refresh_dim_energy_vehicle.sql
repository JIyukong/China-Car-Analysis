USE dw_auto_stock;

-- 使用说明：
-- 1. 先用数据库客户端将 data/manual_dimensions/dim_energy_vehicle.csv
--    导入 stg_dim_energy_vehicle 表。
-- 2. 再执行本 SQL，完成主表刷新与 dim_model 自动回填。

-- 非法枚举值检查：若有结果，请先修正 CSV 再继续。
SELECT model_name, energy_type, vehicle_type
FROM stg_dim_energy_vehicle
WHERE TRIM(energy_type) NOT IN ('燃油', '新能源', '其他', '占位')
   OR TRIM(vehicle_type) NOT IN ('轿车', 'SUV', 'MPV', '其他', '占位');

INSERT INTO dim_energy_vehicle (
    model_name,
    energy_type,
    vehicle_type,
    source_file
)
SELECT
    TRIM(model_name) AS model_name,
    TRIM(energy_type) AS energy_type,
    TRIM(vehicle_type) AS vehicle_type,
    COALESCE(NULLIF(TRIM(source_file), ''), 'data/manual_dimensions/dim_energy_vehicle.csv') AS source_file
FROM stg_dim_energy_vehicle
WHERE model_name IS NOT NULL
  AND TRIM(model_name) <> ''
ON DUPLICATE KEY UPDATE
    energy_type = VALUES(energy_type),
    vehicle_type = VALUES(vehicle_type),
    source_file = VALUES(source_file);

DELETE dev
FROM dim_energy_vehicle dev
LEFT JOIN (
    SELECT DISTINCT TRIM(model_name) AS model_name
    FROM stg_dim_energy_vehicle
    WHERE model_name IS NOT NULL
      AND TRIM(model_name) <> ''
) stg
    ON stg.model_name = dev.model_name
WHERE stg.model_name IS NULL;

TRUNCATE TABLE stg_dim_energy_vehicle;
