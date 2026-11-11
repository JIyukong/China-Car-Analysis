-- ============================================================
-- 05_create_dim_zipper_tables.sql
-- DIM 维度层 - 拉链表（SCD2 缓慢变化维）建表脚本
-- 追踪维度属性随时间的变化轨迹
-- ============================================================

USE dw_auto_stock;

-- ============================================================
-- DIM 拉链层
-- ============================================================

-- 1. 车型智驾方案拉链表（SCD2）
-- 来源：dim_model_ads_manual 手工权威存储表
-- 仅接收当前已存在于 dim_model 的车型；尚未上市/尚无销量的车型留在权威存储表中
-- 同一车型仅在 ads_supplier / ads_supplier_mode 变化时，关闭旧版本并开启新版本
-- ads_proportion / 上市时间修正直接覆盖当前版本，避免无意义版本膨胀
CREATE TABLE IF NOT EXISTS dim_model_ads_zipper (
    id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    model_id               BIGINT UNSIGNED NOT NULL                COMMENT '车型ID（外键→dim_model.id）',
    model_name_snapshot    VARCHAR(128)    NOT NULL                COMMENT '车型名称快照（便于手工维护时识别）',
    brand_name_snapshot    VARCHAR(128)    NOT NULL                COMMENT '品牌名称快照（便于手工维护时识别）',
    ads_proportion_pct     DECIMAL(5,2)    DEFAULT NULL            COMMENT 'ADS 搭载占比（百分比数值，如 95.00）',
    ads_supplier           VARCHAR(64)     NOT NULL DEFAULT '未知' COMMENT '智驾供应商（如 华为ADS/小鹏自研/Momenta/大疆/百度/毫末等）',
    ads_supplier_mode      VARCHAR(64)     NOT NULL DEFAULT '未知' COMMENT '智驾供应商模式（如 鸿蒙智行/HI模式/HIPLUS/华为零部件等）',
    listed_at_raw          VARCHAR(32)     DEFAULT NULL            COMMENT '上市时间原文（保留手工 CSV 原始表达）',
    listed_at_date         DATE            DEFAULT NULL            COMMENT '上市时间标准化日期（模糊粒度取区间首日）',
    listed_at_granularity  VARCHAR(16)     NOT NULL DEFAULT 'unknown' COMMENT '上市时间粒度（day/month/quarter/season/unknown）',
    effective_start_date   DATE            NOT NULL                COMMENT '生效开始日期（该智驾方案开始搭载的月份）',
    effective_end_date     DATE            NOT NULL DEFAULT '9999-12-31' COMMENT '生效结束日期（9999-12-31 表示当前有效；关闭时填入新方案生效前一月）',
    is_current             TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否当前有效：1=当前版本 0=历史版本',
    source_table           VARCHAR(64)     NOT NULL DEFAULT 'dim_model_ads_manual' COMMENT '来源表名',
    source_note            VARCHAR(255)    DEFAULT NULL            COMMENT '来源说明或备注（便于手工维护记录依据）',
    created_at             DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at             DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_model_ads_start (model_id, effective_start_date),
    KEY idx_ads_model (model_id),
    KEY idx_ads_current (is_current),
    KEY idx_ads_supplier (ads_supplier),
    KEY idx_ads_date_range (effective_start_date, effective_end_date),
    CONSTRAINT fk_ads_model FOREIGN KEY (model_id) REFERENCES dim_model(id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型智驾方案拉链表（SCD2，追踪智驾供应商/模式历史变化）';
