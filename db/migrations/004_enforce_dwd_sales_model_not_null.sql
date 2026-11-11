-- ============================================================
-- migrations/004_enforce_dwd_sales_model_not_null.sql
-- 对已上线 dwd_sales_monthly_model 强制 model_id / oem_id / brand_id /
-- company_id NOT NULL，并添加外键约束。
--
-- 背景：
--   旧版 sp_etl_clean_sales_dwd 对 dim_model 使用 LEFT JOIN，当 ODS 车型未
--   命中维表时会以 model_id=NULL 落库。本迁移：
--     1. 先尝试通过 dim_model 回填历史 NULL 数据；
--     2. 审查仍无法回填的行，先补齐映射，保留原始记录；
--     3. 将相关列 MODIFY 为 NOT NULL；
--     4. 添加外键约束。
--
-- 管理员分段执行：先回填，检查第二区段返回零行后，再执行结构变更。
-- 使用严格 SQL 模式；执行前先确认有可恢复备份。此脚本不属于 ETL。
-- ============================================================

USE dw_auto_stock;

-- ------------------------------------------------------------
-- 一、回填历史 NULL 数据：通过 model_name + manufacturer_name 关联维表
-- ------------------------------------------------------------

-- 1.1 回填 oem_id
UPDATE dwd_sales_monthly_model d
    JOIN dim_oem doem
      ON doem.oem_name = d.manufacturer_name
SET d.oem_id = doem.id
WHERE d.oem_id IS NULL;

-- 1.2 回填 model_id / brand_id / company_id
UPDATE dwd_sales_monthly_model d
    JOIN dim_model dm
      ON dm.model_name = d.model_name
     AND dm.oem_id     = d.oem_id
    JOIN dim_oem doem
      ON doem.id = d.oem_id
SET d.model_id   = dm.id,
    d.brand_id   = dm.brand_id,
    d.company_id = doem.company_id
WHERE d.model_id IS NULL;

-- ------------------------------------------------------------
-- 二、查找仍无法回填的记录。若有结果，在此停止并补齐映射。
-- ------------------------------------------------------------
SELECT * FROM dwd_sales_monthly_model
 WHERE model_id IS NULL
    OR oem_id   IS NULL
    OR brand_id IS NULL
    OR company_id IS NULL
    OR model_name IS NULL
    OR TRIM(model_name) = ''
    OR manufacturer_name IS NULL
    OR TRIM(manufacturer_name) = '';

-- ------------------------------------------------------------
-- 三、将列修改为 NOT NULL
-- ------------------------------------------------------------
ALTER TABLE dwd_sales_monthly_model
    MODIFY COLUMN model_id   BIGINT UNSIGNED NOT NULL COMMENT '关联车型维度ID（外键→dim_model.id，非空）',
    MODIFY COLUMN oem_id     BIGINT UNSIGNED NOT NULL COMMENT '关联主机厂维度ID（外键→dim_oem.id，非空）',
    MODIFY COLUMN brand_id   BIGINT UNSIGNED NOT NULL COMMENT '关联品牌维度ID（外键→dim_brand.id，非空）',
    MODIFY COLUMN company_id BIGINT UNSIGNED NOT NULL COMMENT '关联上市公司维度ID（外键→dim_company.id，非空）';

-- ------------------------------------------------------------
-- 四、添加外键约束（幂等：先删除再添加，避免重复键名报错）
-- ------------------------------------------------------------
SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dwd_sales_monthly_model'
       AND CONSTRAINT_NAME = 'fk_dwd_sales_model'
);
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE dwd_sales_monthly_model DROP FOREIGN KEY fk_dwd_sales_model',
    'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dwd_sales_monthly_model'
       AND CONSTRAINT_NAME = 'fk_dwd_sales_oem'
);
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE dwd_sales_monthly_model DROP FOREIGN KEY fk_dwd_sales_oem',
    'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dwd_sales_monthly_model'
       AND CONSTRAINT_NAME = 'fk_dwd_sales_brand'
);
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE dwd_sales_monthly_model DROP FOREIGN KEY fk_dwd_sales_brand',
    'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
     WHERE CONSTRAINT_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dwd_sales_monthly_model'
       AND CONSTRAINT_NAME = 'fk_dwd_sales_company'
);
SET @sql = IF(@fk_exists > 0,
    'ALTER TABLE dwd_sales_monthly_model DROP FOREIGN KEY fk_dwd_sales_company',
    'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

ALTER TABLE dwd_sales_monthly_model
    ADD CONSTRAINT fk_dwd_sales_model   FOREIGN KEY (model_id)   REFERENCES dim_model(id)   ON DELETE RESTRICT ON UPDATE CASCADE,
    ADD CONSTRAINT fk_dwd_sales_oem     FOREIGN KEY (oem_id)     REFERENCES dim_oem(id)     ON DELETE RESTRICT ON UPDATE CASCADE,
    ADD CONSTRAINT fk_dwd_sales_brand   FOREIGN KEY (brand_id)   REFERENCES dim_brand(id)   ON DELETE RESTRICT ON UPDATE CASCADE,
    ADD CONSTRAINT fk_dwd_sales_company FOREIGN KEY (company_id) REFERENCES dim_company(id) ON DELETE RESTRICT ON UPDATE CASCADE;

-- ------------------------------------------------------------
-- 五、验证 SQL（人工执行确认）
-- ------------------------------------------------------------
-- 期望以下查询全部返回 0：
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE model_id IS NULL;
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE oem_id IS NULL;
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE brand_id IS NULL;
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE company_id IS NULL;
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE model_name IS NULL OR TRIM(model_name) = '';
-- SHOW CREATE TABLE dwd_sales_monthly_model;
