-- ============================================================
-- 19_create_scheduler_tables.sql
-- 编号：19
-- 用途：调度体系元数据，共 3 张表
--   1) scheduler_run_log      任务运行日志（每次触发一条）
--   2) scheduler_heartbeat    调度器心跳（单行表，外部探活）
--   3) dim_trade_calendar     A 股交易日历（日链守卫用）
-- 与 init_database.py 的关系：
--   本文件由 scripts/init_database.py 在 18 号脚本之后、
--   20 号（存储过程）之前按编号顺序自动执行，无需手工干预。
-- 目标库：dw_auto_stock
-- ============================================================

USE dw_auto_stock;

-- ------------------------------------------------------------
-- 1. 任务运行日志
--    记录每一次 cron / 手动 / 依赖 / 重试触发的执行情况，
--    是 recovery、告警、依赖缺口扫描的事实来源。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler_run_log (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    run_id          VARCHAR(40)  NOT NULL COMMENT 'uuid4，业务幂等键',
    task_id         VARCHAR(64)  NOT NULL COMMENT '任务标识（task_config 中的 key）',
    business_key    VARCHAR(32)  NULL     COMMENT '业务周期键：stat_month(YYYY-MM) 或 trade_date(YYYY-MM-DD)',
    trigger_type    ENUM('cron','manual','dependency','retry') NOT NULL COMMENT '触发方式',
    parent_run_id   VARCHAR(40)  NULL     COMMENT '上游 run_id（依赖触发时）',
    status          ENUM('running','success','failed','timeout','skipped','killed','retrying') NOT NULL,
    hostname        VARCHAR(128) NULL     COMMENT '执行机主机名',
    pid             INT          NULL     COMMENT '执行进程 PID',
    started_at      DATETIME     NOT NULL COMMENT '开始时间',
    finished_at     DATETIME     NULL     COMMENT '结束时间',
    duration_sec    INT          NULL     COMMENT '耗时（秒）',
    retry_no        INT          NOT NULL DEFAULT 0 COMMENT '第几次重试（0 表示首次）',
    affected_rows   INT          NULL     COMMENT 'SP 通过 @etl_affected_rows 回传的影响行数',
    error_type      VARCHAR(128) NULL     COMMENT '异常类型（类名）',
    error_msg       TEXT         NULL     COMMENT '异常/失败信息（尾部截断）',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_run_id (run_id),
    KEY idx_task_started (task_id, started_at),
    KEY idx_task_business_status (task_id, business_key, status),
    KEY idx_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='调度任务运行日志';

-- ------------------------------------------------------------
-- 2. 调度器心跳（单行表，外部探活用）
--    固定主键 id=1，调度器启动时 UPSERT，运行期间每 30 秒 UPDATE。
--    外部脚本（check_scheduler_heartbeat.py）每 5 分钟探测：
--    若 TIMESTAMPDIFF(MINUTE, last_heartbeat, NOW()) > 3 则告警。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id              INT          NOT NULL PRIMARY KEY DEFAULT 1,
    hostname        VARCHAR(128) NOT NULL COMMENT '调度器所在主机名',
    pid             INT          NOT NULL COMMENT '调度器主进程 PID',
    last_heartbeat  DATETIME     NOT NULL COMMENT '最近一次心跳时间',
    started_at      DATETIME     NOT NULL COMMENT '本次启动时间',
    version         VARCHAR(32)  NOT NULL COMMENT '调度器版本号'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='调度器心跳（单行表）';

-- ------------------------------------------------------------
-- 3. A 股交易日历（日链守卫用）
--    is_open=1 表示当日开市，日链任务执行前先查此表，
--    非交易日抛 SkipTask。由 scripts/sync_trade_calendar.py
--    用 akshare tool_trade_date_hist_sina() 同步。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_trade_calendar (
    trade_date      DATE         NOT NULL PRIMARY KEY COMMENT '交易日',
    is_open         TINYINT(1)   NOT NULL COMMENT '1=交易日 0=休市',
    market          VARCHAR(16)  NOT NULL DEFAULT 'SSE' COMMENT 'SSE=沪深, SHFE=期货夜盘',
    note            VARCHAR(64)  NULL     COMMENT '备注（如节假日名称）',
    KEY idx_is_open (is_open)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易日历';
