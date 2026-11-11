"""调度器 Dry-run 空跑验证脚本。

不触达任何外部采集源/API，通过 patch ``scheduler.runtime._run_in_subprocess``
完全绕过 spawn 子进程，在主进程内验证：
    1. TASK_CONFIG 静态合法性（依赖闭环、孤立任务、cron、callable 可导入）
    2. cron 任务注册数量（应为 6：月链 style 1 + 月链 city 1 + 日链 4）
    3. 月链 DAG 依赖触发顺序与日志记录
    4. 交易日守卫 skipped 路径
    5. 失败重试调度

前置：需要一个可连通的开发/测试 MySQL 库（scheduler_run_log 等表已存在）。
仅写入 business_key LIKE 'dry-run-%' 的日志记录，结束时自动清理。

用法：
    python scripts/dry_run_scheduler.py
"""

from __future__ import annotations

import importlib
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

# 确保项目根目录在 sys.path 中（脚本位于 scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from base_infra.db import engine as _engine
from base_infra.logging_config import get_logger
from scheduler import alert
from scheduler.runtime import TaskResult, scheduler_holder, task as task_decorator
from scheduler.task_config import TASK_CONFIG

logger = get_logger(__name__)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    """记录一项检查结果。"""
    results.append((name, status, detail))
    mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[status]
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. 静态校验
# ---------------------------------------------------------------------------

def check_downstream_refs() -> None:
    """所有 downstream 引用的 task_id 必须存在。"""
    missing = []
    for tid, cfg in TASK_CONFIG.items():
        for ds in cfg.get("downstream") or []:
            if ds not in TASK_CONFIG:
                missing.append(f"{tid} -> {ds}")
    record("downstream 引用合法", FAIL if missing else PASS,
           "; ".join(missing) if missing else f"{len(TASK_CONFIG)} 个任务引用均合法")


def check_no_cycle() -> None:
    """依赖图无环（基于 downstream 边做 DFS 三色标记）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in TASK_CONFIG}

    def visit(node: str, stack: list[str]) -> list[str] | None:
        color[node] = GRAY
        for nxt in TASK_CONFIG[node].get("downstream") or []:
            if color[nxt] == GRAY:
                return stack + [node, nxt]
            if color[nxt] == WHITE:
                cyc = visit(nxt, stack + [node])
                if cyc:
                    return cyc
        color[node] = BLACK
        return None

    for tid in TASK_CONFIG:
        if color[tid] == WHITE:
            cyc = visit(tid, [])
            if cyc:
                record("依赖无环", FAIL, " -> ".join(cyc))
                return
    record("依赖无环", PASS)


def check_no_orphan() -> None:
    """有 cron 的任务是根；无 cron 的任务必须被某个 downstream 引用。"""
    referenced = set()
    for cfg in TASK_CONFIG.values():
        referenced.update(cfg.get("downstream") or [])
    orphans = [tid for tid, cfg in TASK_CONFIG.items()
               if not cfg.get("cron") and tid not in referenced]
    record("无孤立任务", FAIL if orphans else PASS,
           f"孤立: {orphans}" if orphans else "所有事件任务均被上游引用")


def check_cron_valid() -> None:
    """cron 配置可构造 CronTrigger。"""
    bad = []
    for tid, cfg in TASK_CONFIG.items():
        cron_cfg = cfg.get("cron")
        if not cron_cfg:
            continue
        try:
            CronTrigger(timezone="Asia/Shanghai", **cron_cfg)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{tid}: {exc}")
    record("cron 配置合法", FAIL if bad else PASS, "; ".join(bad) if bad else "")


def check_callable_importable() -> None:
    """callable 字符串可导入。"""
    bad = []
    for tid, cfg in TASK_CONFIG.items():
        dotted = cfg.get("callable", "")
        if ":" not in dotted:
            bad.append(f"{tid}: 格式错误 {dotted}")
            continue
        mod, fn = dotted.split(":", 1)
        try:
            module = importlib.import_module(mod)
            getattr(module, fn)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{tid}: {exc}")
    record("callable 可导入", FAIL if bad else PASS, "; ".join(bad) if bad else "")


# ---------------------------------------------------------------------------
# 2 & 3. 调度器注册 + DAG 触发
# ---------------------------------------------------------------------------

def _build_scheduler() -> BackgroundScheduler:
    """构造后台调度器并注册全部任务（MemoryJobStore，不连真实 APScheduler 表）。"""
    scheduler = BackgroundScheduler(
        timezone="Asia/Shanghai",
        jobstores={"default": MemoryJobStore()},
        job_defaults={
            "misfire_grace_time": 24 * 3600,
            "coalesce": True,
            "max_instances": 1,
        },
    )
    for tid, cfg in TASK_CONFIG.items():
        mod, fn = cfg["callable"].split(":", 1)
        raw_func = getattr(importlib.import_module(mod), fn)
        wrapped = task_decorator(tid, cfg)(raw_func)
        if cfg.get("cron"):
            scheduler.add_job(
                wrapped, CronTrigger(timezone="Asia/Shanghai", **cfg["cron"]),
                id=tid, name=tid, replace_existing=True,
            )
    scheduler_holder.scheduler = scheduler
    return scheduler


def _wait_for(business_key: str, expected_success: int, timeout_sec: int = 60) -> int:
    """轮询等待指定 business_key 的 success 记录数达到预期。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with _engine.connect() as conn:
            cnt = conn.execute(
                text("SELECT COUNT(*) FROM scheduler_run_log "
                     "WHERE business_key=:bk AND status='success'"),
                {"bk": business_key},
            ).scalar()
        if cnt and cnt >= expected_success:
            return cnt
        time.sleep(0.5)
    return -1


def _run_in_subprocess_fake(func, args, kwargs, timeout_sec):  # noqa: ARG001
    """桩：记录被调用的业务函数名，返回成功；重试测试由调用计数控制异常。"""
    name = func.__name__
    _run_in_subprocess_fake.calls[name] = _run_in_subprocess_fake.calls.get(name, 0) + 1
    # retry_target 首次调用抛异常，触发重试
    if name == _run_in_subprocess_fake.retry_target:
        if _run_in_subprocess_fake.calls[name] == 1:
            raise RuntimeError("dry-run retry injection")
    return TaskResult(success=True, affected_rows=0)


_run_in_subprocess_fake.calls = {}
_run_in_subprocess_fake.retry_target = None


def check_registration_and_dag(scheduler: BackgroundScheduler) -> str | None:
    """注册数量校验 + style 月链 DAG 触发校验。返回 business_key 供清理，失败返回 None。"""
    cron_jobs = [j.id for j in scheduler.get_jobs() if j.id in TASK_CONFIG]
    if len(cron_jobs) != 6:
        record("cron 任务注册数=6", FAIL, f"实际 {len(cron_jobs)}: {cron_jobs}")
        return None
    record("cron 任务注册数=6", PASS, f"{cron_jobs}")

    bk = f"dry-run-dag-{uuid.uuid4().hex[:8]}"
    # MemoryJobStore 中按 task_id 注册的 cron job 的 func 即装饰后的 wrapper
    root_wrapper = None
    for job in scheduler.get_jobs():
        if job.id == "crawl_16888_style_monthly":
            root_wrapper = job.func
            break
    if root_wrapper is None:
        record("月链 DAG 触发", FAIL, "未找到 crawl_16888_style_monthly 已注册 wrapper")
        return None

    scheduler.add_job(
        root_wrapper, trigger="date", run_date=datetime.now(),
        id=f"dryrun-root-{uuid.uuid4().hex[:8]}", replace_existing=False,
        kwargs={"business_key": bk},
    )
    # style 链共 7 个任务（crawl + load_style_ods + 4 个 ETL + 2 个扇出终点）
    got = _wait_for(bk, expected_success=7, timeout_sec=60)
    if got != 7:
        record("月链 DAG 产生 7 条 success", FAIL, f"仅得到 {got} 条")
        return bk

    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT task_id, trigger_type FROM scheduler_run_log "
                 "WHERE business_key=:bk ORDER BY id"),
            {"bk": bk},
        ).fetchall()
    task_ids = [r[0] for r in rows]
    expected_order = [
        "crawl_16888_style_monthly", "load_style_ods", "build_sales_dimensions",
        "refresh_ads_zipper", "run_dwd_clean",
    ]
    # 前 5 个严格串行；最后两个终点并行，顺序不定
    head_ok = task_ids[:5] == expected_order
    tail_ok = set(task_ids[5:]) == {"update_lifecycle", "run_dws_agg"}
    trig_ok = rows[0][1] == "cron" and all(r[1] == "dependency" for r in rows[1:])
    record("月链 DAG 顺序与 trigger_type",
           PASS if (head_ok and tail_ok and trig_ok) else FAIL,
           f"tasks={task_ids}")
    return bk


def check_trade_guard(scheduler: BackgroundScheduler) -> str | None:
    """交易日守卫：mock 非交易日 → skipped 且不调用桩。"""
    bk = f"dry-run-guard-{uuid.uuid4().hex[:8]}"
    wrapper = None
    for job in scheduler.get_jobs():
        if job.id == "load_stock_daily":
            wrapper = job.func
            break
    if wrapper is None:
        record("交易日守卫", FAIL, "未找到 load_stock_daily wrapper")
        return None

    before = dict(_run_in_subprocess_fake.calls)
    with patch("scheduler.runtime.is_trade_day", return_value=False):
        scheduler.add_job(
            wrapper, trigger="date", run_date=datetime.now(),
            id=f"dryrun-guard-{uuid.uuid4().hex[:8]}", replace_existing=False,
            kwargs={"business_key": bk},
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            with _engine.connect() as conn:
                st = conn.execute(
                    text("SELECT status FROM scheduler_run_log "
                         "WHERE business_key=:bk LIMIT 1"),
                    {"bk": bk},
                ).scalar()
            if st:
                break
            time.sleep(0.3)
    called = _run_in_subprocess_fake.calls != before
    record("交易日守卫 skipped",
           PASS if (st == "skipped" and not called) else FAIL,
           f"status={st}, 桩被调用={called}")
    return bk


def check_retry(scheduler: BackgroundScheduler) -> str | None:
    """失败重试：update_lifecycle（无下游，已将 retry_interval 改为 0）首次失败后重试成功。"""
    bk = f"dry-run-retry-{uuid.uuid4().hex[:8]}"
    wrapper = None
    for job in scheduler.get_jobs():
        if job.id == "update_lifecycle":
            wrapper = job.func
            break
    if wrapper is None:
        record("失败重试", FAIL, "未找到 update_lifecycle wrapper")
        return None

    _run_in_subprocess_fake.retry_target = "update_lifecycle"
    scheduler.add_job(
        wrapper, trigger="date", run_date=datetime.now(),
        id=f"dryrun-retry-{uuid.uuid4().hex[:8]}", replace_existing=False,
        kwargs={"business_key": bk},
    )
    # 等待该 bk 出现 success（重试后的那次）
    deadline = time.time() + 30
    statuses = []
    while time.time() < deadline:
        with _engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, retry_no FROM scheduler_run_log "
                     "WHERE business_key=:bk ORDER BY id"),
                {"bk": bk},
            ).fetchall()
        statuses = [(r[0], r[1]) for r in rows]
        if any(s == "success" for s, _ in statuses):
            break
        time.sleep(0.5)
    _run_in_subprocess_fake.retry_target = None
    has_retrying = any(s == "retrying" for s, _ in statuses)
    has_success = any(s == "success" for s, _ in statuses)
    has_retry_no = any(r == 1 for _, r in statuses)
    record("失败重试调度",
           PASS if (has_retrying and has_success and has_retry_no) else FAIL,
           f"记录={statuses}")
    return bk


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _check_db_safe() -> bool:
    """检查数据库连通性，并拒绝在生产库上执行。"""
    try:
        with _engine.connect() as conn:
            db_name = conn.execute(text("SELECT DATABASE()")).scalar()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 数据库不可连，动态检查将跳过: {exc}")
        return False
    if db_name and ("prod" in db_name.lower() or "production" in db_name.lower()):
        print(f"[!] 检测到生产库 {db_name}，拒绝执行 dry-run。")
        return False
    print(f"[i] 连接数据库: {db_name}")
    return True


def main() -> None:
    """脚本入口。"""
    print("=" * 60)
    print("调度器 Dry-run 验证")
    print("=" * 60)

    print("\n[1] 静态校验")
    check_downstream_refs()
    check_no_cycle()
    check_no_orphan()
    check_cron_valid()
    check_callable_importable()

    business_keys: list[str] = []
    db_ok = _check_db_safe()

    if db_ok:
        print("\n[2] 动态验证（BackgroundScheduler + MemoryJobStore）")
        # 重试测试用任务：设为 1 次重试、间隔 0 分钟，需在装饰前修改 config
        TASK_CONFIG["update_lifecycle"]["retry_times"] = 1
        TASK_CONFIG["update_lifecycle"]["retry_interval_minutes"] = 0

        # 屏蔽真实告警发送
        alert.send_alert = MagicMock()

        scheduler = _build_scheduler()
        scheduler.start()
        try:
            with patch("scheduler.runtime._run_in_subprocess",
                       side_effect=_run_in_subprocess_fake):
                bk = check_registration_and_dag(scheduler)
                if bk:
                    business_keys.append(bk)
                bk = check_trade_guard(scheduler)
                if bk:
                    business_keys.append(bk)
                bk = check_retry(scheduler)
                if bk:
                    business_keys.append(bk)
        finally:
            scheduler.shutdown(wait=False)
    else:
        record("动态验证", SKIP, "数据库不可连")

    # 清理 dry-run 日志
    if business_keys:
        print("\n[清理] 删除 dry-run 日志记录 ...")
        with _engine.begin() as conn:
            for bk in business_keys:
                conn.execute(
                    text("DELETE FROM scheduler_run_log WHERE business_key=:bk"),
                    {"bk": bk},
                )

    # 汇总
    print("\n" + "=" * 60)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print(f"汇总: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP")
    if n_fail:
        for name, s, detail in results:
            if s == FAIL:
                print(f"  [FAIL] {name} — {detail}")
        sys.exit(1)
    print("全部通过。")


if __name__ == "__main__":
    main()
