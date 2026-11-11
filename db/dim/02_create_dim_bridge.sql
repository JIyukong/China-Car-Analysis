-- ============================================================
-- dim/02_create_dim_bridge.sql
-- 桥接层（2 张）：bridge_brand_oem / bridge_company_stock
-- 来源：原 01_create_tables.sql 拆分（bridge 层归入 dim 目录）
-- ============================================================

USE dw_auto_stock;

-- 1. 品牌-主机厂桥接表（多对多关联）
CREATE TABLE IF NOT EXISTS bridge_brand_oem (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    brand_id             BIGINT UNSIGNED NOT NULL                COMMENT '品牌ID（外键→dim_brand.id）',
    brand_name_snapshot  VARCHAR(128)    NOT NULL                COMMENT '品牌名称快照',
    oem_id               BIGINT UNSIGNED NOT NULL                COMMENT '主机厂ID（外键→dim_oem.id）',
    oem_name_snapshot    VARCHAR(128)    NOT NULL                COMMENT '主机厂名称快照',
    company_id           BIGINT UNSIGNED NOT NULL                COMMENT '关联上市公司ID（外键→dim_company.id）',
    company_name_snapshot VARCHAR(64)    NOT NULL                COMMENT '上市公司名称快照',
    relation_type        ENUM('主销','JV授权','进口代理','其他') NOT NULL DEFAULT '其他' COMMENT '品牌归属关系类型',
    effective_start_date DATE            NOT NULL                COMMENT '生效开始日期',
    effective_end_date   DATE            NOT NULL DEFAULT '9999-12-31' COMMENT '生效结束日期（9999-12-31 表示当前有效）',
    is_current           TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否当前有效：1=是 0=否',
    source_note          VARCHAR(255)    DEFAULT NULL            COMMENT '来源说明或备注',
    created_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_brand_oem_period (brand_id, oem_id, effective_start_date),
    KEY idx_bridge_brand (brand_id),
    KEY idx_bridge_oem (oem_id),
    KEY idx_bridge_company (company_id),
    CONSTRAINT fk_bridge_brand FOREIGN KEY (brand_id) REFERENCES dim_brand(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_bridge_oem   FOREIGN KEY (oem_id)   REFERENCES dim_oem(id)   ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_bridge_brand_oem_company FOREIGN KEY (company_id) REFERENCES dim_company(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='品牌-主机厂桥接表（多对多关联）';

-- 2. 上市公司-股票桥接表（多对多关联）
CREATE TABLE IF NOT EXISTS bridge_company_stock (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    company_id     BIGINT UNSIGNED NOT NULL                COMMENT '上市公司ID（外键→dim_company.id）',
    stock_id       BIGINT UNSIGNED NOT NULL                COMMENT '股票ID（外键→dim_stock.id）',
    primary_market ENUM('A','HK','US') DEFAULT NULL        COMMENT '该上市公司的主上市市场',
    is_primary     TINYINT(1)      NOT NULL DEFAULT 0      COMMENT '是否为主上市标的：1=主标的 0=次要标的',
    relation_note  VARCHAR(128)    DEFAULT NULL            COMMENT '关联关系说明（如"港股二次上市""ADR"）',
    created_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_company_stock (company_id, stock_id),
    KEY idx_bridge_stock (stock_id),
    CONSTRAINT fk_bridge_company FOREIGN KEY (company_id) REFERENCES dim_company(id) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_bridge_stock   FOREIGN KEY (stock_id)   REFERENCES dim_stock(id)   ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上市公司-股票桥接表（多对多关联）';
