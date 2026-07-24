USE dw_auto_stock;

-- 将 dim_model.ads_id 收敛为对 dim_model_ads_zipper.id 的外键引用。
-- 注意：执行前需要保证 05_create_dim_zipper_tables.sql 已执行。

SET @schema_name = DATABASE();

SET @ads_id_type = (
    SELECT DATA_TYPE
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'ads_id'
);
SET @ads_id_relax_sql = IF(
    @ads_id_type <> 'bigint',
    "ALTER TABLE dim_model MODIFY COLUMN ads_id VARCHAR(64) NULL COMMENT 'ADS 当前有效拉链ID（引用 dim_model_ads_zipper.id）'",
    "SELECT 'skip relax ads_id' AS msg"
);
PREPARE stmt FROM @ads_id_relax_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

ALTER TABLE dim_model MODIFY COLUMN ads_supplier_name VARCHAR(128) NULL COMMENT 'ADS 当前有效供应商名称（来自 dim_model_ads_zipper.ads_supplier）';

UPDATE dim_model
SET ads_id = NULL
WHERE ads_id = '占位待定';

UPDATE dim_model
SET ads_supplier_name = NULL
WHERE ads_supplier_name = '占位待定';

ALTER TABLE dim_model MODIFY COLUMN ads_id BIGINT UNSIGNED NULL COMMENT 'ADS 当前有效拉链ID（引用 dim_model_ads_zipper.id）';

SET @ads_idx_exists = (
    SELECT COUNT(*)
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND INDEX_NAME = 'idx_model_ads_id'
);
SET @ads_idx_sql = IF(
    @ads_idx_exists = 0,
    "ALTER TABLE dim_model ADD KEY idx_model_ads_id (ads_id)",
    "SELECT 'skip idx_model_ads_id' AS msg"
);
PREPARE stmt FROM @ads_idx_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @fk_exists = (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
      AND CONSTRAINT_NAME = 'fk_model_ads_zipper'
);
SET @fk_sql = IF(
    @fk_exists = 0,
    "ALTER TABLE dim_model ADD CONSTRAINT fk_model_ads_zipper FOREIGN KEY (ads_id) REFERENCES dim_model_ads_zipper(id) ON DELETE SET NULL ON UPDATE CASCADE",
    "SELECT 'skip fk_model_ads_zipper' AS msg"
);
PREPARE stmt FROM @fk_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
