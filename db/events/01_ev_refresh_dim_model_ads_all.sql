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
