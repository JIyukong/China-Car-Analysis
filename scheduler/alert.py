"""调度器告警通道：飞书 / 企业微信机器人。

设计要点：
- 仅依赖标准库 urllib，避免为告警单独引入 requests。
- 进程内去重：同 ``task_id:run_id:level`` 在 10 分钟内只发一次，
  防止重试风暴刷屏；CRITICAL 级别不去重（心跳告警必须每次都到）。
- 发送失败仅记录日志，不向上抛异常，绝不阻塞调度主流程。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime

from base_infra.config import get_settings
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 去重窗口与清理阈值（秒）
_DEDUP_WINDOW_SEC = 10 * 60        # 同 key 10 分钟内只发一次
_DEDUP_PURGE_SEC = 30 * 60         # 超过 30 分钟的条目定期清理
_HTTP_TIMEOUT_SEC = 5              # webhook 请求超时

# 进程内去重表：key -> 上次发送时间戳
_recent_alerts: dict[str, float] = {}
_recent_alerts_lock = threading.Lock()
_last_purge_ts = 0.0


def _purge_stale_entries(now: float) -> None:
    """清理超过 30 分钟未更新的去重条目，避免内存无限增长。"""
    global _last_purge_ts
    if now - _last_purge_ts < _DEDUP_PURGE_SEC:
        return
    stale_keys = [k for k, ts in _recent_alerts.items() if now - ts > _DEDUP_PURGE_SEC]
    for k in stale_keys:
        _recent_alerts.pop(k, None)
    _last_purge_ts = now


def _should_skip(level: str, task_id: str, run_id: str) -> bool:
    """判断该告警是否因去重而应跳过。CRITICAL 永远发送。"""
    if level.upper() == "CRITICAL":
        return False
    key = f"{task_id}:{run_id}:{level.upper()}"
    now = time.time()
    with _recent_alerts_lock:
        _purge_stale_entries(now)
        last = _recent_alerts.get(key)
        if last is not None and now - last < _DEDUP_WINDOW_SEC:
            return True
        _recent_alerts[key] = now
    return False


def _build_payload(webhook_type: str, text: str) -> bytes:
    """按机器人类型组装请求体并序列化为 UTF-8 JSON。"""
    wt = (webhook_type or "feishu").lower()
    if wt == "wecom":
        body = {"msgtype": "text", "text": {"content": text}}
    else:  # feishu 默认
        body = {"msg_type": "text", "content": {"text": text}}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _format_extra(extra: dict) -> str:
    """把额外字段格式化为 key=value 多行文本；空字典返回空串。"""
    if not extra:
        return ""
    lines = [f"{k}={v}" for k, v in extra.items() if v is not None]
    return "\n".join(lines)


def send_alert(
    level: str,
    task_id: str,
    run_id: str,
    message: str,
    *,
    deduplicate: bool = True,
    **extra: object,
) -> bool:
    """发送一条调度器告警。

    :param level: INFO / WARNING / ERROR / CRITICAL
    :param task_id: 任务标识
    :param run_id: 本次运行 ID
    :param message: 告警正文
    :param extra: 附加上下文字段（business_key/error_type/error_msg 等）
    :returns: True=已发送或无 webhook 仅记日志；False=发送失败
    """
    lvl = (level or "INFO").upper()

    if deduplicate and _should_skip(lvl, task_id, run_id):
        logger.debug("告警命中去重窗口，跳过发送: %s/%s/%s", task_id, run_id, lvl)
        return True

    settings = get_settings()
    chat_id = getattr(settings, "FEISHU_ALERT_CHAT_ID", "") or ""
    webhook_url = getattr(settings, "ALERT_WEBHOOK_URL", "") or ""
    webhook_type = getattr(settings, "ALERT_WEBHOOK_TYPE", "feishu") or "feishu"

    extra_text = _format_extra(extra)  # type: ignore[arg-type]
    text = f"[CarApp调度器][{lvl}] {task_id}/{run_id}\n{message}"
    if extra_text:
        text = f"{text}\n{extra_text}"
    text = f"{text}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    if chat_id:
        from scheduler.feishu import FeishuError, client_from_settings

        try:
            client_from_settings(settings).send_chat_text(chat_id, text)
        except FeishuError as exc:
            logger.warning("飞书群告警发送失败: task=%s run=%s reason=%s", task_id, run_id, exc)
            return False
        logger.info("飞书群告警已发送[%s]: %s/%s", lvl, task_id, run_id)
        return True

    if not webhook_url:
        logger.warning("[ALERT][%s] %s/%s - %s", lvl, task_id, run_id, message)
        return True

    payload = _build_payload(webhook_type, text)
    req = urllib.request.Request(
        url=webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status >= 400:
                logger.warning("告警 webhook 返回非 2xx: status=%s", status)
                return False
            result = json.loads(resp.read())
            code = result.get("errcode") if webhook_type.lower() == "wecom" else result.get(
                "code", result.get("StatusCode"),
            )
            if code != 0:
                logger.warning("告警 webhook 拒绝请求: type=%s code=%s", webhook_type, code)
                return False
        logger.info("告警已发送[%s]: %s/%s", lvl, task_id, run_id)
        return True
    except (OSError, TimeoutError, ValueError, AttributeError) as exc:
        # 网络异常不允许抛出，仅记录日志，避免影响调度主流程
        logger.warning("告警发送失败: task=%s run=%s error_type=%s", task_id, run_id, type(exc).__name__)
        return False
