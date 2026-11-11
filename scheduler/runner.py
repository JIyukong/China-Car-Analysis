"""APScheduler 调度器入口：注册任务、启动阻塞调度、启动恢复与心跳。

用法:
    python -m scheduler.runner
"""

from __future__ import annotations

import importlib
import socket

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from base_infra.config import get_settings
from base_infra.logging_config import get_logger
from scheduler.alert import send_alert
from scheduler.runtime import (
    run_recovery,
    scheduler_holder,
    start_heartbeat_thread,
    task as task_decorator,
)
from scheduler.task_config import TASK_CONFIG

logger = get_logger(__name__)

# APScheduler 3.x 中事件触发的依赖任务通过 add_job 动态注册，
# 给一个 24 小时的 misfire 宽限，避免调度器瞬时繁忙导致依赖任务被误判过期。
# 注意：runtime.py 中动态 add_job 传 misfire_grace_time=None，在 3.x 语义是
# "完全跳过 misfire 检查（永不过期）"，而非"当作 0 立即过期"。
_MISFIRE_GRACE_SEC = 24 * 3600


def _resolve_callable(dotted: str):
    """按 ``"module:function"`` 动态导入业务 callable。"""
    module_name, func_name = dotted.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _on_scheduler_event(event) -> None:
    """APScheduler 级事件（misfire / max_instances / executor error）告警。

    事件回调内绝不允许抛出异常，否则会污染 APScheduler 事件循环。
    """
    try:
        name = {
            EVENT_JOB_ERROR: "JOB_ERROR",
            EVENT_JOB_MISSED: "JOB_MISSED",
            EVENT_JOB_MAX_INSTANCES: "JOB_MAX_INSTANCES",
        }.get(event.code, str(event.code))
        msg = (
            f"调度事件 {name}: job_id={getattr(event, 'job_id', '?')} "
            f"executor={getattr(event, 'executor', '?')} "
            f"exc={getattr(event, 'exception', '')}"
        )
        logger.warning(msg)
        send_alert("WARNING", "scheduler", "-", msg)
    except Exception:  # noqa: BLE001 - 监听器不得抛错
        logger.exception("调度事件回调自身异常")


def create_scheduler() -> BlockingScheduler:
    """创建 BlockingScheduler 并按 TASK_CONFIG 注册全部任务。"""
    settings = get_settings()
    scheduler = BlockingScheduler(
        timezone="Asia/Shanghai",
        jobstores={"default": SQLAlchemyJobStore(url=settings.database_url)},
        job_defaults={
            "misfire_grace_time": _MISFIRE_GRACE_SEC,
            "coalesce": True,
            "max_instances": 1,
        },
    )

    for task_id, cfg in TASK_CONFIG.items():
        raw_func = _resolve_callable(cfg["callable"])
        wrapped = task_decorator(task_id, cfg)(raw_func)
        # TASK_REGISTRY 由装饰器自动写入；wrapped 即注册后的可调用对象
        cron_cfg = cfg.get("cron")
        if cron_cfg:
            scheduler.add_job(
                wrapped,
                CronTrigger(**cron_cfg),
                id=task_id,
                name=task_id,
                replace_existing=True,
            )
            logger.info("注册 cron 任务: %s → %s", task_id, cron_cfg)
        else:
            logger.info("注册事件触发任务: %s（无 cron，由上游触发）", task_id)

    scheduler.add_listener(
        _on_scheduler_event,
        EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES,
    )
    scheduler_holder.scheduler = scheduler
    return scheduler


def main() -> None:
    """调度器启动入口。"""
    logger.info("调度器启动（host=%s）...", socket.gethostname())
    scheduler = create_scheduler()

    for job in scheduler.get_jobs():
        logger.info("已注册: %s → %s", job.id, job.trigger)

    # 启动前 recovery + 心跳：清扫僵尸记录、补触发缺口、初始化心跳行
    run_recovery(scheduler)
    start_heartbeat_thread()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
