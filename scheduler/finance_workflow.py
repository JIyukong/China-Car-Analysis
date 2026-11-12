"""Track finance probes by the report periods observed in validated ODS."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from base_infra.config import get_settings
from pipelines.ingest.stock.load_stock_finance_akshare import CONTENT_FIELDS, collect_finance_reports
from scheduler.jobs.common import build_scheduler_config
from scheduler.workflow import State

logger = logging.getLogger(__name__)
_CHAIN = 'load_stock_finance'


def _dwd_enabled() -> bool:
    return get_settings().ETL_STOCK_DWD_ENABLED


def _publish_finance_period(period, keys):
    from pipelines.staging.clean_stock_finance_dwd import publish_finance_period

    return publish_finance_period(period, keys)


def _digest(rows):
    ordered = sorted(rows, key=lambda row: (row['stock_code_raw'], row['report_type']))
    payload = [[row[field] for field in CONTENT_FIELDS] for row in ordered]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def process_finance(state: State, day: date) -> bool:
    """Probe the active A-share universe and recover accepted report publications."""
    state.service(_CHAIN, 'collecting', f'财报来源探测 {day}')
    config = build_scheduler_config()
    config.report_through = day
    use_dwd = _dwd_enabled()
    tracked = {date.fromisoformat(row['period']): row for row in state.batches(_CHAIN)}
    pending = {}

    def stage(reports):
        for period, rows in reports.items():
            digest = _digest(rows)
            previous = tracked.get(period, {})
            completed = {'published'} if use_dwd else {'published', 'ods_accepted'}
            if digest == previous.get('published_digest') and previous.get('status') in completed:
                continue
            pending[period] = (digest, {(row['stock_code_raw'], row['report_type']) for row in rows})
            if digest != previous.get('digest'):
                state.save(_CHAIN, period.isoformat(), 'reconciling', digest=digest)

    try:
        verified = collect_finance_reports(config, tracked_periods=set(tracked), on_verified=stage)
    except Exception as exc:
        logger.exception('财报来源探测失败')
        state.service(_CHAIN, 'source_error', f'{day} {exc}')
        return False

    publication_failed = False
    for period, (digest, keys) in sorted(pending.items()):
        label = period.isoformat()
        if not use_dwd:
            state.save(_CHAIN, label, 'ods_accepted', digest=digest, message=f'财报 ODS 已验证 {len(keys)} 个业务键')
            continue
        state.save(_CHAIN, label, 'ready', digest=digest)
        try:
            _publish_finance_period(period, keys)
        except Exception as exc:
            logger.exception('财报 DWD 发布失败：%s', period)
            state.save(_CHAIN, label, 'publish_error', digest=digest, message=str(exc))
            publication_failed = True
            continue
        state.save(_CHAIN, label, 'published', digest=digest, message=f'财报 DWD 已发布 {len(keys)} 个业务键')

    missing = {period for period in tracked if period <= day and period not in verified}
    for period in missing:
        state.save(_CHAIN, period.isoformat(), 'source_error', message='未找到此前追踪的财报 ODS 记录')
    if missing:
        state.service(_CHAIN, 'source_error', f'{day} 缺少 {len(missing)} 个已追踪财报报告期')
    elif publication_failed:
        state.service(_CHAIN, 'publish_error', f'{day} 财报批次仍待核验或发布')
    else:
        state.service(_CHAIN, 'ok', f'{day} 已验证 {len(verified)} 个财报报告期')
    return not (missing or publication_failed)
