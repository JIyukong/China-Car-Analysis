-- ============================================================
-- 02_create_powerbi_user.sql
-- 创建 Power BI 只读账号（仅 SELECT 权限）
-- 执行前：将 ${POWERBI_READONLY_USER} 与 ${POWERBI_READONLY_PASSWORD}
--         替换为实际用户名与强密码（建议从 .env 读取）
-- ============================================================

-- 1. 创建账号（允许从任意主机连接，生产环境应收紧为 Power BI 出网 IP 段）
CREATE USER IF NOT EXISTS '${POWERBI_READONLY_USER}'@'%'
    IDENTIFIED BY '${POWERBI_READONLY_PASSWORD}';

-- 2. 授予 dw_auto_stock 全库只读权限
GRANT SELECT ON dw_auto_stock.* TO '${POWERBI_READONLY_USER}'@'%';

-- 3. 显式回收所有写权限（双保险，防止意外的库级授权残留）
REVOKE INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, INDEX, REFERENCES, CREATE TEMPORARY TABLES,
      LOCK TABLES, CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, EXECUTE,
      TRIGGER, EVENT ON dw_auto_stock.* FROM '${POWERBI_READONLY_USER}'@'%';

-- 4. 刷新权限
FLUSH PRIVILEGES;

-- ============================================================
-- 验证步骤（用只读账号登录后执行）：
--   SELECT * FROM dw_auto_stock.dim_company LIMIT 1;  -- 应成功
--   INSERT INTO dw_auto_stock.dim_company(company_code, company_name) VALUES('X', '测试');
--     -- 应被拒绝：ERROR 1142 INSERT command denied
-- ============================================================
