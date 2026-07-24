USE dw_auto_stock;

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

CREATE TABLE IF NOT EXISTS stg_dim_energy_vehicle (
    model_name    VARCHAR(128)    NOT NULL                COMMENT '车型名称（来自手工 CSV）',
    energy_type   VARCHAR(16)     NOT NULL                COMMENT '能源类型原始值',
    vehicle_type  VARCHAR(16)     NOT NULL                COMMENT '车型类型原始值',
    source_file   VARCHAR(255)    NOT NULL DEFAULT 'data/manual_dimensions/dim_energy_vehicle.csv' COMMENT '手工维护文件来源',
    loaded_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '导入时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='车型能源类型/车型类型手工维度导入暂存表';
