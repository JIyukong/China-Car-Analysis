-- ============================================================
-- dim/08_create_dim_commodity.sql
-- 商品期货品种维度表 dim_commodity
-- 来源：原 18_create_ods_futures_daily.sql 拆分
-- 描述期货品种本体（碳酸锂/铜/铝），与 dim_stock 平级
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS dim_commodity;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS dim_commodity (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    commodity_code  VARCHAR(16)     NOT NULL                COMMENT '品种大写代码（如 LC/CU/AL）',
    commodity_name  VARCHAR(64)     NOT NULL                COMMENT '品种中文名（如 碳酸锂）',
    exchange        VARCHAR(16)     NOT NULL                COMMENT '交易所（GFEX/SHFE/DCE/CZCE/CFFEX/INE）',
    category        VARCHAR(32)     DEFAULT NULL            COMMENT '品种类别（energy_metal/industrial_metal/precious_metal/agricultural/chemical/energy）',
    trading_unit    VARCHAR(64)     DEFAULT NULL            COMMENT '交易单位（如 5吨/手）',
    quote_unit      VARCHAR(32)     DEFAULT NULL            COMMENT '报价单位（如 元/吨）',
    listed_date     DATE            DEFAULT NULL            COMMENT '上市日期',
    main_symbol     VARCHAR(16)     NOT NULL                COMMENT '主力连续 symbol（如 LC0/CU0/AL0），采集入参',
    currency        VARCHAR(8)      NOT NULL DEFAULT 'CNY'  COMMENT '计价货币',
    description     VARCHAR(255)    DEFAULT NULL            COMMENT '品种描述',
    is_active       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用（1启用/0停用）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_commodity_code (commodity_code),
    KEY idx_commodity_exchange (exchange),
    KEY idx_commodity_category (category),
    KEY idx_commodity_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='商品期货品种维度表（与 dim_stock 平级，描述期货品种本体）';
