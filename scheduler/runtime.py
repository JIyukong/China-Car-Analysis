"""调度器运行时核心：任务装饰器、进程级超时、重试、依赖触发、恢复与心跳。

对应方案文档 ``.trae/documents/scheduler_refactor_plan.md`` 3.3.1 节。

公开 API：
    - :class:`TaskResult`       业务函数返回值
    - :class:`SkipTask`         跳过执行（非交易日等），不告警
    - :func:`task`              任务装饰器
    - :func:`run_recovery`      启动恢复
    - :func:`start_heartbeat_thread`  心跳守护线程
    - :func:`is_trade_day`      交易日历守卫
    - :func:`build_business_key`      业务周期键
    - :data:`scheduler_holder`  runner 注入 BlockingScheduler 的容器
    - :data:`TASK_REGISTRY`     task_id -> 已装饰 callable
"""

from __future__ import annotations

import contextlib
import importlib
import multiprocessing
import os
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import text

from base_infra.db import engine as _engine
from base_infra.logging_config import get_logger
from scheduler import alert

logger = get_logger(__name__)

# 业务函数在子进程执行时，若超过该秒数仍未向 queue 投递结果，视为超时
# （装饰器内部使用，配置里的 timeout_minutes 会换算到这里）
_JOIN_GRACE_SEC = 5


# ---------------------------------------------------------------------------
# 公共数据结构
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """业务函数返回值。老任务返回 True/False/None 也会被归一化为此类型。"""

    success: bool
    affected_rows: int | None = None
    message: str = ""
    error_type: str | None = None


class SkipTask(Exception):
    """业务函数主动抛出，记 ``status='skipped'``，不告警不重试。"""


class _WorkerError(Exception):
    """子进程业务异常包装，携带原始错误类型名和 traceback。"""

    def __init__(self, error_type: str, message: str, tb: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.traceback_str = tb


# ---------------------------------------------------------------------------
# 调度器引用与任务注册表
# ---------------------------------------------------------------------------

class SchedulerHolder:
    """runner 创建 BlockingScheduler 后赋值给 ``scheduler_holder.scheduler``。"""

    scheduler: Any = None


scheduler_holder = SchedulerHolder()

# task_id -> 被 @task 装饰后的 wrapper；下游触发从这里取可调用对象
TASK_REGISTRY: dict[str, Callable[..., Any]] = {}


# ---------------------------------------------------------------------------
# 子进程入口（必须模块级，Windows spawn 需要可 pickle）
# ---------------------------------------------------------------------------

def _worker_entry(
    result_queue: "multiprocessing.Queue",
    func: Callable[..., Any],
    args: tuple,
    kwargs: dict,
) -> None:
    """在子进程中执行业务函数，把 (ok, payload) 投递回主进程。"""
    # 子进程重新配置日志（spawn 后 handler 未继承）
    try:
        from base_infra.logging_config import setup_logging
        setup_logging()
    except Exception:  # noqa: BLE001
        pass
    try:
        result = func(*args, **kwargs)
        result_queue.put(("ok", result))
    except BaseException as exc:  # noqa: BLE001 - 需要把任意异常回传
        result_queue.put(("err", type(exc).__name__, str(exc), traceback.format_exc()))


def _kill_process_tree(pid: int) -> None:
    """强杀进程及其子孙进程（含 Playwright/Chromium 孙进程）。"""
    if sys.platform == "win32":
        # /T 连同子进程树一起终止，/F 强制
        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("taskkill 失败 pid=%s err=%s", pid, exc)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            logger.warning("killpg 失败 pid=%s err=%s", pid, exc)


def _run_in_subprocess(
    func: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    timeout_sec: float,
) -> TaskResult:
    """用独立子进程运行业务函数，超时强杀进程树。"""
    # 统一用 spawn：Windows 必须，POSIX 下也避免 fork 继承 SQLAlchemy 连接池
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    preexec_fn = None if sys.platform == "win32" else os.setsid
    proc = ctx.Process(
        target=_worker_entry,
        args=(result_queue, func, args, kwargs),
    )
    proc.start()
    proc.join(timeout_sec)

    if proc.is_alive():
        logger.warning("任务子进程超时，强杀进程树 pid=%s", proc.pid)
        _kill_process_tree(proc.pid)
        proc.join(_JOIN_GRACE_SEC)
        # queue 此时大概率没有结果
        with contextlib.suppress(Exception):
            result_queue.close()
        if proc.exitcode is None:
            logger.error("子进程强杀后仍在运行 pid=%s", proc.pid)
        raise TimeoutError(f"任务执行超过 {timeout_sec:.0f} 秒，子进程已被强杀")

    # 进程异常退出（非 0 且未投递结果）：构造明确错误，避免上层拿到空结果
    if proc.exitcode not in (0, None) and result_queue.empty():
        with contextlib.suppress(Exception):
            result_queue.close()
        raise _WorkerError(
            "ChildProcessError",
            f"子进程异常退出 exitcode={proc.exitcode}",
            f"子进程异常退出 exitcode={proc.exitcode}",
        )

    # 进程已退出，读取结果（非阻塞，理论上一定有）
    try:
        payload = result_queue.get(timeout=5)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"子进程未返回结果，exitcode={proc.exitcode}") from exc
    finally:
        with contextlib.suppress(Exception):
            result_queue.close()

    tag = payload[0]
    if tag == "err":
        # 子进程业务异常：保留错误类型，包装成 _WorkerError 上交
        _, err_type, err_msg, err_tb = payload
        if err_type == "SkipTask":
            raise SkipTask(err_msg)
        raise _WorkerError(err_type, err_msg, err_tb)
    return _normalize_result(payload[1])


def _normalize_result(value: Any) -> TaskResult:
    """把业务函数返回值统一为 TaskResult。"""
    if isinstance(value, TaskResult):
        return value
    if value is None:
        return TaskResult(success=True)
    if isinstance(value, bool):
        return TaskResult(success=value)
    # 其它真值默认视为成功
    return TaskResult(success=True, message=str(value))


# ---------------------------------------------------------------------------
# 日志表读写辅助
# ---------------------------------------------------------------------------

def _insert_run_log(
    conn: Any,
    *,
    run_id: str,
    task_id: str,
    trigger_type: str,
    retry_no: int,
    business_key: str | None,
    parent_run_id: str | None,
    note: str | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO scheduler_run_log
                (run_id, task_id, business_key, trigger_type, parent_run_id,
                 status, hostname, pid, started_at, retry_no, error_msg)
            VALUES
                (:run_id, :task_id, :business_key, :trigger_type, :parent_run_id,
                 'running', :hostname, :pid, NOW(6), :retry_no, :note)
            """
        ),
        {
            "run_id": run_id,
            "task_id": task_id,
            "business_key": business_key,
            "trigger_type": trigger_type,
            "parent_run_id": parent_run_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "retry_no": retry_no,
            "note": note,
        },
    )


def _update_status(
    conn: Any,
    *,
    run_id: str,
    status: str,
    started_at: datetime | None = None,
    affected_rows: int | None = None,
    error_type: str | None = None,
    error_msg: str | None = None,
) -> None:
    """更新运行日志状态。duration_sec 由 MySQL 端用时间戳差计算。"""
    sets = ["status = :status", "finished_at = NOW(6)"]
    if started_at is not None:
        sets.append("duration_sec = TIMESTAMPDIFF(SECOND, :started_at, NOW(6))")
    if affected_rows is not None:
        sets.append("affected_rows = :affected_rows")
    if error_type is not None:
        sets.append("error_type = :error_type")
    if error_msg is not None:
        sets.append("error_msg = LEFT(:error_msg, 4000)")
    sql = f"UPDATE scheduler_run_log SET {', '.join(sets)} WHERE run_id = :run_id"
    conn.execute(
        text(sql),
        {
            "status": status,
            "run_id": run_id,
            "started_at": started_at,
            "affected_rows": affected_rows,
            "error_type": error_type,
            "error_msg": error_msg,
        },
    )


def _last_final_status(conn: Any, task_id: str, current_run_id: str | None = None) -> str | None:
    """返回该 task_id 最近一次终态（排除 running/retrying 以及当前运行记录）。

    用于判断"上一次是否 failed"以决定是否发恢复通知。需要在 UPDATE 当前行
    为 success 之后、同一事务里调用，因此显式排除当前 ``run_id``。
    """
    row = conn.execute(
        text(
            """
            SELECT status FROM scheduler_run_log
            WHERE task_id = :tid
              AND status NOT IN ('running','retrying')
              AND (:rid IS NULL OR run_id <> :rid)
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"tid": task_id, "rid": current_run_id},
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# 交易日历与业务周期键
# ---------------------------------------------------------------------------

def is_trade_day(date_: date, market: str = "SSE") -> bool:
    """查询 dim_trade_calendar；表无记录则告警并返回 True（避免阻塞日链）。"""
    with _engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_open FROM dim_trade_calendar "
                "WHERE trade_date = :d AND market = :m"
            ),
            {"d": date_, "m": market},
        ).fetchone()
    if row is None:
        logger.warning("交易日历无 %s (%s) 记录，按交易日处理", date_, market)
        return True
    return bool(row[0])


def build_business_key(fn_name: str) -> str | None:
    """根据 business_key_fn 配置生成业务周期键。

    - ``last_month`` → 上月 YYYY-MM
    - ``today``      → 今天 YYYY-MM-DD
    - 其它值         → None
    """
    today = date.today()
    if fn_name == "last_month":
        first_of_this = today.replace(day=1)
        last_month_end = first_of_this - timedelta(days=1)
        return last_month_end.strftime("%Y-%m")
    if fn_name == "today":
        return today.strftime("%Y-%m-%d")
    return None


# ---------------------------------------------------------------------------
# @task 装饰器
# ---------------------------------------------------------------------------

def task(
    task_id: str,
    config: dict,
    scheduler_ref_holder: SchedulerHolder | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """把业务函数包裹为带日志/超时/重试/下游触发的调度 job。

    :param task_id: 任务标识，与 task_config 键一致
    :param config: 任务配置 dict
    :param scheduler_ref_holder: 含 scheduler 属性的容器；为 None 用全局 holder
    """
    holder = scheduler_ref_holder or scheduler_holder

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> None:
            # 提取内部控制参数（不传给业务函数）
            retry_no = int(kwargs.pop("_retry_no", 0) or 0)
            parent_run_id = kwargs.pop("_parent_run_id", None)
            explicit_trigger = kwargs.pop("_trigger_type", None)
            existing_run_id = kwargs.pop("_run_id", None)
            recovery_note = kwargs.pop("_recovery_note", None)
            business_key = kwargs.pop("business_key", None)

            if explicit_trigger:
                trigger_type = explicit_trigger
            elif retry_no > 0:
                trigger_type = "retry"
            elif parent_run_id:
                trigger_type = "dependency"
            else:
                trigger_type = "cron"

            # 未显式传 business_key 时按配置生成
            if not business_key:
                bk_fn = config.get("business_key_fn")
                if bk_fn:
                    business_key = build_business_key(bk_fn)

            run_id = existing_run_id or uuid.uuid4().hex
            started_at = datetime.now()
            hostname = socket.gethostname()
            pid = os.getpid()

            logger.info(
                "任务开始 task=%s run=%s trigger=%s retry=%s bk=%s",
                task_id, run_id, trigger_type, retry_no, business_key,
            )

            # 交易日守卫
            market = config.get("trade_calendar")
            if market and not is_trade_day(date.today(), market):
                _write_skipped(run_id, task_id, trigger_type, retry_no,
                               business_key, parent_run_id,
                               hostname, pid, "非交易日跳过", recovery_note)
                return

            # 写 running 日志
            with _engine.begin() as conn:
                _insert_run_log(
                    conn,
                    run_id=run_id,
                    task_id=task_id,
                    trigger_type=trigger_type,
                    retry_no=retry_no,
                    business_key=business_key,
                    parent_run_id=parent_run_id,
                    note=recovery_note,
                )

            # 执行（异常归一化）
            timeout_sec = float(config.get("timeout_minutes", 60)) * 60.0
            try:
                result = _run_in_subprocess(func, args, kwargs, timeout_sec)
                if not result.success:
                    # 业务函数返回 TaskResult(success=False)：保留其 error_type
                    raise _WorkerError(
                        result.error_type or "TaskFailed",
                        result.message or "TaskResult.success=False",
                        result.message or "TaskResult.success=False",
                    )
            except SkipTask as exc:
                with _engine.begin() as conn:
                    _update_status(
                        conn, run_id=run_id, status="skipped",
                        started_at=started_at, error_type="SkipTask",
                        error_msg=str(exc),
                    )
                logger.info("任务跳过 task=%s run=%s reason=%s",
                            task_id, run_id, exc)
                return
            except TimeoutError as exc:
                logger.error("任务超时 task=%s run=%s", task_id, run_id)
                _handle_failure(
                    holder, config, task_id, run_id, started_at,
                    business_key, parent_run_id, retry_no, args, kwargs,
                    error_type="TimeoutError", error_msg=str(exc),
                )
                return
            except BaseException as exc:  # noqa: BLE001
                # 子进程业务异常：保留原错误类型与 traceback，便于告警归类
                if isinstance(exc, _WorkerError):
                    err_type = exc.error_type
                    tb = exc.traceback_str or str(exc)
                else:
                    err_type = type(exc).__name__
                    tb = traceback.format_exc()
                logger.error("任务异常 task=%s run=%s type=%s: %s",
                             task_id, run_id, err_type, exc)
                logger.error(tb)
                _handle_failure(
                    holder, config, task_id, run_id, started_at,
                    business_key, parent_run_id, retry_no, args, kwargs,
                    error_type=err_type, error_msg=tb[-4000:],
                )
                return

            # 成功
            with _engine.begin() as conn:
                _update_status(
                    conn, run_id=run_id, status="success",
                    started_at=started_at,
                    affected_rows=result.affected_rows,
                    error_msg=result.message or None,
                )
                prev_status = _last_final_status(conn, task_id, current_run_id=run_id)
            logger.info(
                "任务成功 task=%s run=%s rows=%s",
                task_id, run_id, result.affected_rows,
            )

            # 失败转成功的恢复通知
            if prev_status == "failed":
                alert.send_alert(
                    "INFO", task_id, run_id, "任务已恢复成功",
                    business_key=business_key,
                    previous_status=prev_status,
                    affected_rows=result.affected_rows,
                )

            # 触发下游
            _trigger_downstream(
                holder, config, task_id, run_id, business_key,
            )

        # 保留原函数名，便于 APScheduler 按 "module:func" 解析与 pickle
        wrapper.__name__ = func.__name__
        wrapper.__qualname__ = func.__name__
        wrapper.__module__ = func.__module__
        wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        wrapper.task_id = task_id  # type: ignore[attr-defined]
        TASK_REGISTRY[task_id] = wrapper
        return wrapper

    return decorator


def _write_skipped(
    run_id: str,
    task_id: str,
    trigger_type: str,
    retry_no: int,
    business_key: str | None,
    parent_run_id: str | None,
    hostname: str,
    pid: int,
    reason: str,
    note: str | None,
) -> None:
    """交易日守卫命中：直接写一条 skipped 记录（先 running 再 update 也行，
    但 skipped 语义清晰，单独 UPSERT 减少一次写库）。"""
    with _engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO scheduler_run_log
                    (run_id, task_id, business_key, trigger_type, parent_run_id,
                     status, hostname, pid, started_at, finished_at,
                     duration_sec, retry_no, error_msg)
                VALUES
                    (:run_id, :task_id, :business_key, :trigger_type, :parent_run_id,
                     'skipped', :hostname, :pid, NOW(6), NOW(6), 0, :retry_no, :msg)
                """
            ),
            {
                "run_id": run_id,
                "task_id": task_id,
                "business_key": business_key,
                "trigger_type": trigger_type,
                "parent_run_id": parent_run_id,
                "hostname": hostname,
                "pid": pid,
                "retry_no": retry_no,
                "msg": note or reason,
            },
        )


def _handle_failure(
    holder: SchedulerHolder,
    config: dict,
    task_id: str,
    run_id: str,
    started_at: datetime,
    business_key: str | None,
    parent_run_id: str | None,
    retry_no: int,
    args: tuple,
    kwargs: dict,
    *,
    error_type: str,
    error_msg: str,
) -> None:
    """失败处理：判断是否重试，否则最终失败告警。"""
    max_retry = int(config.get("retry_times", 0) or 0)
    interval = int(config.get("retry_interval_minutes", 10) or 10)

    if retry_no < max_retry:
        next_retry = retry_no + 1
        with _engine.begin() as conn:
            _update_status(
                conn, run_id=run_id, status="retrying",
                started_at=started_at, error_type=error_type,
                error_msg=error_msg,
            )
        _schedule_retry(
            holder, config, task_id, run_id, business_key,
            next_retry, interval, args, kwargs,
        )
        alert.send_alert(
            "INFO", task_id, run_id,
            f"第 {next_retry} 次重试已调度（{interval} 分钟后）",
            error_type=error_type,
            error_msg=error_msg[-500:],
            business_key=business_key,
        )
        return

    # 重试耗尽
    with _engine.begin() as conn:
        _update_status(
            conn, run_id=run_id, status="failed",
            started_at=started_at, error_type=error_type,
            error_msg=error_msg,
        )
    logger.error("任务最终失败 task=%s run=%s type=%s", task_id, run_id, error_type)
    alert.send_alert(
        "ERROR", task_id, run_id, "任务最终失败（重试耗尽）",
        error_type=error_type,
        error_msg=error_msg[-2000:],
        business_key=business_key,
        parent_run_id=parent_run_id,
    )


def _schedule_retry(
    holder: SchedulerHolder,
    config: dict,
    task_id: str,
    run_id: str,
    business_key: str | None,
    next_retry: int,
    interval_minutes: int,
    args: tuple,
    kwargs: dict,
) -> None:
    """通过 APScheduler 重新调度一次。不 sleep，立即返回。"""
    scheduler = getattr(holder, "scheduler", None)
    if scheduler is None:
        logger.error("scheduler 未注入，无法调度重试 task=%s run=%s", task_id, run_id)
        return

    wrapper = TASK_REGISTRY.get(task_id) or _resolve_callable(config.get("callable"))
    if wrapper is None:
        logger.error("无法解析重试 callable task=%s", task_id)
        return

    run_date = datetime.now() + timedelta(minutes=interval_minutes)
    job_id = f"retry::{task_id}::{run_id}::{next_retry}"
    retry_kwargs = dict(kwargs)
    retry_kwargs.update(
        business_key=business_key,
        _retry_no=next_retry,
        _parent_run_id=run_id,
        _trigger_type="retry",
    )
    scheduler.add_job(
        func=wrapper,
        trigger="date",
        run_date=run_date,
        id=job_id,
        replace_existing=False,
        args=list(args),
        kwargs=retry_kwargs,
        misfire_grace_time=None,
        max_instances=1,
        coalesce=True,
    )
    logger.warning(
        "已调度重试 task=%s run=%s retry_no=%s at=%s",
        task_id, run_id, next_retry, run_date,
    )


def _trigger_downstream(
    holder: SchedulerHolder,
    config: dict,
    task_id: str,
    run_id: str,
    business_key: str | None,
) -> None:
    """上游成功后触发下游任务；按 parent_run_id 做幂等判断。"""
    downstream = config.get("downstream") or []
    if not downstream:
        return

    scheduler = getattr(holder, "scheduler", None)
    if scheduler is None:
        logger.error("scheduler 未注入，无法触发下游 task=%s", task_id)
        return

    for ds_id in downstream:
        try:
            with _engine.connect() as conn:
                exists = conn.execute(
                    text(
                        """
                        SELECT 1 FROM scheduler_run_log
                        WHERE task_id = :tid
                          AND parent_run_id = :prid
                          AND status IN ('success','running','retrying')
                        LIMIT 1
                        """
                    ),
                    {"tid": ds_id, "prid": run_id},
                ).fetchone()
            if exists:
                logger.info("下游已存在运行记录，跳过 task=%s parent=%s", ds_id, run_id)
                continue

            ds_wrapper = TASK_REGISTRY.get(ds_id)
            if ds_wrapper is None:
                # 尝试按 callable 字符串解析并装饰（runner 未注册时兜底）
                ds_cfg = _get_task_config(ds_id)
                if ds_cfg and ds_cfg.get("callable"):
                    ds_wrapper = _resolve_callable(ds_cfg["callable"])
            if ds_wrapper is None:
                logger.error("下游任务未注册，无法触发 ds=%s parent=%s", ds_id, run_id)
                continue

            ds_job_id = f"{ds_id}::{run_id}"
            scheduler.add_job(
                func=ds_wrapper,
                trigger="date",
                run_date=datetime.now(),
                id=ds_job_id,
                replace_existing=False,
                kwargs={
                    "business_key": business_key,
                    "_parent_run_id": run_id,
                    "_trigger_type": "dependency",
                },
                misfire_grace_time=None,
                max_instances=1,
                coalesce=True,
            )
            logger.info("已触发下游任务 ds=%s parent=%s", ds_id, run_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("触发下游失败 ds=%s parent=%s err=%s", ds_id, run_id, exc)


def _resolve_callable(callable_path: str | None) -> Callable[..., Any] | None:
    """按 ``"module:function"`` 解析 callable；若未被装饰则自动装饰。"""
    if not callable_path or ":" not in callable_path:
        return None
    module_name, func_name = callable_path.split(":", 1)
    try:
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        logger.error("解析 callable 失败 path=%s err=%s", callable_path, exc)
        return None
    # 若已经是装饰过的 wrapper（在 TASK_REGISTRY 中），直接返回
    for wrapped in TASK_REGISTRY.values():
        if getattr(wrapped, "__wrapped__", None) is func:
            return wrapped
    return func


# ---------------------------------------------------------------------------
# 任务配置的延迟加载（避免 runner → runtime → task_config 循环）
# ---------------------------------------------------------------------------

def _get_task_config(task_id: str) -> dict | None:
    try:
        from scheduler.task_config import TASK_CONFIG
        return TASK_CONFIG.get(task_id)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 启动恢复
# ---------------------------------------------------------------------------

def run_recovery(scheduler: Any) -> None:
    """调度器启动时调用：清扫僵尸、初始化心跳、补触发依赖缺口。"""
    logger.info("开始执行启动恢复 ...")
    hostname = socket.gethostname()
    pid = os.getpid()

    # 1. 清扫僵尸 running/retrying
    with _engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE scheduler_run_log
                SET status = 'killed',
                    finished_at = NOW(6),
                    duration_sec = TIMESTAMPDIFF(SECOND, started_at, NOW(6)),
                    error_msg = '调度器重启时清扫为未完成状态'
                WHERE status IN ('running','retrying')
                """
            )
        )
        killed = result.rowcount or 0

        # 2. UPSERT 心跳行
        version = _read_version()
        conn.execute(
            text(
                """
                INSERT INTO scheduler_heartbeat
                    (id, hostname, pid, last_heartbeat, started_at, version)
                VALUES
                    (1, :hostname, :pid, NOW(6), NOW(6), :version)
                ON DUPLICATE KEY UPDATE
                    hostname = VALUES(hostname),
                    pid = VALUES(pid),
                    last_heartbeat = NOW(6),
                    started_at = NOW(6),
                    version = VALUES(version)
                """
            ),
            {"hostname": hostname, "pid": pid, "version": version},
        )
    logger.info("僵尸运行记录清扫完成 killed=%s", killed)

    # 3. 依赖缺口扫描（失败不阻断启动）
    try:
        _scan_dependency_gaps(scheduler)
    except Exception as exc:  # noqa: BLE001
        logger.exception("依赖缺口扫描失败: %s", exc)

    logger.info("启动恢复完成")


def _read_version() -> str:
    """读取应用版本；找不到 carapp 包时回退 0.1.0。"""
    try:
        import carapp  # type: ignore
        return getattr(carapp, "__version__", "0.1.0")
    except Exception:  # noqa: BLE001
        return "0.1.0"


def _scan_dependency_gaps(scheduler: Any) -> None:
    """扫描最近 24 小时 success 但下游缺口，重新 add_job 补触发。"""
    try:
        from scheduler.task_config import TASK_CONFIG
    except Exception:  # noqa: BLE001
        logger.warning("task_config 不可用，跳过依赖缺口扫描")
        return

    since = datetime.now() - timedelta(hours=24)
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT run_id, task_id, business_key
                FROM scheduler_run_log
                WHERE status = 'success'
                  AND started_at >= :since
                ORDER BY started_at ASC
                """
            ),
            {"since": since},
        ).fetchall()

    triggered = 0
    for run_id, task_id, business_key in rows:
        cfg = TASK_CONFIG.get(task_id)
        if not cfg:
            continue
        for ds_id in cfg.get("downstream") or []:
            with _engine.connect() as conn:
                gap = conn.execute(
                    text(
                        """
                        SELECT 1 FROM scheduler_run_log
                        WHERE task_id = :tid
                          AND parent_run_id = :prid
                          AND status IN ('success','running','retrying')
                        LIMIT 1
                        """
                    ),
                    {"tid": ds_id, "prid": run_id},
                ).fetchone()
            if gap:
                continue

            ds_wrapper = TASK_REGISTRY.get(ds_id)
            if ds_wrapper is None:
                logger.warning("recovery: 下游 %s 未注册，跳过", ds_id)
                continue
            try:
                scheduler.add_job(
                    func=ds_wrapper,
                    trigger="date",
                    run_date=datetime.now(),
                    id=f"{ds_id}::{run_id}",
                    replace_existing=False,
                    kwargs={
                        "business_key": business_key,
                        "_parent_run_id": run_id,
                        "_trigger_type": "dependency",
                        "_recovery_note": "recovery 补触发",
                    },
                    misfire_grace_time=None,
                    max_instances=1,
                    coalesce=True,
                )
                triggered += 1
                logger.warning("recovery 补触发 downstream=%s parent=%s", ds_id, run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("recovery 补触发失败 ds=%s err=%s", ds_id, exc)
    logger.info("依赖缺口扫描完成，补触发 %s 个下游", triggered)


# ---------------------------------------------------------------------------
# 心跳线程
# ---------------------------------------------------------------------------

def start_heartbeat_thread() -> None:
    """启动 daemon 线程，每 30 秒更新一次 scheduler_heartbeat。"""

    def _beat() -> None:
        hostname = socket.gethostname()
        pid = os.getpid()
        while True:
            try:
                with _engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE scheduler_heartbeat
                            SET last_heartbeat = NOW(6),
                                hostname = :hostname,
                                pid = :pid
                            WHERE id = 1
                            """
                        ),
                        {"hostname": hostname, "pid": pid},
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("心跳更新失败: %s", exc)
            time.sleep(30)

    t = threading.Thread(target=_beat, name="scheduler-heartbeat", daemon=True)
    t.start()
    logger.info("心跳线程已启动")
