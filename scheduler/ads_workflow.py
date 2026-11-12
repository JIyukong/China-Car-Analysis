"""Import the dedicated Feishu ADS table without republishing sales facts."""

import logging
import unicodedata

from filelock import Timeout

from scheduler.feishu import _ADS_FIELDS, _ads, _string, cell_text, client_from_settings

logger = logging.getLogger(__name__)


def sync(state, records, transaction, apply) -> int:
    payloads = {}
    keys = set()
    for row in records:
        fields = row["fields"]
        key = cell_text(fields, "model_name")
        values = {name: cell_text(fields, name) for name in _ADS_FIELDS}
        if not key and not any(values.values()):
            continue
        try:
            _string(key, "model_name", 128)
            unique = unicodedata.normalize("NFKD", key.casefold())
            unique = "".join(char for char in unique if not unicodedata.combining(char))
            if unique in keys:
                raise ValueError("model_name 重复")
            payloads[key] = _ads(values)
            keys.add(unique)
        except ValueError as exc:
            raise ValueError(f"ADS 记录 {row['record_id']} ({key})：{exc}") from exc
    if not payloads:
        return 0
    with state.lock("warehouse", timeout=10), transaction() as conn:
        for key in sorted(payloads):
            try:
                apply(conn, key, payloads[key])
            except ValueError as exc:
                raise ValueError(f"ADS 车型 {key}：{exc}") from exc
    return len(payloads)


def poll(state) -> bool:
    from base_infra.config import get_settings
    from base_infra.db import engine
    from pipelines.dim.manual_refresh import apply_ads

    settings = get_settings()
    table = settings.FEISHU_ADS_TABLE_ID.strip()
    if not table:
        return True
    try:
        if table == settings.FEISHU_TABLE_ID or not settings.FEISHU_BASE_TOKEN:
            raise ValueError("ADS 必须使用独立的飞书多维表")
        with state.lock("feishu_ads"):
            client = client_from_settings(settings, table_id=table)
            count = sync(state, client.records(), engine.begin, apply_ads)
            state.service("feishu_ads", "ok", f"已同步 {count} 条 ADS 权威记录")
        return True
    except Timeout:
        return True
    except Exception as exc:
        logger.exception("Feishu ADS sync failed")
        state.service("feishu_ads", "error", str(exc)[:400] if isinstance(exc, ValueError) else type(exc).__name__)
        return False
    finally:
        state.export()
