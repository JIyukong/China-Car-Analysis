USE dw_auto_stock;

-- 存储过程：一键刷新 ADS（先匹配、再拉链、最后回填 dim_model）
-- 依赖 05/06 号存储过程
DROP PROCEDURE IF EXISTS sp_refresh_dim_model_ads_all;
DELIMITER $$
CREATE PROCEDURE sp_refresh_dim_model_ads_all()
BEGIN
    CALL sp_refresh_dim_model_ads_manual_match();
    CALL sp_refresh_dim_model_ads_zipper();

    -- 将 dim_model 的 ADS 引用字段回填到当前有效拉链记录。
    UPDATE dim_model dm
    LEFT JOIN dim_model_ads_zipper current_zip
        ON current_zip.model_id = dm.id
       AND current_zip.is_current = 1
    SET dm.ads_id = current_zip.id,
        dm.ads_supplier_name = current_zip.ads_supplier;
END$$
DELIMITER ;
