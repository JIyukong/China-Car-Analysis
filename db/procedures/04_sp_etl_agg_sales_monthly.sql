-- ============================================================
-- procedures/04_sp_etl_agg_sales_monthly.sql
-- 编号：04
-- 来源：原 20_create_procedures_etl.sql 拆分
-- sp_etl_agg_sales_monthly(IN p_month DATE)
--   DWD → DWS 公司/品牌两层月度聚合
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

DROP PROCEDURE IF EXISTS sp_etl_agg_sales_monthly;
DELIMITER $$

-- ============================================================
-- 4. sp_etl_agg_sales_monthly
--    来源：pipelines/serve/agg_sales_monthly.py
--    改造：VALUES(col) → alias.col（MySQL 8.0.19+）
--         p_month 增量；两个 INSERT 同一事务
--    注意：dwd.stat_month 是 CHAR(7)，dws 是 DATE，子查询内统一转 DATE
-- ============================================================
CREATE DEFINER = CURRENT_USER SQL SECURITY INVOKER
PROCEDURE sp_etl_agg_sales_monthly(IN p_month DATE)
BEGIN
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

    START TRANSACTION;

    -- 4.1 按上市公司月度聚合
    INSERT INTO dws_sales_monthly_company (
        stat_month, company_id, company_name,
        total_sales, market_share, yoy_growth, mom_growth
    )
    SELECT *
    FROM (
        SELECT
            curr.stat_month,
            curr.company_id,
            dc.company_name,
            curr.monthly_sales                                                       AS total_sales,
            ROUND(curr.monthly_sales / total.monthly_sales * 100, 4)                 AS market_share,
            CASE WHEN prev.monthly_sales > 0
                 THEN ROUND((curr.monthly_sales - prev.monthly_sales) / prev.monthly_sales * 100, 2)
                 ELSE NULL END                                                       AS yoy_growth,
            CASE WHEN last_month.monthly_sales > 0
                 THEN ROUND((curr.monthly_sales - last_month.monthly_sales) / last_month.monthly_sales * 100, 2)
                 ELSE NULL END                                                       AS mom_growth
        FROM (
            SELECT STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d') AS stat_month,
                   company_id,
                   SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE company_id IS NOT NULL
             GROUP BY STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), company_id
        ) curr
        JOIN dim_company dc ON dc.id = curr.company_id
        CROSS JOIN (
            SELECT STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d') AS stat_month,
                   SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE company_id IS NOT NULL
             GROUP BY STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')
        ) total ON total.stat_month = curr.stat_month
        LEFT JOIN (
            SELECT DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 12 MONTH) AS stat_month,
                   company_id, SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE company_id IS NOT NULL
             GROUP BY DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 12 MONTH), company_id
        ) prev ON prev.stat_month = curr.stat_month AND prev.company_id = curr.company_id
        LEFT JOIN (
            SELECT DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 1 MONTH) AS stat_month,
                   company_id, SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE company_id IS NOT NULL
             GROUP BY DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 1 MONTH), company_id
        ) last_month ON last_month.stat_month = curr.stat_month
                    AND last_month.company_id = curr.company_id
        WHERE (p_month IS NULL OR curr.stat_month = p_month)
    ) AS new
    ON DUPLICATE KEY UPDATE
        company_name = new.company_name,
        total_sales  = new.total_sales,
        market_share = new.market_share,
        yoy_growth   = new.yoy_growth,
        mom_growth   = new.mom_growth;
    SET v_rows = v_rows + ROW_COUNT();

    -- 4.2 按品牌月度聚合
    INSERT INTO dws_sales_monthly_brand (
        stat_month, brand_id, brand_name,
        total_sales, market_share, yoy_growth, mom_growth
    )
    SELECT *
    FROM (
        SELECT
            curr.stat_month,
            curr.brand_id,
            db.brand_name,
            curr.monthly_sales                                                       AS total_sales,
            ROUND(curr.monthly_sales / total.monthly_sales * 100, 4)                 AS market_share,
            CASE WHEN prev.monthly_sales > 0
                 THEN ROUND((curr.monthly_sales - prev.monthly_sales) / prev.monthly_sales * 100, 2)
                 ELSE NULL END                                                       AS yoy_growth,
            CASE WHEN last_month.monthly_sales > 0
                 THEN ROUND((curr.monthly_sales - last_month.monthly_sales) / last_month.monthly_sales * 100, 2)
                 ELSE NULL END                                                       AS mom_growth
        FROM (
            SELECT STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d') AS stat_month,
                   brand_id,
                   SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE brand_id IS NOT NULL
             GROUP BY STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), brand_id
        ) curr
        JOIN dim_brand db ON db.id = curr.brand_id
        CROSS JOIN (
            SELECT STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d') AS stat_month,
                   SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE brand_id IS NOT NULL
             GROUP BY STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d')
        ) total ON total.stat_month = curr.stat_month
        LEFT JOIN (
            SELECT DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 12 MONTH) AS stat_month,
                   brand_id, SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE brand_id IS NOT NULL
             GROUP BY DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 12 MONTH), brand_id
        ) prev ON prev.stat_month = curr.stat_month AND prev.brand_id = curr.brand_id
        LEFT JOIN (
            SELECT DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 1 MONTH) AS stat_month,
                   brand_id, SUM(sales_volume) AS monthly_sales
              FROM dwd_sales_monthly_model
             WHERE brand_id IS NOT NULL
             GROUP BY DATE_SUB(STR_TO_DATE(CONCAT(stat_month, '-01'), '%Y-%m-%d'), INTERVAL 1 MONTH), brand_id
        ) last_month ON last_month.stat_month = curr.stat_month
                    AND last_month.brand_id = curr.brand_id
        WHERE (p_month IS NULL OR curr.stat_month = p_month)
    ) AS new
    ON DUPLICATE KEY UPDATE
        brand_name   = new.brand_name,
        total_sales  = new.total_sales,
        market_share = new.market_share,
        yoy_growth   = new.yoy_growth,
        mom_growth   = new.mom_growth;
    SET v_rows = v_rows + ROW_COUNT();

    COMMIT;
    SET @etl_affected_rows = v_rows;
END$$

DELIMITER ;
