"""日链一次性执行器（运维工具）。

用法：
    python scripts/run_daily_chain_once.py [--date YYYY-MM-DD]

按 DAG 顺序串行执行（生产为并行，此处串行便于体检）：
    load_stock_daily -> load_futures_daily -> load_index_daily -> load_stock_finance

执行前做 SSE 交易日守卫；非交易日则全部跳过并生成报告。
每个任务独立捕获结果/异常，失败后继续，最终生成
docs/daily_chain_report.html。

注意：本脚本绕过 APScheduler 的 @task 装饰器，直接调用业务函数，
不写 scheduler_run_log、不触发下游，仅用于一次性体检/补数。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import platform
import socket
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _patch_pymysql_timezone() -> None:
    import pymysql

    _orig_connect = pymysql.connect

    def _patched(*args, **kwargs):
        kwargs.pop("time_zone", None)
        return _orig_connect(*args, **kwargs)

    pymysql.connect = _patched
    print("[db] patched pymysql.connect to strip time_zone kwarg")


_patch_pymysql_timezone()


from sqlalchemy import text  # noqa: E402

from base_infra.db import engine  # noqa: E402
from pipelines.ingest.futures.load_futures_main_sina import (  # noqa: E402
    STATS as futures_stats,
)
from pipelines.ingest.index.load_ths_index_daily_akshare import (  # noqa: E402
    STATS as index_stats,
)
from pipelines.ingest.stock.load_stock_daily_akshare import (  # noqa: E402
    STATS as stock_daily_stats,
)
from pipelines.ingest.stock.load_stock_finance_akshare import (  # noqa: E402
    STATS as stock_finance_stats,
)
from scheduler.jobs.load_futures_ods_job import load_futures_main_daily  # noqa: E402
from scheduler.jobs.load_index_ods_job import load_ths_index_daily  # noqa: E402
from scheduler.jobs.load_stock_ods_job import (  # noqa: E402
    load_stock_daily,
    load_stock_finance,
)


@dataclass
class TaskRecord:
    task_id: str
    title: str
    status: str = "pending"
    start_time: str = ""
    end_time: str = ""
    duration_sec: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    total_rows: int = 0
    total_items: int = 0
    items_label: str = ""
    message: str = ""
    error_type: str = ""
    traceback_tail: str = ""


def _snapshot_stats(stats: dict, items_key: str, label: str) -> dict:
    return {
        "items_label": label,
        "total_items": int(stats.get(items_key, 0) or 0),
        "success_count": int(stats.get("success_count", 0) or 0),
        "fail_count": int(stats.get("fail_count", 0) or 0),
        "skip_count": int(stats.get("skip_count", 0) or 0),
        "total_rows": int(stats.get("total_rows", 0) or 0),
    }


def _reset_stats(stats: dict, items_key: str) -> None:
    stats[items_key] = 0
    stats["success_count"] = 0
    stats["fail_count"] = 0
    stats["skip_count"] = 0
    stats["warning_count"] = 0
    stats["total_rows"] = 0
    stats["fail_details"] = []
    stats["error_types"] = {}


def _run_stock_daily(business_key: str) -> dict:
    _reset_stats(stock_daily_stats, "total_stocks")
    ok = load_stock_daily(business_key=business_key)
    snap = _snapshot_stats(stock_daily_stats, "total_stocks", "股票数")
    snap["ok"] = ok
    if not ok:
        raise RuntimeError(f"load_stock_daily 返回 False（成功 {snap['success_count']} / 失败 {snap['fail_count']}）")
    return snap


def _run_futures_daily(business_key: str) -> dict:
    _reset_stats(futures_stats, "total_commodities")
    ok = load_futures_main_daily(business_key=business_key)
    snap = _snapshot_stats(futures_stats, "total_commodities", "品种数")
    snap["ok"] = ok
    if not ok:
        raise RuntimeError(f"load_futures_main_daily 返回 False（成功 {snap['success_count']} / 失败 {snap['fail_count']}）")
    return snap


def _run_index_daily(business_key: str) -> dict:
    _reset_stats(index_stats, "total_indices")
    ok = load_ths_index_daily(business_key=business_key)
    snap = _snapshot_stats(index_stats, "total_indices", "指数数")
    snap["ok"] = ok
    if not ok:
        raise RuntimeError(f"load_ths_index_daily 返回 False（成功 {snap['success_count']} / 失败 {snap['fail_count']}）")
    return snap


def _run_stock_finance(business_key: str) -> dict:
    _reset_stats(stock_finance_stats, "total_stocks")
    ok = load_stock_finance(business_key=business_key)
    snap = _snapshot_stats(stock_finance_stats, "total_stocks", "股票数")
    snap["ok"] = ok
    if not ok:
        raise RuntimeError(f"load_stock_finance 返回 False（成功 {snap['success_count']} / 失败 {snap['fail_count']}）")
    return snap


TASKS: list[tuple[str, str, Callable[[str], dict]]] = [
    ("load_stock_daily", "① 股票日线采集（akshare → ods_stock_daily）", _run_stock_daily),
    ("load_futures_daily", "② 商品期货主力连续日线（新浪 → ods_futures_daily）", _run_futures_daily),
    ("load_index_daily", "③ 同花顺行业指数日线（akshare → ods_index_daily）", _run_index_daily),
    ("load_stock_finance", "④ 股票财务数据采集（akshare → ods_stock_finance）", _run_stock_finance),
]


def is_trade_day(date_: dt.date, market: str = "SSE") -> tuple[bool, str]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_open FROM dim_trade_calendar "
                "WHERE trade_date = :d AND market = :m"
            ),
            {"d": date_, "m": market},
        ).fetchone()
    if row is None:
        return True, f"交易日历无 {date_} ({market}) 记录，按交易日处理"
    return bool(row[0]), ("交易日" if row[0] else "非交易日（休市）")


def _execute(task_id: str, title: str, fn: Callable[[str], dict], business_key: str) -> TaskRecord:
    rec = TaskRecord(task_id=task_id, title=title)
    rec.start_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    print(f"\n{'=' * 70}\n▶ [{task_id}] {title}\n{'=' * 70}")
    try:
        snap = fn(business_key)
        rec.success_count = snap["success_count"]
        rec.fail_count = snap["fail_count"]
        rec.skip_count = snap["skip_count"]
        rec.total_rows = snap["total_rows"]
        rec.total_items = snap["total_items"]
        rec.items_label = snap["items_label"]
        rec.status = "success"
        rec.message = (
            f"{rec.items_label}={rec.total_items}, 成功={rec.success_count}, "
            f"失败={rec.fail_count}, 跳过={rec.skip_count}, 写入行数={rec.total_rows}"
        )
        print(f"✅ [{task_id}] 完成：{rec.message}")
    except Exception as e:  # noqa: BLE001
        rec.status = "failed"
        rec.error_type = type(e).__name__
        rec.message = str(e)
        rec.traceback_tail = traceback.format_exc()[-3000:]
        print(f"❌ [{task_id}] 失败: {type(e).__name__}: {e}")
    finally:
        rec.duration_sec = round(time.time() - t0, 2)
        rec.end_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return rec


STATUS_COLOR = {
    "success": "#2ecc71",
    "failed": "#e74c3c",
    "skipped": "#f39c12",
    "pending": "#95a5a6",
}
STATUS_LABEL = {
    "success": "成功",
    "failed": "失败",
    "skipped": "跳过",
    "pending": "待执行",
}


def _esc(s: str) -> str:
    return html.escape(str(s) if s is not None else "")


def render_html(
    records: list[TaskRecord],
    business_key: str,
    is_trade: bool,
    trade_note: str,
    started_at: str,
    finished_at: str,
) -> str:
    total = len(records)
    ok = sum(1 for r in records if r.status == "success")
    fail = sum(1 for r in records if r.status == "failed")
    skip = sum(1 for r in records if r.status == "skipped")
    total_dur = round(sum(r.duration_sec for r in records), 2)
    total_rows = sum(r.total_rows for r in records)
    total_ok_items = sum(r.success_count for r in records)
    total_fail_items = sum(r.fail_count for r in records)

    dag_nodes = ""
    for i, r in enumerate(records):
        color = STATUS_COLOR[r.status]
        dag_nodes += (
            f'<div class="dag-node" style="border-color:{color}">'
            f'<div class="dag-dot" style="background:{color}"></div>'
            f'<div class="dag-name">{_esc(r.task_id)}</div>'
            f'<div class="dag-status" style="color:{color}">{STATUS_LABEL[r.status]}</div>'
            f'</div>'
        )
        if i < len(records) - 1:
            dag_nodes += '<div class="dag-arrow">▶</div>'

    rows_html = ""
    for r in records:
        color = STATUS_COLOR[r.status]
        stats_cell = (
            f"成功 <b>{r.success_count}</b> ｜ 失败 <b>{r.fail_count}</b>"
            f"{' ｜ 跳过 ' + str(r.skip_count) if r.skip_count else ''}"
            f"<br><span class='muted'>{_esc(r.items_label)}={r.total_items} ｜ 写入 {r.total_rows} 行</span>"
            if r.status == "success"
            else "—"
        )
        rows_html += f"""
        <tr>
          <td><code>{_esc(r.task_id)}</code><br><span class="muted">{_esc(r.title)}</span></td>
          <td><span class="badge" style="background:{color}">{STATUS_LABEL[r.status]}</span></td>
          <td>{_esc(r.start_time)}<br><span class="muted">→ {_esc(r.end_time)}</span></td>
          <td class="num">{r.duration_sec}</td>
          <td>{stats_cell}</td>
          <td class="msg">{_esc(r.message)}</td>
        </tr>"""

    err_html = ""
    for r in records:
        if r.status == "failed" and r.traceback_tail:
            err_html += f"""
        <details class="err-block">
          <summary><span style="color:{STATUS_COLOR['failed']}">❌ {_esc(r.task_id)}</span> — {_esc(r.error_type)}: {_esc(r.message[:120])}</summary>
          <pre>{_esc(r.traceback_tail)}</pre>
        </details>"""
    if not err_html:
        err_html = '<p class="muted">无失败任务。</p>'

    trade_badge_color = "#2ecc71" if is_trade else "#f39c12"
    trade_badge_text = "交易日" if is_trade else "非交易日"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>日链执行报告 — {business_key}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background:#0f1320; color:#e4e7ee; padding:28px; }}
  h1 {{ margin:0 0 6px; font-size:24px; }}
  .sub {{ color:#9aa3b2; font-size:13px; margin-bottom:22px; }}
  .cards {{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:24px; }}
  .card {{ background:#1a2030; border:1px solid #2a3142; border-radius:10px;
           padding:16px 20px; min-width:130px; }}
  .card .v {{ font-size:26px; font-weight:700; }}
  .card .l {{ color:#9aa3b2; font-size:12px; margin-top:4px; }}
  .ok {{ color:#2ecc71; }} .fail-c {{ color:#e74c3c; }} .skip-c {{ color:#f39c12; }}
  .panel {{ background:#1a2030; border:1px solid #2a3142; border-radius:10px;
            padding:20px; margin-bottom:22px; }}
  .panel h2 {{ margin:0 0 16px; font-size:17px; }}
  .dag {{ display:flex; align-items:center; flex-wrap:wrap; gap:8px; }}
  .dag-node {{ border:2px solid #2a3142; border-radius:8px; padding:10px 14px;
               min-width:150px; background:#111624; }}
  .dag-dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; margin-right:6px; }}
  .dag-name {{ font-weight:600; font-size:13px; display:inline-block; }}
  .dag-status {{ font-size:12px; margin-top:4px; }}
  .dag-arrow {{ color:#5a6478; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid #2a3142; vertical-align:top; }}
  th {{ color:#9aa3b2; font-weight:600; font-size:12px; text-transform:uppercase; }}
  td.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
  .muted {{ color:#7b8496; font-size:12px; }}
  .msg {{ color:#c8cdd8; word-break:break-all; }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:20px; color:#fff; font-size:12px; }}
  code {{ background:#111624; padding:2px 6px; border-radius:4px; font-size:12px; color:#9ecbff; }}
  .err-block {{ background:#111624; border:1px solid #2a3142; border-radius:6px;
                padding:10px 14px; margin-bottom:10px; }}
  .err-block summary {{ cursor:pointer; font-size:13px; }}
  pre {{ background:#0b0e17; color:#e0b4b4; padding:12px; border-radius:6px;
         overflow-x:auto; font-size:12px; white-space:pre-wrap; }}
  footer {{ margin-top:24px; color:#6b7488; font-size:12px; }}
</style>
</head>
<body>
  <h1>📈 日链执行报告</h1>
  <div class="sub">business_key: <code>{business_key}</code> ｜
    <span class="badge" style="background:{trade_badge_color}">{trade_badge_text}</span>
    <span class="muted">{_esc(trade_note)}</span><br>
    执行时间: {started_at} → {finished_at} ｜
    主机: {_esc(socket.gethostname())} ｜ Python: {platform.python_version()}</div>

  <div class="cards">
    <div class="card"><div class="v">{total}</div><div class="l">任务总数</div></div>
    <div class="card"><div class="v ok">{ok}</div><div class="l">成功</div></div>
    <div class="card"><div class="v fail-c">{fail}</div><div class="l">失败</div></div>
    <div class="card"><div class="v skip-c">{skip}</div><div class="l">跳过</div></div>
    <div class="card"><div class="v">{total_dur}s</div><div class="l">总耗时</div></div>
    <div class="card"><div class="v">{total_rows}</div><div class="l">累计写入行数</div></div>
    <div class="card"><div class="v ok">{total_ok_items}</div><div class="l">累计采集成功</div></div>
    <div class="card"><div class="v fail-c">{total_fail_items}</div><div class="l">累计采集失败</div></div>
  </div>

  <div class="panel">
    <h2>📊 DAG 执行状态</h2>
    <div class="dag">{dag_nodes}</div>
    <p class="muted" style="margin-top:14px">说明：生产环境中股票/期货/指数日线为并行调度（17:30），财务数据 18:30 单独调度；本报告为一次性体检，串行执行并采用"失败后继续"策略。</p>
  </div>

  <div class="panel">
    <h2>📋 任务明细</h2>
    <table>
      <thead><tr><th>任务</th><th>状态</th><th>开始 / 结束</th><th>耗时(s)</th><th>采集统计</th><th>信息</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>🐛 错误详情</h2>
    {err_html}
  </div>

  <footer>
    生成脚本: <code>scripts/run_daily_chain_once.py</code> ｜
    数据源: akshare / 新浪财经 ｜ 执行策略: 直接调用业务函数，绕过 APScheduler
  </footer>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.date.today().strftime("%Y-%m-%d"), help="目标日期 YYYY-MM-DD")
    args = parser.parse_args()
    business_key = args.date
    target_date = dt.datetime.strptime(business_key, "%Y-%m-%d").date()

    is_trade, trade_note = is_trade_day(target_date)
    print(f"[init] business_key={business_key}, is_trade_day={is_trade} ({trade_note})")

    started_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records: list[TaskRecord] = []

    if not is_trade:
        for task_id, title, _ in TASKS:
            rec = TaskRecord(task_id=task_id, title=title, status="skipped", message=trade_note)
            rec.start_time = rec.end_time = started_at
            records.append(rec)
    else:
        for task_id, title, fn in TASKS:
            records.append(_execute(task_id, title, fn, business_key))

    finished_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out = PROJECT_ROOT / "docs" / "daily_chain_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_html(records, business_key, is_trade, trade_note, started_at, finished_at),
        encoding="utf-8",
    )
    print(f"\n[report] HTML written: {out}")

    fail = sum(1 for r in records if r.status == "failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
