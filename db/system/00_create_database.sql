-- ============================================================
-- 00_create_database.sql
-- 创建数据库 dw_auto_stock（阿里云 RDS MySQL）
-- 字符集 utf8mb4，排序规则 utf8mb4_unicode_ci
-- 使用 IF NOT EXISTS 保证可重复执行
-- ============================================================

CREATE DATABASE IF NOT EXISTS dw_auto_stock
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

-- 切换到目标数据库
USE dw_auto_stock;
