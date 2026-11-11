"""定时启动或手动运行同一套 ETL。"""

import argparse
import subprocess
import sys
from pathlib import Path

from scheduler.runtime import business_period, chain_steps, run_chain
from scheduler.task_config import CHAINS, DAILY_TIMES, MONTHLY_CRON

ROOT = Path(__file__).resolve().parents[1]
TIMEZONE = "Asia/Shanghai"


def print_plan(chain: str | None = None, period: str | None = None, *, from_csv=False, step=None) -> None:
    for name in (chain,) if chain else CHAINS:
        steps = chain_steps(name, from_csv=from_csv, step=step)
        label = f"{name} {period}" if period else name
        print(f"{label}: {' -> '.join(steps)}")
        if not chain:
            cron = MONTHLY_CRON.get(name)
            if cron:
                print(f"  北京时间每月 {cron['day']} 日 {cron['hour']:02}:{cron['minute']:02}")
            else:
                for task, (hour, minute) in DAILY_TIMES.items():
                    print(f"  北京时间工作日 {hour:02}:{minute:02} {task}")


def run_scheduled(chain: str, step: str | None = None) -> None:
    from scheduler.alert import send_alert

    period = business_period(chain, None)
    args = [sys.executable, "-m", "scheduler.runner", "--chain", chain, "--period", period]
    if step:
        args.extend(["--step", step])
    timeout_seconds = 7200 if chain == "daily" else 21600
    try:
        result = subprocess.run(args, cwd=ROOT, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        send_alert("ERROR", step or chain, period, "ETL 执行超时，请查看日志")
        raise
    if result.returncode:
        send_alert("ERROR", step or chain, period, f"ETL 执行失败，退出码 {result.returncode}，请查看日志")


def create_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(
        timezone=TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    for chain, cron in MONTHLY_CRON.items():
        scheduler.add_job(run_scheduled, "cron", args=(chain,), id=chain, **cron)
    for step, (hour, minute) in DAILY_TIMES.items():
        scheduler.add_job(
            run_scheduled,
            "cron",
            args=("daily", step),
            id=step,
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
        )
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CarApp 采集与 ETL")
    parser.add_argument("--chain", choices=CHAINS)
    parser.add_argument("--period", help="月链 YYYY-MM，日链 YYYY-MM-DD")
    parser.add_argument("--from-csv", action="store_true", help="用目标月的本地 CSV，跳过爬取")
    parser.add_argument("--step", choices=DAILY_TIMES, help="只运行一项独立的日链任务")
    parser.add_argument("--plan", action="store_true", help="离线预览步骤")
    args = parser.parse_args(argv)

    if not args.chain and (args.period or args.from_csv or args.step):
        parser.error("--period、--from-csv、--step 需要 --chain")
    try:
        period = business_period(args.chain, args.period) if args.chain else None
        if args.chain:
            chain_steps(args.chain, from_csv=args.from_csv, step=args.step)
    except ValueError as exc:
        parser.error(str(exc))

    if args.plan:
        print_plan(args.chain, period, from_csv=args.from_csv, step=args.step)
        return 0

    from base_infra.logging_config import get_logger

    logger = get_logger(__name__)
    if args.chain:
        try:
            return 0 if run_chain(args.chain, period, from_csv=args.from_csv, step=args.step) else 1
        except Exception:
            logger.exception("ETL 启动失败")
            return 1

    scheduler = create_scheduler()
    for job in scheduler.get_jobs():
        logger.info("定时任务 %s: %s", job.id, job.trigger)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
