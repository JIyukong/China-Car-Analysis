-- ============================================================
-- 15_alter_dwd_sales_monthly_model.sql
-- 对已上线 dwd_sales_monthly_model 表做两项改造：
--   1. stat_month 由 DATE 改为 CHAR(7)（YYYY-MM 格式）
--   2. 新增 ads_id / ads_supplier_name 两列（冗余自 dim_model，支持一表多维查询）
--   3. 回填历史数据（stat_month + ads_id + ads_supplier_name）
-- 顺序说明：
--   - 先新增两列并回填 → 再原地格式化 stat_month 内容为 YYYY-MM → 最后 MODIFY 列类型
--   - 若直接 MODIFY，MySQL 会把 DATE 隐式转成 'YYYY-MM-DD'（10 字符），无法塞进 CHAR(7)
-- ============================================================

USE dw_auto_stock;

-- ------------------------------------------------------------
-- 一、新增两列（放在 company_id 之后，与 plan 一致）
-- ------------------------------------------------------------
ALTER TABLE dwd_sales_monthly_model
    ADD COLUMN ads_id BIGINT UNSIGNED DEFAULT NULL COMMENT '智驾 ADS 拉链ID（冗余自 dim_model.ads_id）'                 AFTER company_id,
    ADD COLUMN ads_supplier_name VARCHAR(128) DEFAULT NULL COMMENT '智驾供应商名称快照（冗余自 dim_model.ads_supplier_name）' AFTER ads_id,
    ADD KEY idx_dwd_sales_ads (ads_id);

-- ------------------------------------------------------------
-- 二、回填 ads_id / ads_supplier_name（通过 model_id 关联 dim_model）
-- ------------------------------------------------------------
UPDATE dwd_sales_monthly_model d
    JOIN dim_model m ON d.model_id = m.id
SET d.ads_id            = m.ads_id,
    d.ads_supplier_name = m.ads_supplier_name;

-- ------------------------------------------------------------
-- 三、stat_month 从 DATE 转 CHAR(7)：先原地格式化再改类型
-- ------------------------------------------------------------
-- 3.1 先临时放宽长度（DATE 转 CHAR 会得到 10 字符 'YYYY-MM-DD'，避免截断报错）
ALTER TABLE dwd_sales_monthly_model
    MODIFY COLUMN stat_month CHAR(10) NOT NULL COMMENT '统计月份（临时中间态）';

-- 3.2 原地截断为 YYYY-MM 格式（历史数据的日部分固定为 01，可安全丢弃）
UPDATE dwd_sales_monthly_model
   SET stat_month = LEFT(stat_month, 7);

-- 3.3 最终收敛为 CHAR(7) YYYY-MM
ALTER TABLE dwd_sales_monthly_model
    MODIFY COLUMN stat_month CHAR(7) NOT NULL COMMENT '统计月份（YYYY-MM 格式，如 2026-07）';

-- ------------------------------------------------------------
-- 四、验证 SQL（人工执行确认）
-- ------------------------------------------------------------
-- SHOW CREATE TABLE dwd_sales_monthly_model;
-- SELECT COUNT(*) FROM dwd_sales_monthly_model WHERE stat_month NOT REGEXP '^[0-9]{4}-(0[1-9]|1[0-2])$';
-- SELECT SUM(ads_id IS NULL)/COUNT(*) FROM dwd_sales_monthly_model WHERE model_id IS NOT NULL;
