"""定时爬取 16888 销量数据任务。

数据发布节奏（业务事实）：
    - 车型月销量（style）：每月 12~15 号发布上月数据
    - 城市车型月销量（city）：每月 20~25 号发布上月数据

因此拆成两条独立月链，cron 在各自窗口内每日探测，由 ODS 行数判断上月数据是否
到位，未到位抛 :class:`scheduler.runtime.SkipTask`，次日继续探测，到位后由下游
事件驱动。超时与强杀由 runtime.@task 装饰器在进程层兜底；本模块内部 Popen 另设
一个安全上限，避免脚本卡死时长时间无输出。
"""

from __future__ import annotations

import csv
import os
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

try:
    from base_infra.config import get_settings
    from base_infra.db import engine
    from base_infra.logging_config import get_logger
    from scheduler import alert
    from scheduler.runtime import SkipTask
except ImportError:
    from base_infra.db import engine
    from base_infra.logging_config import get_logger
    from scheduler import alert
    from scheduler.runtime import SkipTask

logger = get_logger(__name__)

# Popen 自身的安全上限（秒）。比 runtime 的 timeout_minutes(180) 略短，
# 确保 Popen 先触发、能完整记录 tail stdout 并优雅 return False。
_POPEN_TIMEOUT_SEC = 170 * 60
# 失败时回写到 scheduler_run_log.error_msg 的 stdout 尾部长度
_TAIL_CHARS = 4000
# 判定目标月数据"已到位"所需的最小行数（防止抓到半截页面误判）：
# style 每月约 500+ 行，city 每月约 25000 行，阈值取预期的 ~80%
_MIN_ROWS_STYLE = 400
_MIN_ROWS_CITY = 20000


def _project_root() -> Path:
    """从配置读取项目根目录；失败时回退到本文件向上两级。"""
    try:
        return Path(get_settings().PROJECT_ROOT)
    except Exception:  # noqa: BLE001 - .env 未加载或字段缺失时回退
        return Path(__file__).resolve().parents[2]


def _target_month(business_key: str | None) -> str:
    """把业务键 YYYY-MM 归一化；缺省取上月（月链 cron 默认语义）。"""
    if business_key:
        month = business_key[:7]
        # 严格校验，避免非法 key 静默传入 SQL/CSV 路径
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError(f"非法 business_key: {business_key!r}") from exc
        return month
    first_of_this = date.today().replace(day=1)
    return (first_of_this - timedelta(days=1)).strftime("%Y-%m")


def _month_first_day(month: str) -> date:
    """YYYY-MM → 当月第一天 date。"""
    return datetime.strptime(month, "%Y-%m").date()


def _ods_rows_for_month(table: str, month: str) -> int:
    """统计 ODS 表中指定月份（stat_month = 该月 1 号）的行数。"""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {table} "  # noqa: S608 - table 为内部常量
                "WHERE stat_month = :d"
            ),
            {"d": _month_first_day(month)},
        ).fetchone()
    return int(row[0] or 0) if row else 0


def _csv_rows_for_month(csv_path: Path, month: str) -> int:
    """统计本地 CSV 中目标月份的数据行数（不含表头）。

    爬虫落地 ``data/<prefix>_<YYYY-MM>.csv``，用于在 ODS 入库前判断站点
    是否已发布目标月数据；CSV 不存在或为空返回 0。
    """
    if not csv_path.exists():
        return 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for row in reader if str(row.get("month", "")).strip() == month)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """强杀子进程及其进程组（连同 Playwright/Chromium 孙进程一起收）。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("taskkill 失败 pid=%s err=%s", proc.pid, exc)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            logger.warning("killpg 失败 pid=%s err=%s", proc.pid, exc)


def _run_script(name: str, script: Path, args: list[str], cwd: Path) -> bool:
    """启动脚本并等待结束；超时强杀进程树，失败时 tail stdout 写入日志。"""
    if not script.exists():
        logger.error("爬虫脚本不存在，跳过 %s: %s", name, script)
        return False

    # Windows 用 CREATE_NEW_PROCESS_GROUP；POSIX 用 start_new_session 建立新进程组，
    # 便于超短时按组 kill 掉 Playwright 拉起的 Chromium 孙进程。
    popen_kwargs: dict = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    cmd = [sys.executable, str(script), *args]
    logger.info("开始爬取 %s: %s", name, " ".join(cmd))

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, _ = proc.communicate(timeout=_POPEN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        logger.error("%s 执行超时（%ss），强杀进程树 pid=%s",
                     name, _POPEN_TIMEOUT_SEC, proc.pid)
        _kill_process_tree(proc)
        try:
            stdout, _ = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            stdout = ""
        tail = (stdout or "")[-_TAIL_CHARS:]
        logger.error("%s 超时输出尾部:\n%s", name, tail)
        return False

    if proc.returncode != 0:
        tail = (stdout or "")[-_TAIL_CHARS:]
        logger.error("%s 爬取失败 exit=%s 输出尾部:\n%s",
                     name, proc.returncode, tail)
        return False

    logger.info("%s 爬取完成", name)
    return True


def _crawl_one(
    *,
    name: str,
    script_rel: str,
    data_dir_rel: str,
    csv_prefix: str,
    ods_table: str,
    min_rows: int,
    business_key: str | None,
) -> bool:
    """通用单源月度爬取：前置 ODS 去重 → 子进程抓取 → 后置 CSV 就绪校验。

    - 前置：ODS 已有目标月足够行数 → 抛 SkipTask（窗口内后续每日不重抓）。
    - 后置：抓完后本地 CSV 仍无目标月数据 → 抛 SkipTask（站点尚未发布，次日再探）。
      入库由下游 load_*_ods 任务完成，故此处校验 CSV 而非 ODS。
    """
    month = _target_month(business_key)
    logger.info("%s 月度爬取 business_key=%s 目标月=%s", name, business_key, month)

    existing = _ods_rows_for_month(ods_table, month)
    if existing >= min_rows:
        raise SkipTask(
            f"{name} {month} 已在 ODS 存在 {existing} 行（≥{min_rows}），跳过抓取"
        )

    root = _project_root()
    script = root / script_rel
    # 月链只抓最近一个月；headless 适合服务器/定时任务；加 --resume 以便
    # 断点续传（已完成月份会跳过，未完成月份从断点页继续）。
    args = ["--months", "latest", "--headless", "--resume"]

    if not _run_script(name, script, args, root):
        return False

    # 爬虫 latest 模式自动探测"站点最新有数据月份"，若目标月尚未发布，
    # 抓到的是上上月，本地 CSV 不会出现目标月，据此判断未就绪。
    csv_path = root / data_dir_rel / f"{csv_prefix}_{month}.csv"
    new_rows = _csv_rows_for_month(csv_path, month)
    if new_rows < min_rows:
        raise SkipTask(
            f"{name} 抓取完成但 {month} 在 {csv_path.name} 仅 {new_rows} 行"
            f"（<{min_rows}），判定站点尚未发布，等待次日窗口重试"
        )

    logger.info("%s %s 数据已到位，CSV 行数=%s", name, month, new_rows)
    try:
        alert.send_alert(
            "INFO",
            f"crawl_16888_{name}",
            f"crawl_16888_{name}:{month}",
            f"✅ {name} 数据下载成功\n"
            f"目标月份：{month}\n"
            f"CSV 行数：{new_rows}\n"
            f"文件：{csv_path.name}",
        )
    except Exception:
        logger.exception("发送下载成功通知失败（不影响链路）")
    return True


def crawl_16888_style_monthly(business_key: str | None = None) -> bool:
    """爬取上月 16888 车型月销量（style）。

    站点每月 12~15 号发布上月数据，cron 在窗口内每日 03:00 探测；
    已到位或未发布均抛 :class:`SkipTask`，只有抓取异常才返回 False 触发重试。
    """
    return _crawl_one(
        name="16888_style",
        script_rel="pipelines/ingest/sales/crawl_16888_style/crawl_style.py",
        data_dir_rel="pipelines/ingest/sales/crawl_16888_style/data",
        csv_prefix="style",
        ods_table="ods_sales_monthly_model",
        min_rows=_MIN_ROWS_STYLE,
        business_key=business_key,
    )


def crawl_16888_city_monthly(business_key: str | None = None) -> bool:
    """爬取上月 16888 城市车型月销量（city）。

    站点每月 20~25 号发布上月数据，cron 在窗口内每日 03:30 探测；
    已到位或未发布均抛 :class:`SkipTask`，只有抓取异常才返回 False 触发重试。
    """
    return _crawl_one(
        name="16888_city",
        script_rel="pipelines/ingest/sales/crawl_16888_city/crawl_city.py",
        data_dir_rel="pipelines/ingest/sales/crawl_16888_city/data",
        csv_prefix="city",
        ods_table="ods_sales_monthly_model_city",
        min_rows=_MIN_ROWS_CITY,
        business_key=business_key,
    )


if __name__ == "__main__":
    crawl_16888_style_monthly()
