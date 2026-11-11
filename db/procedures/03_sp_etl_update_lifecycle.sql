-- ============================================================
-- procedures/03_sp_etl_update_lifecycle.sql
-- 编号：03
-- 来源：原 20_create_procedures_etl.sql 拆分
-- sp_etl_update_lifecycle(IN p_month DATE)
--   按 DWD 销量刷新 dim_oem / dim_model 生命周期
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

DROP PROCEDURE IF EXISTS sp_etl_update_lifecycle;
DELIMITER $$

-- ============================================================
-- 3. sp_etl_update_lifecycle
--    来源：pipelines/dim/build_sales_dimensions.py::update_dim_lifecycle
--    规则：
--      - 基准月 = p_month 非空用 p_month，否则 DWD 中 MAX(stat_month)
--      - 阈值 = 基准月 - 3 个月；last_seen_month >= 阈值 则活跃
--      - 无销量数据的维度记录 is_active = 0
--    注意：dwd.stat_month 是 CHAR(7)，dim 中是 DATE，需统一转 DATE
-- ============================================================
CREATE DEFINER = CURRENT_USER SQL SECURITY INVOKER
PROCEDURE sp_etl_update_lifecycle(IN p_month DATE)
BEGIN
    DECLARE v_base_month DATE DEFAULT NULL;
    DECLARE v_threshold  DATE DEFAULT NULL;
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

    -- 基准月：优先用入参，否则取 DWD 最大月份（CHAR(7) → DATE 月首）
    IF p_month IS NOT NULL THEN
        SET v_base_month = p_month;
    ELSE
        SELECT STR_TO_DATE(CONCAT(MAX(stat_month), '-01'), '%Y-%m-%d')
          INTO v_base_month
          FROM dwd_sales_monthly_model;
    END IF;

    IF v_base_month IS NULL THEN
        -- DWD 无数据：直接返回 0 行，不报错
        SET @etl_affected_rows = 0;
    ELSE
        SET v_threshold = DATE_SUB(v_base_month, INTERVAL 3 MONTH);

        START TRANSACTION;

        -- 3.1 dim_oem：有销量 → 首次/末次月 + 活跃位
        UPDATE dim_oem o
        JOIN (
            SELECT oem_id,
                   MIN(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')) AS first_month,
                   MAX(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')) AS last_month
              FROM dwd_sales_monthly_model
             WHERE oem_id IS NOT NULL
             GROUP BY oem_id
        ) s ON o.id = s.oem_id
        SET o.first_seen_month = s.first_month,
            o.last_seen_month  = s.last_month,
            o.is_active = CASE WHEN s.last_month >= v_threshold THEN 1 ELSE 0 END;
        SET v_rows = v_rows + ROW_COUNT();

        -- 3.2 dim_oem：无销量 → 标记不活跃
        UPDATE dim_oem
        SET is_active = 0
        WHERE id NOT IN (
            SELECT DISTINCT oem_id
              FROM dwd_sales_monthly_model
             WHERE oem_id IS NOT NULL
        );
        SET v_rows = v_rows + ROW_COUNT();

        -- 3.3 dim_model：有销量
        UPDATE dim_model m
        JOIN (
            SELECT model_id,
                   MIN(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')) AS first_month,
                   MAX(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')) AS last_month
              FROM dwd_sales_monthly_model
             WHERE model_id IS NOT NULL
             GROUP BY model_id
        ) s ON m.id = s.model_id
        SET m.first_seen_month = s.first_month,
            m.last_seen_month  = s.last_month,
            m.is_active = CASE WHEN s.last_month >= v_threshold THEN 1 ELSE 0 END;
        SET v_rows = v_rows + ROW_COUNT();

        -- 3.4 dim_model：无销量 → 标记不活跃
        UPDATE dim_model
        SET is_active = 0
        WHERE id NOT IN (
            SELECT DISTINCT model_id
              FROM dwd_sales_monthly_model
             WHERE model_id IS NOT NULL
        );
        SET v_rows = v_rows + ROW_COUNT();

        COMMIT;
        SET @etl_affected_rows = v_rows;
    END IF;
END$$

DELIMITER ;
