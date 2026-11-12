"""调度入口共用的业务日期和任务参数，可离线导入。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from scheduler.task_config import CHAINS


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


def run_chain(chain: str, period: str, *, from_csv: bool = False, step: str | None = None) -> bool:
    """兼容调用统一入口的旧代码。"""
    from scheduler.runner import main

    period = business_period(chain, period)
    chain_steps(chain, from_csv=from_csv, step=step)
    args = ["--chain", chain, "--period", period]
    if from_csv:
        args.append("--from-csv")
    if step:
        args.extend(["--step", step])
    return main(args) == 0
