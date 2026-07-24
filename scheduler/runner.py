"""APScheduler 调度器入口：注册定时任务并启动阻塞调度。

用法:
    python -m scheduler.runner
"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from base_infra.logging_config import get_logger
from scheduler.jobs.crawl_16888_job import crawl_16888_monthly
from scheduler.jobs.load_ods_job import load_ods_after_crawl
from scheduler.task_config import TASK_CONFIG

logger = get_logger(__name__)


def create_scheduler() -> BlockingScheduler:
    """创建调度器并注册定时任务。

    Returns:
        配置好任务的 BlockingScheduler 实例。
    """
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    # 任务 1：每月 2 号 3 点爬取上月 16888 销量
    cfg = TASK_CONFIG["crawl_16888_monthly"]
    scheduler.add_job(
        func=crawl_16888_monthly,
        trigger=CronTrigger(**cfg["cron"]),
        id="crawl_16888_monthly",
        name="爬取上月16888销量数据",
        misfire_grace_time=3600,          # 延迟容忍 1 小时
        max_instances=1,                  # 不允许并发执行
        coalesce=True,                    # 错过多次只执行一次
    )

    # 任务 2：爬取完成后 2 小时自动入库 ODS
    cfg = TASK_CONFIG["load_ods_after_crawl"]
    scheduler.add_job(
        func=load_ods_after_crawl,
        trigger=CronTrigger(**cfg["cron"]),
        id="load_ods_after_crawl",
        name="爬取后自动加载ODS",
        misfire_grace_time=3600,
        max_instances=1,
        coalesce=True,
    )

    # 任务 3：ODS 入库后 1 小时执行 DWD 清洗
    from pipelines.staging.clean_sales_dwd import run_clean_sales_dwd
    cfg = TASK_CONFIG["run_dwd_clean"]
    scheduler.add_job(
        func=run_clean_sales_dwd,
        trigger=CronTrigger(**cfg["cron"]),
        id="run_dwd_clean",
        name="DWD销量数据清洗",
        misfire_grace_time=3600,
        max_instances=1,
        coalesce=True,
    )

    return scheduler


def main() -> None:
    """调度器启动入口。                                   """
    logger.info("调度器启动...")
    sched = create_scheduler()

    # 打印已注册任务
    for job in sched.get_jobs():
        logger.info("已注册任务: %s → %s", job.id, job.trigger)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
