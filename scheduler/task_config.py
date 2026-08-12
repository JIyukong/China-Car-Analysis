"""调度任务参数配置：cron、超时、重试、下游依赖、业务键。

字段含义：
    callable                   "module.path:function_name" 形式的业务入口
    cron                       APScheduler CronTrigger kwargs；None 表示事件触发
    timeout_minutes            进程级超时（装饰器强杀子进程）
    retry_times                失败后的重试次数（不含首次执行）
    retry_interval_minutes     重试调度间隔
    downstream                 上游成功后触发的下游 task_id 列表
    trade_calendar             交易日历市场（SSE/SHFE）；配置后执行前做交易日守卫
    business_key_fn            业务周期键生成器："last_month" / "today"
    kind                       任务类型："subprocess" 表示入口本身就是 Popen 包装

runner.py 遍历此字典，动态 import callable 并用 @task 装饰后注册到调度器。
"""

from __future__ import annotations

TASK_CONFIG: dict[str, dict] = {
    # ==================== 月链：每月 2 号 03:00 触发首任务，后续事件驱动 ====================
    "crawl_16888_monthly": {
        "callable": "scheduler.jobs.crawl_16888_job:crawl_16888_monthly",
        "cron": {"day": 2, "hour": 3, "minute": 0},
        "timeout_minutes": 180,
        "retry_times": 3,
        "retry_interval_minutes": 30,
        "downstream": ["load_ods_after_crawl"],
        "business_key_fn": "last_month",
        "kind": "subprocess",
    },
    "load_ods_after_crawl": {
        "callable": "scheduler.jobs.load_ods_job:load_ods_after_crawl",
        "cron": None,
        "timeout_minutes": 60,
        "retry_times": 2,
        "retry_interval_minutes": 15,
        "downstream": ["build_sales_dimensions"],
    },
    "build_sales_dimensions": {
        "callable": "scheduler.jobs.etl_jobs:build_dimensions",
        "cron": None,
        "timeout_minutes": 30,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": ["refresh_ads_zipper"],
    },
    "refresh_ads_zipper": {
        "callable": "scheduler.jobs.etl_jobs:refresh_ads",
        "cron": None,
        "timeout_minutes": 15,
        "retry_times": 2,
        "retry_interval_minutes": 5,
        "downstream": ["run_dwd_clean"],
    },
    "run_dwd_clean": {
        "callable": "scheduler.jobs.etl_jobs:run_dwd_clean",
        "cron": None,
        "timeout_minutes": 30,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        # DWD 清洗后扇出并行：生命周期更新与 DWS 聚合互不依赖
        "downstream": ["update_lifecycle", "run_dws_agg"],
    },
    "update_lifecycle": {
        "callable": "scheduler.jobs.etl_jobs:update_lifecycle",
        "cron": None,
        "timeout_minutes": 20,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": [],
    },
    "run_dws_agg": {
        "callable": "scheduler.jobs.etl_jobs:run_dws_agg",
        "cron": None,
        "timeout_minutes": 20,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": [],
    },

    # ==================== 日链：交易日 17:30 并行 ====================
    "load_stock_daily": {
        "callable": "scheduler.jobs.load_stock_ods_job:load_stock_daily",
        "cron": {"day_of_week": "mon-fri", "hour": 17, "minute": 30},
        "timeout_minutes": 60,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": [],
        "trade_calendar": "SSE",
        "business_key_fn": "today",
    },
    "load_futures_daily": {
        "callable": "scheduler.jobs.load_futures_ods_job:load_futures_main_daily",
        "cron": {"day_of_week": "mon-fri", "hour": 17, "minute": 30},
        "timeout_minutes": 30,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": [],
        "trade_calendar": "SSE",
        "business_key_fn": "today",
    },
    "load_index_daily": {
        "callable": "scheduler.jobs.load_index_ods_job:load_ths_index_daily",
        "cron": {"day_of_week": "mon-fri", "hour": 17, "minute": 45},
        "timeout_minutes": 30,
        "retry_times": 2,
        "retry_interval_minutes": 10,
        "downstream": [],
        "trade_calendar": "SSE",
        "business_key_fn": "today",
    },

    # ==================== 财务数据：每交易日增量（非季度 cron） ====================
    "load_stock_finance": {
        "callable": "scheduler.jobs.load_stock_ods_job:load_stock_finance",
        "cron": {"day_of_week": "mon-fri", "hour": 18, "minute": 30},
        "timeout_minutes": 90,
        "retry_times": 1,
        "retry_interval_minutes": 30,
        "downstream": [],
        "trade_calendar": "SSE",
        "business_key_fn": "today",
    },
}
