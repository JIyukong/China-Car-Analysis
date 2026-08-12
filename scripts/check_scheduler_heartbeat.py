"""调度器心跳探活脚本。

检查 scheduler_heartbeat.last_heartbeat 是否在 3 分钟内更新；
超时通过告警通道发 CRITICAL。

用法（系统 cron / 任务计划每 5 分钟）:
    python -m scripts.check_scheduler_heartbeat
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from sqlalchemy import text

from base_infra.db import engine
from base_infra.logging_config import get_logger
from scheduler.alert import send_alert

logger = get_logger(__name__)

# 心跳超时阈值（分钟）：runner 每 30 秒更新一次，3 分钟未更新判定为异常
STALE_MINUTES = 3


def main() -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT hostname, pid, last_heartbeat, started_at, version "
                "FROM scheduler_heartbeat WHERE id = 1"
            )
        ).fetchone()

    if row is None:
        send_alert("CRITICAL", "scheduler", "-", "调度器无心跳记录（表为空），可能从未启动")
        logger.error("scheduler_heartbeat 表为空")
        return 1

    hostname, pid, last_hb, started_at, version = row

    # MySQL DATETIME 通常返回 naive datetime，按本地时间比较；若带时区则对齐时区
    now = datetime.now()
    if isinstance(last_hb, datetime) and last_hb.tzinfo is not None:
        now = now.astimezone(last_hb.tzinfo)

    delta = now - last_hb
    if delta > timedelta(minutes=STALE_MINUTES):
        minutes = int(delta.total_seconds() // 60)
        msg = (
            f"调度器心跳已 {minutes} 分钟未更新 "
            f"(host={hostname}, pid={pid}, version={version}, "
            f"started_at={started_at}, last_heartbeat={last_hb})"
        )
        send_alert("CRITICAL", "scheduler", "-", msg)
        logger.error(msg)
        return 1

    logger.info(
        "心跳正常: host=%s last=%s (%.0f 秒前)",
        hostname,
        last_hb,
        delta.total_seconds(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
