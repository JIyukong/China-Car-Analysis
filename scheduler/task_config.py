"""调度任务参数配置：cron 表达式、重试策略、超时时间。

所有定时任务的调度参数集中管理，runner.py 读取此配置注册任务。
"""

# 任务调度配置
TASK_CONFIG = {
    # 每月 2 号凌晨 3 点爬取上月销量数据（16888 通常 1 号更新）
    "crawl_16888_monthly": {
        "cron": {"day": 2, "hour": 3, "minute": 0},
        "timeout_minutes": 120,             # 超时 2 小时
        "retry_times": 3,                   # 失败重试 3 次
        "retry_interval_minutes": 30,       # 重试间隔 30 分钟
    },
    # 爬取完成后 2 小时自动入库（留足爬虫执行时间）
    "load_ods_after_crawl": {
        "cron": {"day": 2, "hour": 5, "minute": 0},
        "timeout_minutes": 60,
        "retry_times": 2,
        "retry_interval_minutes": 15,
    },
    # 每月 2 号 6 点执行 DWD 清洗
    "run_dwd_clean": {
        "cron": {"day": 2, "hour": 6, "minute": 0},
        "timeout_minutes": 30,
        "retry_times": 2,
        "retry_interval_minutes": 10,
    },
}
