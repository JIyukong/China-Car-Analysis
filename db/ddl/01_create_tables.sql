-- ============================================================
-- 01_create_tables.sql
-- 16888 销量数仓统一建表脚本
-- 维度层：dim_(7) + 桥接层：bridge_(2) + 贴源层：ods_(3) 共 12 张表
-- 命名遵循数仓 7 大类前缀规范
-- ============================================================

USE dw_auto_stock;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS dim_model_ads_zipper;
DROP TABLE IF EXISTS stg_dim_energy_vehicle;
DROP TABLE IF EXISTS dim_energy_vehicle;
DROP TABLE IF EXISTS bridge_brand_oem;
DROP TABLE IF EXISTS bridge_company_stock;
DROP TABLE IF EXISTS dim_model;
DROP TABLE IF EXISTS dim_oem;
DROP TABLE IF EXISTS dim_brand;
DROP TABLE IF EXISTS dim_stock;
DROP TABLE IF EXISTS dim_company;
SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================
-- 一、dim_ 维度层（7 张）
-- ============================================================

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

-- 5. 车型能源/类型手工维度表
CREATE TABLE IF NOT EXISTS dim_energy_vehicle (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    model_name    VARCHAR(128)    NOT NULL                COMMENT '车型名称（业务主键）',
    energy_type   ENUM('燃油','新能源','其他','占位') NOT NULL DEFAULT '占位' COMMENT '能源类型（手工维度表权威值）',
    vehicle_type  ENUM('轿车','SUV','MPV','其他','占位') NOT NULL DEFAULT '占位' COMMENT '车型类型（手工维度表权威值）',
    source_file   VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_energy_vehicle.csv' COMMENT '手工维护文件来源',
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_dim_energy_vehicle_model_name (model_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型能源类型/车型类型手工维度表';

-- 6. 车型能源/类型手工维度导入暂存表
CREATE TABLE IF NOT EXISTS stg_dim_energy_vehicle (
    model_name    VARCHAR(128)    NOT NULL                COMMENT '车型名称（来自手工 CSV）',
    energy_type   VARCHAR(16)     NOT NULL                COMMENT '能源类型原始值',
    vehicle_type  VARCHAR(16)     NOT NULL                COMMENT '车型类型原始值',
    source_file   VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_energy_vehicle.csv' COMMENT '手工维护文件来源',
    loaded_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '导入时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型能源类型/车型类型手工维度导入暂存表';

-- 7. 车型大类维度表
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

-- 8. 车型细分类型维度表
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

-- 9. 日期维度表
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

-- 10. 股票维度表
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

-- ============================================================
-- 二、bridge_ 桥接层（2 张）
-- ============================================================

-- 11. 品牌-主机厂桥接表（多对多关联）
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

-- 12. 上市公司-股票桥接表（多对多关联）
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

-- ============================================================
-- 三、ods_ 贴源层（3 张，爬虫/接口直落原始数据）
-- ============================================================

-- 13. 车型月销量 贴源表（16888 style）
CREATE TABLE IF NOT EXISTS ods_sales_monthly_model (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    source                ENUM('16888_style','other') NOT NULL DEFAULT '16888_style' COMMENT '数据来源',
    stat_month            DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    rank_no               INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    model_name_raw        VARCHAR(128)    NOT NULL                COMMENT '原始车型名（未清洗）',
    manufacturer_name_raw VARCHAR(128)    NOT NULL                COMMENT '原始主机厂名（未清洗）',
    price_range_raw       VARCHAR(64)     DEFAULT NULL            COMMENT '原始价格区间（未清洗）',
    sales_volume          INT UNSIGNED    DEFAULT NULL            COMMENT '月销量（辆）',
    source_file           VARCHAR(255)    DEFAULT NULL            COMMENT '来源文件路径或文件名',
    source_url            VARCHAR(255)    DEFAULT NULL            COMMENT '来源页面 URL',
    row_hash              CHAR(32)        DEFAULT NULL            COMMENT '行级哈希（用于幂等校验）',
    fetch_time            DATETIME        NOT NULL                COMMENT '采集/入库时间',
    created_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_style_month_model_oem (source, stat_month, model_name_raw, manufacturer_name_raw),
    UNIQUE KEY uk_style_row_hash (row_hash),
    KEY idx_ods_style_month (stat_month),
    KEY idx_ods_style_model (model_name_raw),
    KEY idx_ods_style_manufacturer (manufacturer_name_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型月销量贴源表（16888 style 原始未清洗）';

-- 14. 车型城市月销量 贴源表（16888 city）
CREATE TABLE IF NOT EXISTS ods_sales_monthly_model_city (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    source          ENUM('16888_city','other') NOT NULL DEFAULT '16888_city' COMMENT '数据来源',
    stat_month      DATE            NOT NULL                COMMENT '统计月份（该月第一天）',
    rank_no         INT UNSIGNED    DEFAULT NULL            COMMENT '当月排名',
    model_name_raw  VARCHAR(128)    NOT NULL                COMMENT '原始车型名（未清洗）',
    city_name_raw   VARCHAR(64)     NOT NULL                COMMENT '原始城市名（未清洗）',
    level_raw       VARCHAR(64)     DEFAULT NULL            COMMENT '原始车型级别（未清洗）',
    price_range_raw VARCHAR(64)     DEFAULT NULL            COMMENT '原始价格区间（未清洗）',
    sales_volume    INT UNSIGNED    DEFAULT NULL            COMMENT '城市月销量（辆）',
    source_file     VARCHAR(255)    DEFAULT NULL            COMMENT '来源文件路径或文件名',
    source_url      VARCHAR(255)    DEFAULT NULL            COMMENT '来源页面 URL',
    row_hash        CHAR(32)        DEFAULT NULL            COMMENT '行级哈希（用于幂等校验）',
    fetch_time      DATETIME        NOT NULL                COMMENT '采集/入库时间',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_city_month_model_city (source, stat_month, model_name_raw, city_name_raw),
    UNIQUE KEY uk_city_row_hash (row_hash),
    KEY idx_ods_city_month (stat_month),
    KEY idx_ods_city_model (model_name_raw),
    KEY idx_ods_city_city (city_name_raw)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型城市月销量贴源表（16888 city 原始未清洗）';

-- 15. 股价日线 贴源表
CREATE TABLE IF NOT EXISTS ods_stock_daily (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    stock_id          BIGINT UNSIGNED DEFAULT NULL              COMMENT '股票ID（可选映射，用于下游使用便利）',
    stock_code_raw    VARCHAR(32)     NOT NULL                  COMMENT '原始股票代码（如 002594.SZ / 1211.HK / NIO.US）',
    stock_name_raw    VARCHAR(128)    DEFAULT NULL              COMMENT '原始股票名称快照',
    market_raw        VARCHAR(8)      NOT NULL                  COMMENT '原始市场代码（A/HK/US）',
    exchange_raw      VARCHAR(16)     DEFAULT NULL              COMMENT '原始交易所代码（SZ/SH/HKEX/NYSE/NASDAQ）',
    source_symbol     VARCHAR(32)     DEFAULT NULL              COMMENT '请求源接口时使用的 symbol 快照',
    trade_date        DATE            NOT NULL                  COMMENT '交易日',
    open              DECIMAL(12,4)   DEFAULT NULL              COMMENT '开盘价',
    high              DECIMAL(12,4)   DEFAULT NULL              COMMENT '最高价',
    low               DECIMAL(12,4)   DEFAULT NULL              COMMENT '最低价',
    close             DECIMAL(12,4)   DEFAULT NULL              COMMENT '收盘价',
    volume            BIGINT          DEFAULT NULL              COMMENT '成交量（股）',
    amount            DECIMAL(18,2)   DEFAULT NULL              COMMENT '成交额（元/港元/美元）',
    pct_change        DECIMAL(8,4)    DEFAULT NULL              COMMENT '涨跌幅（%，如 2.35 表示 +2.35%）',
    source            ENUM('akshare','other') NOT NULL DEFAULT 'akshare' COMMENT '数据来源',
    fetch_time        DATETIME        NOT NULL                  COMMENT '采集时间',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP                             COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_stock_code_date (stock_code_raw, trade_date, source),
    KEY idx_ods_stock_id (stock_id),
    KEY idx_ods_stock_code_date (stock_code_raw, trade_date),
    KEY idx_ods_stock_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股价日线贴源表（原始未清洗，不强制外键约束，保留 stock_id 便于下游使用）';
