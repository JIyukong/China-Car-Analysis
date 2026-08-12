"""定时爬取 16888 销量数据任务。

通过 subprocess 调用 crawl_style.py / crawl_city.py，月链只抓上月数据。
超时与强杀由 runtime.@task 装饰器在进程层兜底；本模块内部 Popen 另设一个
安全上限，避免脚本卡死时长时间无输出。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from base_infra.config import get_settings
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# Popen 自身的安全上限（秒）。主超时由 runtime 装饰器按 timeout_minutes 控制。
_POPEN_TIMEOUT_SEC = 180 * 60
# 失败时回写到 scheduler_run_log.error_msg 的 stdout 尾部长度
_TAIL_CHARS = 4000


def _project_root() -> Path:
    """从配置读取项目根目录；失败时回退到本文件向上两级。"""
    try:
        return Path(get_settings().PROJECT_ROOT)
    except Exception:  # noqa: BLE001 - .env 未加载或字段缺失时回退
        return Path(__file__).resolve().parents[2]


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


def crawl_16888_monthly(business_key: str | None = None) -> bool:
    """爬取上月 16888 style + city 数据。

    :param business_key: 业务周期键（上月 YYYY-MM）；当前不影响脚本参数，
        两个爬虫脚本自身按 ``--months latest`` 抓最近一月，由 ODS 层按月份入库。
    :returns: 成功返回 True，任一脚本失败返回 False。
    """
    root = _project_root()
    style_script = (
        root / "pipelines" / "ingest" / "sales"
        / "crawl_16888_style" / "crawl_style.py"
    )
    city_script = (
        root / "pipelines" / "ingest" / "sales"
        / "crawl_16888_city" / "crawl_city.py"
    )

    # 月链只抓最近一个月；headless 适合服务器/定时任务；加 --resume 以便
    # 断点续传（已完成月份会跳过，未完成月份从断点页继续）。
    common_args = ["--months", "latest", "--headless", "--resume"]

    if business_key:
        logger.info("crawl_16888_monthly business_key=%s", business_key)

    if not _run_script("16888_style", style_script, common_args, root):
        return False
    if not _run_script("16888_city", city_script, common_args, root):
        return False
    return True


if __name__ == "__main__":
    crawl_16888_monthly()
