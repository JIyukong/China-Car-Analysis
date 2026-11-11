-- ============================================================
-- dim/01_create_dim_core.sql
-- 维度层核心表（8 张）：
--   dim_company / dim_oem / dim_brand / dim_model /
--   dim_vehicle_type / dim_vehicle_subtype / dim_date / dim_stock
-- 注意：dim_energy_vehicle / stg_dim_energy_vehicle 由 04 号文件负责
-- 来源：原 01_create_tables.sql 拆分（dim 层）
-- ============================================================

USE dw_auto_stock;

-- 1. 上市公司维度表
CREATE TABLE IF NOT EXISTS dim_company (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    company_code    VARCHAR(32)     NOT NULL                COMMENT '上市公司唯一编码（业务主键，如 BYD/SAIC/OTHER）',
    company_name    VARCHAR(64)     NOT NULL                COMMENT '上市公司简称（如"比亚迪"）',
    full_name       VARCHAR(128)    DEFAULT NULL            COMMENT '上市公司全称（如"比亚迪股份有限公司"）',
    company_type    ENUM('A股','港股','美股','非上市占位','其他') NOT NULL DEFAULT '其他' COMMENT '上市公司类型',
    country         VARCHAR(32)     NOT NULL DEFAULT 'CN'   COMMENT '所属国家/地区代码',
    aliases         JSON            DEFAULT NULL            COMMENT '别名数组（如 ["比亚迪股份","BYD"]）',
    is_listed       TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否上市：1=上市 0=非上市占位',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_company_code (company_code),
    UNIQUE KEY uk_company_name (company_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上市公司维度表';

-- 2. 主机厂维度表
CREATE TABLE IF NOT EXISTS dim_oem (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    oem_code             VARCHAR(64)     NOT NULL                COMMENT '主机厂唯一编码（自动生成，辅助业务键）',
    oem_name             VARCHAR(128)    NOT NULL                COMMENT '主机厂名称（如"上汽大众""一汽-大众"）',
    company_id           BIGINT UNSIGNED NOT NULL                COMMENT '关联上市公司ID（外键→dim_company.id；无法归属时指向 OTHER）',
    company_name_snapshot VARCHAR(64)    DEFAULT NULL            COMMENT '上市公司名称快照',
    aliases              JSON            DEFAULT NULL            COMMENT '别名数组',
    first_seen_month     DATE            DEFAULT NULL            COMMENT '当前数据集下首次销售月份',
    last_seen_month      DATE            DEFAULT NULL            COMMENT '当前数据集下最后一次销售月份',
    is_active            TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否活跃：1=活跃 0=不活跃',
    created_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_oem_code (oem_code),
    UNIQUE KEY uk_oem_name (oem_name),
    KEY idx_oem_company (company_id),
    CONSTRAINT fk_oem_company FOREIGN KEY (company_id) REFERENCES dim_company(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='主机厂维度表';

-- 3. 品牌维度表
CREATE TABLE IF NOT EXISTS dim_brand (
    id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    brand_code             VARCHAR(64)     NOT NULL                COMMENT '品牌唯一编码（业务主键）',
    brand_name             VARCHAR(128)    NOT NULL                COMMENT '品牌名称（如"大众""奥迪"）',
    brand_origin_country   VARCHAR(32)     NOT NULL DEFAULT 'CN'   COMMENT '品牌来源国家/地区代码',
    is_joint_venture_brand TINYINT(1)      NOT NULL DEFAULT 0      COMMENT '是否合资品牌：1=是 0=否',
    aliases                JSON            DEFAULT NULL            COMMENT '别名数组',
    created_at             DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at             DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_brand_code (brand_code),
    UNIQUE KEY uk_brand_name (brand_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='品牌维度表';

-- 4. 车型维度表
CREATE TABLE IF NOT EXISTS dim_model (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    model_code        VARCHAR(64)     NOT NULL                COMMENT '车型唯一编码（业务主键）',
    model_name        VARCHAR(128)    NOT NULL                COMMENT '车型名称',
    brand_id          BIGINT UNSIGNED NOT NULL                COMMENT '品牌ID（外键→dim_brand.id）',
    brand_name        VARCHAR(128)    DEFAULT NULL            COMMENT '品牌名称快照',
    oem_id            BIGINT UNSIGNED NOT NULL                COMMENT '主机厂ID（外键→dim_oem.id）',
    oem_name          VARCHAR(128)    DEFAULT NULL            COMMENT '主机厂名称快照',
    ads_id            BIGINT UNSIGNED DEFAULT NULL            COMMENT 'ADS 当前有效拉链ID（引用 dim_model_ads_zipper.id）',
    ads_supplier_name VARCHAR(128)    DEFAULT NULL            COMMENT 'ADS 当前有效供应商名称（来自 dim_model_ads_zipper.ads_supplier）',
    energy_type       ENUM('燃油','新能源','其他','占位') NOT NULL DEFAULT '占位' COMMENT '能源类型（由 dim_energy_vehicle 同步，未命中为占位）',
    vehicle_type      ENUM('轿车','SUV','MPV','其他','占位') NOT NULL DEFAULT '占位' COMMENT '车型类型（由 dim_energy_vehicle 同步，未命中为占位）',
    first_seen_month  DATE            DEFAULT NULL            COMMENT '当前数据集下首次销售月份',
    last_seen_month   DATE            DEFAULT NULL            COMMENT '当前数据集下最后一次销售月份',
    is_active         TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否活跃：1=活跃 0=不活跃',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_model_code (model_code),
    UNIQUE KEY uk_model_name_oem (model_name, oem_id),
    KEY idx_model_brand (brand_id),
    KEY idx_model_oem (oem_id),
    CONSTRAINT fk_model_brand FOREIGN KEY (brand_id) REFERENCES dim_brand(id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_model_oem   FOREIGN KEY (oem_id)   REFERENCES dim_oem(id)   ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型维度表';

-- 5. 车型大类维度表
CREATE TABLE IF NOT EXISTS dim_vehicle_type (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    type_code   VARCHAR(16)     NOT NULL                COMMENT '大类键（业务主键，如 1/2/3/4）',
    type_name   VARCHAR(32)     NOT NULL                COMMENT '大类名称（如 SUV/轿车/MPV/其他）',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_type_code (type_code),
    UNIQUE KEY uk_type_name (type_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型大类维度表';

-- 6. 车型细分类型维度表
CREATE TABLE IF NOT EXISTS dim_vehicle_subtype (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    subtype_code    VARCHAR(16)     NOT NULL                COMMENT '细分类型键（业务主键，如 1-10）',
    subtype_name    VARCHAR(32)     NOT NULL                COMMENT '细分类型名称（如 MPV/SUV/中型车/紧凑型车/跑车/不限等）',
    vehicle_type_id BIGINT UNSIGNED NOT NULL                COMMENT '关联车型大类ID（外键→dim_vehicle_type.id）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_subtype_code (subtype_code),
    UNIQUE KEY uk_subtype_name (subtype_name),
    CONSTRAINT fk_subtype_vehicle_type FOREIGN KEY (vehicle_type_id)
        REFERENCES dim_vehicle_type(id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型细分类型维度表';

-- 7. 日期维度表
CREATE TABLE IF NOT EXISTS dim_date (
    date_key         INT UNSIGNED     NOT NULL                COMMENT '日期键（YYYYMMDD）',
    full_date        DATE             NOT NULL                COMMENT '自然日期',
    year_num         SMALLINT UNSIGNED NOT NULL               COMMENT '年份',
    quarter_num      TINYINT UNSIGNED NOT NULL                COMMENT '季度（1-4）',
    month_num        TINYINT UNSIGNED NOT NULL                COMMENT '月份（1-12）',
    month_key        INT UNSIGNED     NOT NULL                COMMENT '月份键（YYYYMM）',
    month_label      VARCHAR(16)      NOT NULL                COMMENT '月份标签（如 2026-07）',
    week_num         TINYINT UNSIGNED NOT NULL                COMMENT '年内周次',
    day_of_month     TINYINT UNSIGNED NOT NULL                COMMENT '月内第几天',
    day_of_week      TINYINT UNSIGNED NOT NULL                COMMENT '周内第几天（1=周一 7=周日）',
    is_month_start   TINYINT(1)       NOT NULL DEFAULT 0      COMMENT '是否月初',
    is_month_end     TINYINT(1)       NOT NULL DEFAULT 0      COMMENT '是否月末',
    is_quarter_end   TINYINT(1)       NOT NULL DEFAULT 0      COMMENT '是否季末',
    is_year_end      TINYINT(1)       NOT NULL DEFAULT 0      COMMENT '是否年末',
    month_start_date DATE             NOT NULL                COMMENT '当月第一天',
    month_end_date   DATE             NOT NULL                COMMENT '当月最后一天',
    created_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (date_key),
    UNIQUE KEY uk_full_date (full_date),
    KEY idx_month_key (month_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通用日期维度表';

-- 8. 股票维度表
CREATE TABLE IF NOT EXISTS dim_stock (
    id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stock_code          VARCHAR(32)     NOT NULL                COMMENT '股票统一编码（业务主键，如 002594.SZ / 1211.HK / NIO.US）',
    stock_name          VARCHAR(128)    NOT NULL                COMMENT '证券名称',
    market              ENUM('A','HK','US') NOT NULL            COMMENT '上市市场：A=A股 HK=港股 US=美股',
    exchange            VARCHAR(16)     DEFAULT NULL            COMMENT '交易所：SZ/SH/HKEX/NYSE/NASDAQ',
    currency            VARCHAR(8)      DEFAULT NULL            COMMENT '计价币种：CNY/HKD/USD',
    listed_company_name VARCHAR(128)    DEFAULT NULL            COMMENT '关联上市公司名称快照',
    is_listed           TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否在市：1=在市 0=退市',
    listed_date         DATE            DEFAULT NULL            COMMENT '上市日期',
    delisted_date       DATE            DEFAULT NULL            COMMENT '退市日期（NULL 表示在市）',
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_code (stock_code),
    KEY idx_stock_market (market)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股票维度表（一只股票一行，支持多市场上市）';
