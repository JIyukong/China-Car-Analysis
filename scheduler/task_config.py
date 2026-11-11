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

月链数据发布节奏（业务事实）：
    - 车型月销量（style）：每月 12~15 号发布上月数据
    - 城市车型月销量（city）：每月 20~25 号发布上月数据

故拆为两条独立月链，cron 在各自窗口内每日探测：未发布由 crawl 任务抛
SkipTask，次日再探；已发布则事件驱动整条链。

runner.py 遍历此字典，动态 import callable 并用 @task 装饰后注册到调度器。
"""

from __future__ import annotations

TASK_CONFIG: dict[str, dict] = {
    # ==================== 月链·style（车型月销量，每月 12~15 号探测） ====================
    # 根任务：窗口内每日 03:00 触发，未发布则 SkipTask，已发布则串行驱动下游
    "crawl_16888_style_monthly": {
        "callable": "scheduler.jobs.crawl_16888_job:crawl_16888_style_monthly",
        "cron": {"day": "12-15", "hour": 3, "minute": 0},
        "timeout_minutes": 180,
        "retry_times": 3,
        "retry_interval_minutes": 30,
        "downstream": ["load_style_ods"],
        "business_key_fn": "last_month",
        "kind": "subprocess",
    },
    "load_style_ods": {
        "callable": "scheduler.jobs.load_ods_job:load_style_ods",
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

    # ==================== 月链·city（城市车型月销量，每月 20~25 号探测） ====================
    # 与 style 链独立：style 链产出 dim_model 后，city DWD 清洗关联才有意义，
    # 但 city 数据发布晚 8 天，此时 style 链早已跑完，无需显式依赖。
    "crawl_16888_city_monthly": {
        "callable": "scheduler.jobs.crawl_16888_job:crawl_16888_city_monthly",
        "cron": {"day": "20-25", "hour": 3, "minute": 30},
        "timeout_minutes": 180,
        "retry_times": 3,
        "retry_interval_minutes": 30,
        "downstream": ["load_city_ods"],
        "business_key_fn": "last_month",
        "kind": "subprocess",
    },
    "load_city_ods": {
        "callable": "scheduler.jobs.load_ods_job:load_city_ods",
        "cron": None,
        "timeout_minutes": 60,
        "retry_times": 2,
        "retry_interval_minutes": 15,
        "downstream": ["run_dwd_city_clean"],
    },
    "run_dwd_city_clean": {
        "callable": "scheduler.jobs.etl_jobs:run_dwd_city_clean",
        "cron": None,
        "timeout_minutes": 30,
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
