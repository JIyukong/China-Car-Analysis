-- ============================================================================
-- 【遗留对象 / 代码库已无调用方】
-- 本存储过程与 06、07 号存储过程及 db/events/01 事件共同构成早期的纯 SQL
-- ADS 刷新管线。当前应用代码已改用 Python 实现等价逻辑：
--   pipelines/dim/refresh_dim_model_ads.py、pipelines/dim/manual_refresh.py
-- 全仓 Python 代码中不存在对 sp_refresh_dim_model_ads_manual_match 的任何 CALL。
-- 该对象可能仍部署在线上 MySQL（被事件调度器或 DBA 手动使用），故本阶段只
-- 标注、不删除。下线前需由 DBA 确认线上无依赖后再 DROP。
-- ============================================================================
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
