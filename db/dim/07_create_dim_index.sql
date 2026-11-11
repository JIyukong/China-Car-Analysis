-- ============================================================
-- dim/07_create_dim_index.sql
-- 指数维度表 dim_index
-- 来源：原 17_create_ods_index_daily.sql 拆分
-- 统一管理来自不同编制机构的指数（THS / CSINDEX / WIND 等）
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS dim_index;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS dim_index (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    index_code      VARCHAR(32)     NOT NULL                COMMENT '指数代码（如 881125）',
    index_name      VARCHAR(128)    NOT NULL                COMMENT '指数名称（如 汽车整车）',
    index_source    ENUM('THS','CSINDEX','WIND','OTHER') NOT NULL DEFAULT 'THS' COMMENT '指数编制来源',
    market          VARCHAR(16)     DEFAULT NULL            COMMENT '市场标识（CN/HK/US 等）',
    category        VARCHAR(32)     DEFAULT NULL            COMMENT '指数类别（industry/concept/broad_based）',
    description     VARCHAR(255)    DEFAULT NULL            COMMENT '指数描述',
    is_active       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用（1启用/0停用）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_index_code_source (index_code, index_source),
    KEY idx_index_category (category),
    KEY idx_index_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='指数维度表（覆盖同花顺/中证/万得等编制机构的行业与基准指数）';
