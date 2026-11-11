USE dw_auto_stock;

-- 存储过程：匹配 dim_model_ads_manual 与 dim_model
DROP PROCEDURE IF EXISTS sp_refresh_dim_model_ads_manual_match;
DELIMITER $$
CREATE PROCEDURE sp_refresh_dim_model_ads_manual_match()
BEGIN
    UPDATE dim_model_ads_manual src
    LEFT JOIN dim_model dm
        ON dm.model_name = src.model_name
    SET src.matched_model_id = dm.id,
        src.is_in_dim_model = CASE WHEN dm.id IS NULL THEN 0 ELSE 1 END,
        src.missing_in_dim_model_reason = CASE
            WHEN dm.id IS NULL THEN 'DIM_MODEL_NOT_FOUND'
            ELSE NULL
        END,
        src.match_checked_at = NOW();
END$$
DELIMITER ;
