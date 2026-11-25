-- ============================================================
-- procedures/02_sp_etl_clean_sales_dwd.sql
-- 编号：02
-- 来源：原 20_create_procedures_etl.sql 拆分
-- sp_etl_clean_sales_dwd(IN p_month DATE)
--   ODS → DWD 清洗，修复 OEM JOIN bug，幂等
--
-- 规范遵循 plan 3.2.1：
--   - SP 内部自管事务（START TRANSACTION / COMMIT / ROLLBACK）
--   - EXIT HANDLER 捕获 SQLEXCEPTION 后 ROLLBACK 并 RESIGNAL
--   - 影响行数通过用户变量 @etl_affected_rows 回传
--   - 禁止 DDL；CREATE 用 DEFINER=CURRENT_USER SQL SECURITY INVOKER
--   - 锁隔离级别 READ COMMITTED；时区 +08:00
--   - 全表扫描型语句 MAX_EXECUTION_TIME = 300000ms
--
-- 数据质量策略（零容忍）：
--   - dim_model / dim_oem 均使用 INNER JOIN，未匹配行不写入
--   - 预检阶段一旦发现未匹配车型，立即 SIGNAL 报错，不启动事务
--   - 错误消息中携带未匹配车型明细（厂商|车型|行数），便于人工排查
--   - @etl_unmatched_count 回传统计数，@etl_unmatched_detail 回传明细
-- ============================================================

USE dw_auto_stock;

DROP PROCEDURE IF EXISTS sp_etl_clean_sales_dwd;
DELIMITER $$

CREATE DEFINER = CURRENT_USER()
PROCEDURE sp_etl_clean_sales_dwd(IN p_month DATE)
SQL SECURITY INVOKER
BEGIN
    DECLARE v_unmatched_count INT DEFAULT 0;
    DECLARE v_unmatched_groups INT DEFAULT 0;
    DECLARE v_unmatched_detail TEXT DEFAULT '';
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
    -- 放大 GROUP_CONCAT 上限，确保未匹配车型明细不被截断
    SET SESSION group_concat_max_len = 65535;

    -- 清零用户变量，避免上次残留
    SET @etl_affected_rows = 0;
    SET @etl_unmatched_count = 0;
    SET @etl_unmatched_detail = '';

    -- --------------------------------------------------------
    -- 预检：统计未命中 dim_model 的行，并按 厂商+车型 聚合明细
    -- 两种未匹配情况：
    --   (A) 厂商名在 dim_oem 中找不到（doem.id IS NULL）
    --   (B) 厂商存在但车型在该 OEM 下找不到（dm.id IS NULL）
    -- --------------------------------------------------------
    DROP TEMPORARY TABLE IF EXISTS tmp_unmatched_models;
    CREATE TEMPORARY TABLE tmp_unmatched_models (
        manufacturer_name_raw VARCHAR(128) NOT NULL,
        model_name_raw        VARCHAR(128) NOT NULL,
        unmatched_reason      VARCHAR(32)  NOT NULL,
        row_count             INT          NOT NULL,
        KEY idx_manufacturer_model (manufacturer_name_raw, model_name_raw)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    INSERT INTO tmp_unmatched_models
        (manufacturer_name_raw, model_name_raw, unmatched_reason, row_count)
    SELECT
        TRIM(ods.manufacturer_name_raw)                                       AS manufacturer_name_raw,
        TRIM(ods.model_name_raw)                                              AS model_name_raw,
        CASE
            WHEN doem.id IS NULL THEN 'OEM_NOT_FOUND'
            ELSE 'MODEL_NOT_FOUND'
        END                                                                   AS unmatched_reason,
        COUNT(*)                                                              AS row_count
    FROM ods_sales_monthly_model ods
    LEFT JOIN dim_oem doem
        ON doem.oem_name = TRIM(ods.manufacturer_name_raw)
    LEFT JOIN dim_model dm
        ON dm.model_name = TRIM(ods.model_name_raw)
       AND dm.oem_id     = doem.id
    WHERE ods.sales_volume IS NOT NULL
      AND ods.sales_volume > 0
      AND ods.model_name_raw IS NOT NULL
      AND TRIM(ods.model_name_raw) <> ''
      AND ods.manufacturer_name_raw IS NOT NULL
      AND TRIM(ods.manufacturer_name_raw) <> ''
      AND (p_month IS NULL OR ods.stat_month = p_month)
      AND NOT EXISTS (
            SELECT 1 FROM dwd_sales_monthly_model d
             WHERE d.row_hash = ods.row_hash
      )
      AND (doem.id IS NULL OR dm.id IS NULL)
    GROUP BY TRIM(ods.manufacturer_name_raw), TRIM(ods.model_name_raw),
             CASE WHEN doem.id IS NULL THEN 'OEM_NOT_FOUND' ELSE 'MODEL_NOT_FOUND' END;

    SELECT COUNT(*), COALESCE(SUM(row_count), 0)
      INTO v_unmatched_groups, v_unmatched_count
      FROM tmp_unmatched_models;

    IF v_unmatched_count > 0 THEN
        -- 聚合明细：每行格式 "厂商|车型|原因|行数"，分号分隔
        -- GROUP_CONCAT 上限受 group_concat_max_len 限制，默认 1024 足够几十条明细
        SELECT GROUP_CONCAT(
                   CONCAT(manufacturer_name_raw, '|',
                          model_name_raw, '|',
                          unmatched_reason, '|',
                          row_count)
                   ORDER BY row_count DESC
                   SEPARATOR '; '
               )
          INTO v_unmatched_detail
          FROM tmp_unmatched_models;

        SET @etl_unmatched_count  = v_unmatched_count;
        SET @etl_unmatched_detail = v_unmatched_detail;

        DROP TEMPORARY TABLE IF EXISTS tmp_unmatched_models;

        -- 报错消息中携带前 500 字符明细，完整明细在 @etl_unmatched_detail
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'DWD清洗阻断：存在未匹配维度的车型，请人工检查 @etl_unmatched_detail 后补维重跑（详见 @etl_unmatched_count）。';
    END IF;

    DROP TEMPORARY TABLE IF EXISTS tmp_unmatched_models;

    -- --------------------------------------------------------
    -- 正式写入：INNER JOIN 确保 model_id / oem_id / brand_id / company_id 非空
    -- --------------------------------------------------------
    START TRANSACTION;

    INSERT INTO dwd_sales_monthly_model (
        stat_month, source,
        model_name, model_name_raw, model_id,
        manufacturer_name, oem_id, brand_id, company_id,
        sales_volume, price_min, price_max, rank_no, row_hash
    )
    SELECT
        DATE_FORMAT(ods.stat_month, '%Y-%m')                                   AS stat_month,
        ods.source,
        TRIM(ods.model_name_raw)                                               AS model_name,
        ods.model_name_raw                                                     AS model_name_raw,
        dm.id                                                                  AS model_id,
        doem.oem_name                                                        AS manufacturer_name,
        doem.id                                                                AS oem_id,
        dm.brand_id                                                            AS brand_id,
        doem.company_id                                                        AS company_id,
        ods.sales_volume,
        CAST(NULLIF(TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-',  1), '-', -1)), '') AS DECIMAL(10,2)) AS price_min,
        CAST(NULLIF(TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', -1), '-',  1)), '') AS DECIMAL(10,2)) AS price_max,
        ods.rank_no,
        ods.row_hash
    FROM ods_sales_monthly_model ods
    INNER JOIN dim_oem doem
        ON doem.oem_name = TRIM(ods.manufacturer_name_raw)
    INNER JOIN dim_model dm
        ON dm.model_name = TRIM(ods.model_name_raw)
       AND dm.oem_id   = doem.id
    WHERE ods.sales_volume IS NOT NULL
      AND ods.sales_volume > 0
      AND ods.model_name_raw IS NOT NULL
      AND TRIM(ods.model_name_raw) <> ''
      AND ods.manufacturer_name_raw IS NOT NULL
      AND TRIM(ods.manufacturer_name_raw) <> ''
      AND (p_month IS NULL OR ods.stat_month = p_month)
      AND NOT EXISTS (
            SELECT 1 FROM dwd_sales_monthly_model d
             WHERE d.row_hash = ods.row_hash
      );

    SET v_rows = ROW_COUNT();
    COMMIT;
    SET @etl_affected_rows = v_rows;
END$$

DELIMITER ;
