-- ============================================================
-- procedures/01_sp_etl_build_sales_dimensions.sql
-- 编号：01
-- 来源：原 20_create_procedures_etl.sql 拆分
-- sp_etl_build_sales_dimensions(IN p_month DATE)
--   ODS 车型 → dim_model 批量 UPSERT
--
-- 规范遵循 plan 3.2.1：
--   - SP 内部自管事务（START TRANSACTION / COMMIT / ROLLBACK）
--   - EXIT HANDLER 捕获 SQLEXCEPTION 后 ROLLBACK 并 RESIGNAL
--   - 影响行数通过用户变量 @etl_affected_rows 回传
--   - 禁止 DDL；CREATE 用 DEFINER=CURRENT_USER SQL SECURITY INVOKER
--   - 锁隔离级别 READ COMMITTED；时区 +08:00
--   - 全表扫描型语句 MAX_EXECUTION_TIME = 300000ms
-- ============================================================

USE dw_auto_stock;

DROP PROCEDURE IF EXISTS sp_etl_build_sales_dimensions;
DELIMITER $$

-- ============================================================
-- 1. sp_etl_build_sales_dimensions
--    来源：pipelines/dim/build_sales_dimensions.py::upsert_models
--    改造：逐行 UPSERT → 集合式 INSERT...SELECT...ON DUPLICATE KEY
--    入参 p_month：仅处理该月（ods.stat_month = p_month）；NULL 全量
-- ============================================================
CREATE DEFINER = CURRENT_USER SQL SECURITY INVOKER
PROCEDURE sp_etl_build_sales_dimensions(IN p_month DATE)
BEGIN
    -- 严格模式 + RC + 时区 + 超时（必须在 DECLARE 之后）
    DECLARE v_missing_count INT DEFAULT 0;
    DECLARE v_missing_list  VARCHAR(2000) DEFAULT '';
    DECLARE v_other_brand_id BIGINT UNSIGNED;
    DECLARE v_rows INT DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SET time_zone = '+08:00';
    SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
    SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION';
    SET SESSION MAX_EXECUTION_TIME = 300000;

    -- 取 OTHER 品牌兜底（无品牌桥接时使用）
    SELECT id INTO v_other_brand_id
      FROM dim_brand
     WHERE brand_code = 'OTHER'
     LIMIT 1;
    IF v_other_brand_id IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'dim_brand 缺少 OTHER 记录，请先执行种子脚本。';
    END IF;

    -- 统计未命中 dim_oem 的主机厂（精确匹配，aliases 暂不实现）
    SELECT COUNT(*),
           LEFT(GROUP_CONCAT(DISTINCT TRIM(src.manufacturer_name_raw) ORDER BY TRIM(src.manufacturer_name_raw) SEPARATOR ', '), 1000)
      INTO v_missing_count, v_missing_list
      FROM (
          SELECT DISTINCT manufacturer_name_raw
            FROM ods_sales_monthly_model
           WHERE model_name_raw IS NOT NULL
             AND TRIM(model_name_raw) <> ''
             AND manufacturer_name_raw IS NOT NULL
             AND TRIM(manufacturer_name_raw) <> ''
             AND (p_month IS NULL OR stat_month = p_month)
      ) src
     WHERE NOT EXISTS (
        SELECT 1 FROM dim_oem o
         WHERE o.oem_name = TRIM(src.manufacturer_name_raw)
     );

    IF v_missing_count > 0 THEN
        SET @etl_missing_oems = v_missing_list;
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '存在未命中 dim_oem 的主机厂，请先修复权威映射（详见 @etl_missing_oems）。';
    END IF;

    START TRANSACTION;

    -- 集合式 UPSERT：一次性把 ODS 车型写入 dim_model
    INSERT INTO dim_model (
        model_code, model_name,
        brand_id, brand_name,
        oem_id, oem_name,
        ads_id, ads_supplier_name,
        energy_type, vehicle_type
    )
    SELECT
        CONCAT('MODEL_', UPPER(LEFT(MD5(CONCAT(TRIM(src.model_name_raw), '|', TRIM(src.manufacturer_name_raw))), 10))) AS model_code,
        TRIM(src.model_name_raw)                                                                                       AS model_name,
        COALESCE(b.brand_id, v_other_brand_id)                                                                         AS brand_id,
        COALESCE(NULLIF(TRIM(b.brand_name_snapshot), ''), 'OTHER')                                                     AS brand_name,
        o.id                                                                                                           AS oem_id,
        TRIM(src.manufacturer_name_raw)                                                                                AS oem_name,
        NULL                                                                                                           AS ads_id,
        NULL                                                                                                           AS ads_supplier_name,
        '占位'                                                                                                         AS energy_type,
        '占位'                                                                                                         AS vehicle_type
    FROM (
        SELECT DISTINCT model_name_raw, manufacturer_name_raw
          FROM ods_sales_monthly_model
         WHERE model_name_raw IS NOT NULL
           AND TRIM(model_name_raw) <> ''
           AND manufacturer_name_raw IS NOT NULL
           AND TRIM(manufacturer_name_raw) <> ''
           AND (p_month IS NULL OR stat_month = p_month)
    ) src
    JOIN dim_oem o
      ON o.oem_name = TRIM(src.manufacturer_name_raw)
    LEFT JOIN bridge_brand_oem b
      ON b.oem_id = o.id
     AND b.is_current = 1
     AND b.effective_end_date = '9999-12-31'
    ON DUPLICATE KEY UPDATE
        brand_id          = VALUES(brand_id),
        brand_name        = VALUES(brand_name),
        oem_id            = VALUES(oem_id),
        oem_name          = VALUES(oem_name),
        ads_id            = VALUES(ads_id),
        ads_supplier_name = VALUES(ads_supplier_name),
        energy_type       = VALUES(energy_type),
        vehicle_type      = VALUES(vehicle_type);

    SET v_rows = ROW_COUNT();
    COMMIT;
    SET @etl_affected_rows = v_rows;
END$$

DELIMITER ;
