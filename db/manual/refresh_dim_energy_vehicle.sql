-- 由 Python 刷新命令在创建会话临时表后执行，仅新增或更新匹配行。
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
FROM tmp_manual_dim_energy_vehicle
WHERE model_name IS NOT NULL
  AND TRIM(model_name) <> ''
ON DUPLICATE KEY UPDATE
    energy_type = VALUES(energy_type),
    vehicle_type = VALUES(vehicle_type),
    source_file = VALUES(source_file);
