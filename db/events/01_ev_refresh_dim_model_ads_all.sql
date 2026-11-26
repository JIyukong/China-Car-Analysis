-- ============================================================================
-- 【遗留对象 / 代码库已无调用方】
-- 本事件定期调用 sp_refresh_dim_model_ads_all（见 db/procedures/07），属于
-- 早期纯 SQL ADS 刷新管线。当前等价刷新逻辑已由 Python 实现：
--   pipelines/dim/refresh_dim_model_ads.py、pipelines/dim/manual_refresh.py
-- 全仓 Python 代码中不存在对该事件或其关联存储过程的任何调用。
-- 该事件可能仍部署在线上 MySQL 由事件调度器执行，故本阶段只标注、不删除；
-- 下线前需由 DBA 确认线上无依赖后再 DROP EVENT。
-- ============================================================================
USE dw_auto_stock;

-- 说明：事件默认 DISABLED，需要时 SET GLOBAL event_scheduler=ON 后手动 ALTER ... ENABLE
DROP EVENT IF EXISTS ev_refresh_dim_model_ads_all;
DELIMITER $$
CREATE EVENT ev_refresh_dim_model_ads_all
    ON SCHEDULE EVERY 1 DAY
    STARTS CURRENT_TIMESTAMP + INTERVAL 1 DAY
    DISABLE
    DO
        CALL sp_refresh_dim_model_ads_all()$$
DELIMITER ;
