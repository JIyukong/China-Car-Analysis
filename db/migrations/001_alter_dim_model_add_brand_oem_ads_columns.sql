USE dw_auto_stock;

-- 为存量 dim_model 表补充名称冗余字段与 ADS 占位字段。
-- 通过 information_schema 判断列是否存在，避免重复执行时报错。

SET @schema_name = DATABASE();

SET @brand_name_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'brand_name'
);
SET @brand_name_sql = IF(
    @brand_name_exists = 0,
    "ALTER TABLE dim_model ADD COLUMN brand_name VARCHAR(128) DEFAULT NULL COMMENT '品牌名称快照' AFTER brand_id",
    "SELECT 'skip brand_name' AS msg"
);
PREPARE stmt FROM @brand_name_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @oem_name_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'oem_name'
);
SET @oem_name_sql = IF(
    @oem_name_exists = 0,
    "ALTER TABLE dim_model ADD COLUMN oem_name VARCHAR(128) DEFAULT NULL COMMENT '主机厂名称快照' AFTER oem_id",
    "SELECT 'skip oem_name' AS msg"
);
PREPARE stmt FROM @oem_name_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ads_id_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'ads_id'
);
SET @ads_id_sql = IF(
    @ads_id_exists = 0,
    "ALTER TABLE dim_model ADD COLUMN ads_id BIGINT UNSIGNED DEFAULT NULL COMMENT 'ADS 当前有效拉链ID（引用 dim_model_ads_zipper.id）' AFTER oem_name",
    "SELECT 'skip add ads_id' AS msg"
);
PREPARE stmt FROM @ads_id_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE dim_model
SET ads_id = NULL
WHERE ads_id = '占位待定';

SET @ads_id_type = (
    SELECT DATA_TYPE
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'ads_id'
);
SET @ads_id_modify_sql = IF(
    @ads_id_type <> 'bigint',
    "ALTER TABLE dim_model MODIFY COLUMN ads_id BIGINT UNSIGNED DEFAULT NULL COMMENT 'ADS 当前有效拉链ID（引用 dim_model_ads_zipper.id）' AFTER oem_name",
    "SELECT 'skip modify ads_id' AS msg"
);
PREPARE stmt FROM @ads_id_modify_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ads_supplier_name_exists = (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema_name
      AND TABLE_NAME = 'dim_model'
      AND COLUMN_NAME = 'ads_supplier_name'
);
SET @ads_supplier_name_sql = IF(
    @ads_supplier_name_exists = 0,
    "ALTER TABLE dim_model ADD COLUMN ads_supplier_name VARCHAR(128) DEFAULT NULL COMMENT 'ADS 当前有效供应商名称（来自 dim_model_ads_zipper.ads_supplier）' AFTER ads_id",
    "SELECT 'skip add ads_supplier_name' AS msg"
);
PREPARE stmt FROM @ads_supplier_name_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE dim_model
SET ads_supplier_name = NULL
WHERE ads_supplier_name = '占位待定';

ALTER TABLE dim_model
    MODIFY COLUMN ads_supplier_name VARCHAR(128) DEFAULT NULL COMMENT 'ADS 当前有效供应商名称（来自 dim_model_ads_zipper.ads_supplier）' AFTER ads_id;
