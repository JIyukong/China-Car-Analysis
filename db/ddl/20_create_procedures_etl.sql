-- ============================================================
-- 20_create_procedures_etl.sql
-- 编号：20
-- 用途：把 DIM/DWD/DWS/lifecycle 四层纯集合运算下沉为存储过程，
--       Python 侧仅保留 CALL 薄壳（见 scheduler/jobs/etl_jobs.py）。
-- 与 init_database.py 的关系：
--   由 scripts/init_database.py 在 19 号脚本之后自动执行；
--   split_sql_statements() 已支持 DELIMITER 语法。
-- 目标库：dw_auto_stock
--
-- 包含 4 个 SP：
--   1) sp_etl_build_sales_dimensions(IN p_month DATE)
--      ODS 车型 → dim_model 批量 UPSERT
--   2) sp_etl_clean_sales_dwd(IN p_month DATE)
--      ODS → DWD 清洗，修复 OEM JOIN bug，幂等
--   3) sp_etl_update_lifecycle(IN p_month DATE)
--      按 DWD 销量刷新 dim_oem / dim_model 生命周期
--   4) sp_etl_agg_sales_monthly(IN p_month DATE)
--      DWD → DWS 公司/品牌两层月度聚合
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

DELIMITER $$

-- ============================================================
-- 1. sp_etl_build_sales_dimensions
--    来源：pipelines/dim/build_sales_dimensions.py::upsert_models
--    改造：逐行 UPSERT → 集合式 INSERT...SELECT...ON DUPLICATE KEY
--    入参 p_month：仅处理该月（ods.stat_month = p_month）；NULL 全量
-- ============================================================
DROP PROCEDURE IF EXISTS sp_etl_build_sales_dimensions$$
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


-- ============================================================
-- 2. sp_etl_clean_sales_dwd
--    来源：pipelines/staging/clean_sales_dwd.py
--    修复：
--      a) INNER JOIN dim_oem（替代原 LEFT JOIN dm → doem 链式），
--         company_id 直接来自 doem，避免新车型 company_id 为 NULL
--      b) dim_model 关联带 oem_id，防止同名车型跨 OEM 膨胀
--      c) NOT IN → NOT EXISTS
--      d) 未匹配 dim_model 行数 > 100 时 SIGNAL
--    注意：dwd.stat_month 在 15 号脚本已改为 CHAR(7)
-- ============================================================
DROP PROCEDURE IF EXISTS sp_etl_clean_sales_dwd$$
CREATE DEFINER = CURRENT_USER SQL SECURITY INVOKER
PROCEDURE sp_etl_clean_sales_dwd(IN p_month DATE)
BEGIN
    DECLARE v_unmatched_model INT DEFAULT 0;
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

    -- 预检：未命中 dim_model 的行数（dm.id IS NULL）
    -- 超过 100 行视为维表缺失严重，阻断；否则发 note 但继续写入
    SELECT COUNT(*)
      INTO v_unmatched_model
      FROM ods_sales_monthly_model ods
      JOIN dim_oem doem
        ON doem.oem_name = TRIM(ods.manufacturer_name_raw)
      LEFT JOIN dim_model dm
        ON dm.model_name = TRIM(ods.model_name_raw)
       AND dm.oem_id = doem.id
     WHERE ods.sales_volume IS NOT NULL
       AND ods.sales_volume > 0
       AND (p_month IS NULL OR ods.stat_month = p_month)
       AND NOT EXISTS (
            SELECT 1 FROM dwd_sales_monthly_model d
             WHERE d.row_hash = ods.row_hash
       )
       AND dm.id IS NULL;

    IF v_unmatched_model > 100 THEN
        SET @etl_unmatched_model = v_unmatched_model;
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '未匹配 dim_model 的行数超过 100，请先运行 sp_etl_build_sales_dimensions（详见 @etl_unmatched_model）。';
    END IF;

    START TRANSACTION;

    INSERT INTO dwd_sales_monthly_model (
        stat_month, source,
        model_name, model_name_raw, model_id,
        manufacturer_name, oem_id, brand_id, company_id,
        ads_id, ads_supplier_name,
        sales_volume, price_min, price_max, rank_no, row_hash
    )
    SELECT
        DATE_FORMAT(ods.stat_month, '%Y-%m')                                   AS stat_month,
        ods.source,
        TRIM(ods.model_name_raw)                                               AS model_name,
        ods.model_name_raw                                                     AS model_name_raw,
        dm.id                                                                  AS model_id,
        TRIM(ods.manufacturer_name_raw)                                        AS manufacturer_name,
        doem.id                                                                AS oem_id,
        dm.brand_id                                                            AS brand_id,
        doem.company_id                                                        AS company_id,
        dm.ads_id                                                              AS ads_id,
        dm.ads_supplier_name                                                   AS ads_supplier_name,
        ods.sales_volume,
        CAST(NULLIF(TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-',  1), '-', -1)), '') AS DECIMAL(10,2)) AS price_min,
        CAST(NULLIF(TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(ods.price_range_raw, '-', -1), '-',  1)), '') AS DECIMAL(10,2)) AS price_max,
        ods.rank_no,
        ods.row_hash
    FROM ods_sales_monthly_model ods
    INNER JOIN dim_oem doem
        ON doem.oem_name = TRIM(ods.manufacturer_name_raw)
    LEFT JOIN dim_model dm
        ON dm.model_name = TRIM(ods.model_name_raw)
       AND dm.oem_id   = doem.id
    WHERE ods.sales_volume IS NOT NULL
      AND ods.sales_volume > 0
      AND (p_month IS NULL OR ods.stat_month = p_month)
      AND NOT EXISTS (
            SELECT 1 FROM dwd_sales_monthly_model d
             WHERE d.row_hash = ods.row_hash
      );

    SET v_rows = ROW_COUNT();
    COMMIT;
    SET @etl_affected_rows = v_rows;

    -- 未匹配 model 但数量在阈值内：发 note 提醒（不回滚，不阻断）
    IF v_unmatched_model > 0 THEN
        SIGNAL SQLSTATE '01000'
            SET MESSAGE_TEXT = '部分行未匹配 dim_model，已写入 DWD（model_id=NULL），请尽快补维。';
    END IF;
END$$


-- ============================================================
-- 3. sp_etl_update_lifecycle
--    来源：pipelines/dim/build_sales_dimensions.py::update_dim_lifecycle
--    规则：
--      - 基准月 = p_month 非空用 p_month，否则 DWD 中 MAX(stat_month)
--      - 阈值 = 基准月 - 3 个月；last_seen_month >= 阈值 则活跃
--      - 无销量数据的维度记录 is_active = 0
--    注意：dwd.stat_month 是 CHAR(7)，dim 中是 DATE，需统一转 DATE
-- ============================================================
DROP PROCEDURE IF EXISTS sp_etl_update_lifecycle$$
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


-- ============================================================
-- 4. sp_etl_agg_sales_monthly
--    来源：pipelines/serve/agg_sales_monthly.py
--    改造：VALUES(col) → alias.col（MySQL 8.0.19+）
--         p_month 增量；两个 INSERT 同一事务
--    注意：dwd.stat_month 是 CHAR(7)，dws 是 DATE，子查询内统一转 DATE
-- ============================================================
DROP PROCEDURE IF EXISTS sp_etl_agg_sales_monthly$$
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
