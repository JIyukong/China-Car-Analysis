-- ============================================================
-- 005_add_dwd_sales_unique_month_model.sql
-- 为 dwd_sales_monthly_model 添加业务唯一索引
--   uk_dwd_sales_month_model (stat_month, model_id)
--
-- 作用：
--   同一月份同一车型只能有一条记录，从数据库层面防止重复入库。
--   row_hash 唯一索引仅保证 ODS 行级幂等，无法防止同月同车型来自不同 ODS 行
--   （例如不同 source、历史重跑）造成的重复聚合。
--
-- 前置条件：
--   执行前必须确认表中不存在 (stat_month, model_id) 重复，否则 ADD UNIQUE 会失败。
--   预检 SQL：
--     SELECT stat_month, model_id, COUNT(*) AS cnt
--     FROM dwd_sales_monthly_model
--     GROUP BY stat_month, model_id HAVING cnt > 1;
--
-- 回滚：
--   ALTER TABLE dwd_sales_monthly_model DROP INDEX uk_dwd_sales_month_model;
-- ============================================================

USE dw_auto_stock;

ALTER TABLE dwd_sales_monthly_model
    ADD UNIQUE KEY uk_dwd_sales_month_model (stat_month, model_id);
