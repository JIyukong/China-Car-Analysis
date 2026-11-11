"""ETL 按声明顺序运行。此模块可离线导入和预览。"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib import import_module
from zoneinfo import ZoneInfo

from scheduler.task_config import CHAINS, STEPS

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    success: bool
    affected_rows: int | None = None
    message: str = ""
    error_type: str | None = None


class SkipTask(Exception):
    """数据尚未发布或前置条件未就绪。"""


class AlreadyLoaded(SkipTask):
    """目标月已入 ODS，可从下一阶段继续。"""


def business_period(chain: str, period: str | None) -> str:
    if period is None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if chain == "daily":
            return today.isoformat()
        return (today.replace(day=1) - timedelta(days=1)).isoformat()[:7]

    if chain == "daily":
        parsed = date.fromisoformat(period).isoformat()
    else:
        parsed = date.fromisoformat(f"{period}-01").isoformat()[:7]
    if parsed != period:
        raise ValueError("日链使用 YYYY-MM-DD，月链使用 YYYY-MM")
    return parsed


def chain_steps(chain: str, *, from_csv: bool = False, step: str | None = None) -> tuple[str, ...]:
    steps = CHAINS[chain]
    if from_csv:
        if chain == "daily":
            raise ValueError("--from-csv 只用于 style 和 city 月链")
        steps = steps[1:]
    if step is not None:
        if chain != "daily" or step not in steps:
            raise ValueError("--step 只能指定日链中的一项任务")
        return (step,)
    return steps


def is_trade_day(day: date) -> bool:
    from sqlalchemy import text

    from base_infra.db import engine

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT is_open FROM dim_trade_calendar WHERE trade_date = :day AND market = 'SSE'"),
            {"day": day},
        ).first()
    if row is None:
        logger.warning("交易日历缺少 %s，按工作日判断", day)
        return day.weekday() < 5
    return bool(row[0])


def run_chain(chain: str, period: str, *, from_csv: bool = False, step: str | None = None) -> bool:
    period = business_period(chain, period)
    steps = chain_steps(chain, from_csv=from_csv, step=step)
    if chain == "daily" and not is_trade_day(date.fromisoformat(period)):
        logger.info("%s 非交易日，跳过日链", period)
        return True

    success = True
    skip_ods = False
    for task in steps:
        if skip_ods:
            skip_ods = False
            continue

        module, name = STEPS[task].split(":")
        logger.info("开始 %s %s", task, period)
        try:
            execute = getattr(import_module(f"scheduler.jobs.{module}"), name)
            result = execute(business_key=period)
            if isinstance(result, TaskResult):
                if not result.success:
                    raise RuntimeError(result.message or result.error_type or task)
                logger.info("完成 %s %s %s", task, period, result.message)
            elif result is False:
                raise RuntimeError(f"{task} 返回失败")
            else:
                logger.info("完成 %s %s", task, period)
        except AlreadyLoaded as exc:
            logger.info("%s", exc)
            skip_ods = True
        except SkipTask as exc:
            logger.info("跳过 %s %s: %s", task, period, exc)
            if chain != "daily":
                return not from_csv
        except Exception:
            logger.exception("失败 %s %s", task, period)
            success = False
            if chain != "daily":
                break
    return success
