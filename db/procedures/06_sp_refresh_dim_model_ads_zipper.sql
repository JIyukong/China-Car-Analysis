USE dw_auto_stock;

-- 存储过程：刷新 dim_model ADS 拉链表
-- 依赖 sp_refresh_dim_model_ads_manual_match（见 05 号文件）
DROP PROCEDURE IF EXISTS sp_refresh_dim_model_ads_zipper;
DELIMITER $$
CREATE PROCEDURE sp_refresh_dim_model_ads_zipper()
BEGIN
    CALL sp_refresh_dim_model_ads_manual_match();

    -- 先关闭已经不在权威表中、或当前不再命中 dim_model 的当前链记录。
    UPDATE dim_model_ads_zipper z
    LEFT JOIN dim_model_ads_manual src
        ON src.matched_model_id = z.model_id
       AND src.is_in_dim_model = 1
    SET z.effective_end_date = GREATEST(
            z.effective_start_date,
            DATE_SUB(CURRENT_DATE, INTERVAL 1 DAY)
        ),
        z.is_current = 0
    WHERE z.is_current = 1
      AND src.id IS NULL;

    -- 供应商或模式变化时，关闭旧当前链，等待下一步插入新当前链。
    UPDATE dim_model_ads_zipper z
    INNER JOIN dim_model_ads_manual src
        ON src.matched_model_id = z.model_id
       AND src.is_in_dim_model = 1
    SET z.effective_end_date = GREATEST(
            z.effective_start_date,
            DATE_SUB(COALESCE(src.listed_at_date, CURRENT_DATE), INTERVAL 1 DAY)
        ),
        z.is_current = 0
    WHERE z.is_current = 1
      AND (
          z.ads_supplier <> src.ads_supplier
          OR z.ads_supplier_mode <> src.ads_supplier_mode
      );

    -- 仅占比/上市时间修正时，直接覆盖当前链快照字段，不新增版本。
    UPDATE dim_model_ads_zipper z
    INNER JOIN dim_model_ads_manual src
        ON src.matched_model_id = z.model_id
       AND src.is_in_dim_model = 1
    INNER JOIN dim_model dm
        ON dm.id = src.matched_model_id
    SET z.model_name_snapshot = src.model_name,
        z.brand_name_snapshot = COALESCE(NULLIF(TRIM(dm.brand_name), ''), '占位'),
        z.ads_proportion_pct = src.ads_proportion_pct,
        z.listed_at_raw = src.listed_at_raw,
        z.listed_at_date = src.listed_at_date,
        z.listed_at_granularity = src.listed_at_granularity,
        z.source_table = 'dim_model_ads_manual',
        z.source_note = '来自手工 ADS 权威表'
    WHERE z.is_current = 1
      AND z.ads_supplier = src.ads_supplier
      AND z.ads_supplier_mode = src.ads_supplier_mode;

    -- 无当前链或刚因供应商/模式变化而关闭旧链时，插入新的当前链。
    INSERT INTO dim_model_ads_zipper (
        model_id,
        model_name_snapshot,
        brand_name_snapshot,
        ads_proportion_pct,
        ads_supplier,
        ads_supplier_mode,
        listed_at_raw,
        listed_at_date,
        listed_at_granularity,
        effective_start_date,
        effective_end_date,
        is_current,
        source_table,
        source_note
    )
    SELECT
        src.matched_model_id AS model_id,
        src.model_name AS model_name_snapshot,
        COALESCE(NULLIF(TRIM(dm.brand_name), ''), '占位') AS brand_name_snapshot,
        src.ads_proportion_pct,
        src.ads_supplier,
        src.ads_supplier_mode,
        src.listed_at_raw,
        src.listed_at_date,
        src.listed_at_granularity,
        COALESCE(src.listed_at_date, CURRENT_DATE) AS effective_start_date,
        '9999-12-31' AS effective_end_date,
        1 AS is_current,
        'dim_model_ads_manual' AS source_table,
        '来自手工 ADS 权威表' AS source_note
    FROM dim_model_ads_manual src
    INNER JOIN dim_model dm
        ON dm.id = src.matched_model_id
    LEFT JOIN dim_model_ads_zipper current_zip
        ON current_zip.model_id = src.matched_model_id
       AND current_zip.is_current = 1
    WHERE src.is_in_dim_model = 1
      AND current_zip.id IS NULL;
END$$
DELIMITER ;
