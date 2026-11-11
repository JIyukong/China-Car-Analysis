"""车型月销量数据链一次性执行器（运维工具）。

用法：
    python scripts/run_monthly_chain_once.py [--month YYYY-MM]

按 DAG 顺序串行执行：
    load_style_ods -> build_sales_dimensions -> refresh_ads_zipper
    -> run_dwd_clean -> update_lifecycle -> run_dws_agg

每个任务独立捕获结果/异常，失败后继续，最终生成
docs/monthly_chain_report.html。

注意：本脚本绕过 APScheduler 的 @task 装饰器，直接调用业务函数，
不写 scheduler_run_log、不触发下游，仅用于一次性体检/补数。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import platform
import socket
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _patch_pymysql_timezone() -> None:
    """当前 PyMySQL 1.1.2 的 Connection.__init__ 不接受 time_zone 参数，
    而 base_infra.config.Settings.database_url 硬编码了 ?time_zone=+08:00，
    SQLAlchemy 会把该 query 参数透传给 pymysql.connect 导致 TypeError。

    在导入 base_infra.db（创建 engine）之前，包装 pymysql.connect，
    静默剔除 time_zone kwarg。数据库服务器默认时区已是 Asia/Shanghai，
    去掉该参数不影响时间语义。
    """
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
from pipelines.dim.build_sales_dimensions import run_build_sales_dimensions  # noqa: E402
from pipelines.dim.update_lifecycle import run_update_lifecycle  # noqa: E402
from pipelines.ingest.sales.load_16888_style_to_ods import (  # noqa: E402
    main as load_style_ods_main,
)
from pipelines.serve.agg_sales_monthly import run_agg_sales_monthly  # noqa: E402
from pipelines.staging.clean_sales_dwd import run_clean_sales_dwd  # noqa: E402


class SkipTask(Exception):
    pass


def _raise_skip(msg: str):
    raise SkipTask(msg)


import scheduler.jobs.etl_jobs as etl_jobs  # noqa: E402
import scheduler.jobs.load_ods_job as load_ods_job  # noqa: E402

etl_jobs.SkipTask = SkipTask
load_ods_job.SkipTask = SkipTask
etl_jobs.check_dimension_readiness = _raise_skip
etl_jobs._check_dws_dimensions_ready = _raise_skip


@dataclass
class TaskRecord:
    task_id: str
    title: str
    status: str = "pending"
    start_time: str = ""
    end_time: str = ""
    duration_sec: float = 0.0
    affected_rows: int | None = None
    message: str = ""
    error_type: str = ""
    traceback_tail: str = ""


def _run_style_ods(business_key: str) -> int:
    csv_path = (
        PROJECT_ROOT
        / "pipelines"
        / "ingest"
        / "sales"
        / "crawl_16888_style"
        / "data"
        / f"style_{business_key}.csv"
    )
    if not csv_path.exists():
        raise SkipTask(f"CSV 不存在: {csv_path}")
    load_style_ods_main(csv_path)
    with engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT COUNT(*) FROM ods_sales_monthly_model "
                "WHERE source_file LIKE :pat"
            ),
            {"pat": f"%style_{business_key}.csv%"},
        ).scalar()
    return int(n or 0)


def _run_build_dimensions(business_key: str) -> int:
    p_month = dt.datetime.strptime(business_key, "%Y-%m").date()
    return run_build_sales_dimensions(p_month)


def _run_refresh_ads(business_key: str) -> int:
    with engine.begin() as conn:
        conn.execute(text("CALL sp_refresh_dim_model_ads_all()"))
    return 0


def _run_dwd_clean(business_key: str) -> int:
    p_month = dt.datetime.strptime(business_key, "%Y-%m").date()
    return run_clean_sales_dwd(p_month)


def _run_lifecycle(business_key: str) -> int:
    p_month = dt.datetime.strptime(business_key, "%Y-%m").date()
    return run_update_lifecycle(p_month)


def _run_dws_agg(business_key: str) -> int:
    p_month = dt.datetime.strptime(business_key, "%Y-%m").date()
    return run_agg_sales_monthly(p_month)


TASKS: list[tuple[str, str, Callable[[str], int]]] = [
    ("load_style_ods", "① ODS 入库（16888 style CSV → MySQL）", _run_style_ods),
    ("build_sales_dimensions", "② 拉链维度构建（dim_model SCD2 + 维度派生）", _run_build_dimensions),
    ("refresh_ads_zipper", "③ ADS 拉链刷新（sp_refresh_dim_model_ads_all）", _run_refresh_ads),
    ("run_dwd_clean", "④ DWD 清洗（车型/销量/价格/能源/级别）", _run_dwd_clean),
    ("update_lifecycle", "⑤ 更新车型生命周期（在售/停售）", _run_lifecycle),
    ("run_dws_agg", "⑥ DWS 月度聚合（车系/厂商/品牌/级别/能源/价格带/城市）", _run_dws_agg),
]


def _execute(task_id: str, title: str, fn: Callable[[str], int], business_key: str) -> TaskRecord:
    rec = TaskRecord(task_id=task_id, title=title)
    rec.start_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t0 = time.time()
    print(f"\n{'=' * 70}\n▶ [{task_id}] {title}\n{'=' * 70}")
    try:
        rows = fn(business_key)
        rec.affected_rows = int(rows) if rows is not None else None
        rec.status = "success"
        rec.message = f"affected_rows={rec.affected_rows}"
        print(f"✅ [{task_id}] 完成，affected_rows={rec.affected_rows}")
    except SkipTask as e:
        rec.status = "skipped"
        rec.message = str(e)
        print(f"⏭️ [{task_id}] 跳过: {e}")
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


def render_html(records: list[TaskRecord], business_key: str, started_at: str, finished_at: str) -> str:
    total = len(records)
    ok = sum(1 for r in records if r.status == "success")
    fail = sum(1 for r in records if r.status == "failed")
    skip = sum(1 for r in records if r.status == "skipped")
    total_dur = round(sum(r.duration_sec for r in records), 2)
    total_rows = sum(r.affected_rows or 0 for r in records)

    dag_nodes = ""
    for i, r in enumerate(records):
        color = STATUS_COLOR[r.status]
        dag_nodes += f'<div class="dag-node" style="border-color:{color}">' \
                     f'<div class="dag-dot" style="background:{color}"></div>' \
                     f'<div class="dag-name">{_esc(r.task_id)}</div>' \
                     f'<div class="dag-status" style="color:{color}">{STATUS_LABEL[r.status]}</div>' \
                     f'</div>'
        if i < len(records) - 1:
            dag_nodes += '<div class="dag-arrow">▶</div>'

    rows_html = ""
    for r in records:
        color = STATUS_COLOR[r.status]
        rows_html += f"""
        <tr>
          <td><code>{_esc(r.task_id)}</code><br><span class="muted">{_esc(r.title)}</span></td>
          <td><span class="badge" style="background:{color}">{STATUS_LABEL[r.status]}</span></td>
          <td>{_esc(r.start_time)}<br><span class="muted">→ {_esc(r.end_time)}</span></td>
          <td class="num">{r.duration_sec}</td>
          <td class="num">{r.affected_rows if r.affected_rows is not None else '—'}</td>
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

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>车型月销量数据链执行报告 — {business_key}</title>
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
  <h1>🚗 车型月销量数据链执行报告</h1>
  <div class="sub">business_key: <code>{business_key}</code> ｜ 执行时间: {started_at} → {finished_at} ｜
    主机: {_esc(socket.gethostname())} ｜ Python: {platform.python_version()}</div>

  <div class="cards">
    <div class="card"><div class="v">{total}</div><div class="l">任务总数</div></div>
    <div class="card"><div class="v ok">{ok}</div><div class="l">成功</div></div>
    <div class="card"><div class="v fail-c">{fail}</div><div class="l">失败</div></div>
    <div class="card"><div class="v skip-c">{skip}</div><div class="l">跳过</div></div>
    <div class="card"><div class="v">{total_dur}s</div><div class="l">总耗时</div></div>
    <div class="card"><div class="v">{total_rows}</div><div class="l">累计影响行数</div></div>
  </div>

  <div class="panel">
    <h2>📊 DAG 执行状态</h2>
    <div class="dag">{dag_nodes}</div>
    <p class="muted" style="margin-top:14px">说明：本次为一次性体检执行，任务间采用"失败后继续"策略（与生产 fail-fast 不同），便于查看所有节点真实状态。</p>
  </div>

  <div class="panel">
    <h2>📋 任务明细</h2>
    <table>
      <thead><tr><th>任务</th><th>状态</th><th>开始 / 结束</th><th>耗时(s)</th><th>影响行数</th><th>信息</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>🐛 错误详情</h2>
    {err_html}
  </div>

  <footer>
    生成脚本: <code>scripts/run_monthly_chain_once.py</code> ｜
    数据来源: 车主之家 16888 ｜ 执行策略: 直接调用业务函数，绕过 APScheduler
  </footer>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2026-07", help="目标月份 YYYY-MM")
    args = parser.parse_args()
    business_key = args.month

    csv_path = PROJECT_ROOT / "pipelines" / "ingest" / "sales" / "crawl_16888_style" / "data" / f"style_{business_key}.csv"
    print(f"[init] business_key={business_key}, csv_exists={csv_path.exists()}, path={csv_path}")

    started_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records: list[TaskRecord] = []
    for task_id, title, fn in TASKS:
        records.append(_execute(task_id, title, fn, business_key))
    finished_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out = PROJECT_ROOT / "docs" / "monthly_chain_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(records, business_key, started_at, finished_at), encoding="utf-8")
    print(f"\n[report] HTML written: {out}")

    fail = sum(1 for r in records if r.status == "failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
