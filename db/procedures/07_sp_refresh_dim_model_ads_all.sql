-- ============================================================================
-- 【遗留对象 / 代码库已无调用方】
-- 本存储过程属于早期纯 SQL ADS 刷新管线（串联 05、06 号存储过程，并被
-- db/events/01 事件调度）。当前等价逻辑已由 Python 实现：
--   pipelines/dim/refresh_dim_model_ads.py、pipelines/dim/manual_refresh.py
-- 全仓 Python 代码中不存在对 sp_refresh_dim_model_ads_all 的任何 CALL。
-- 该对象可能仍部署在线上 MySQL，故本阶段只标注、不删除；下线前需 DBA 确认。
-- ============================================================================
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
