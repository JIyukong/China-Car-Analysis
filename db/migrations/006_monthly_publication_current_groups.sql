-- 管理员维护脚本。调度账号仅更新经核验月份的当前标记。
-- 旧分组保留在基表中。Power BI 使用下方视图读取当前分组。
USE dw_auto_stock;

ALTER TABLE dws_sales_monthly_company
    ADD COLUMN is_current TINYINT(1) NOT NULL DEFAULT 0 COMMENT '月度当前发布分组';

ALTER TABLE dws_sales_monthly_brand
    ADD COLUMN is_current TINYINT(1) NOT NULL DEFAULT 0 COMMENT '月度当前发布分组';

CREATE OR REPLACE SQL SECURITY INVOKER VIEW vw_dws_sales_monthly_company_current AS
SELECT id, stat_month, company_id, company_name, total_sales, market_share,
       yoy_growth, mom_growth, created_at, updated_at
  FROM dws_sales_monthly_company
 WHERE is_current = 1;

CREATE OR REPLACE SQL SECURITY INVOKER VIEW vw_dws_sales_monthly_brand_current AS
SELECT id, stat_month, brand_id, brand_name, total_sales, market_share,
       yoy_growth, mom_growth, created_at, updated_at
  FROM dws_sales_monthly_brand
 WHERE is_current = 1;
