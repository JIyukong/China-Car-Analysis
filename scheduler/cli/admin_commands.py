"""独立管理/修复类子命令的处理器。

每个处理器对应历史 runner.main 长 if 链里的一个分支，互斥校验、报错文案、
返回码与异常处理均保持原语义。返回 None 表示当前参数不属于管理子命令，
应交回 runner 的常规链路逻辑继续处理。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scheduler.cli.guards import reject_other_flags, require_etl_account

logger = logging.getLogger(__name__)


def _root() -> Path:
    """延迟读取 runner.ROOT，使测试 monkeypatch runner.ROOT 仍然生效。"""
    from scheduler import runner

    return runner.ROOT


def _load_county_areas(parser, args) -> int:
    reject_other_flags(parser, args, allowed=("load_reviewed_county_areas",),
                       message='--load-reviewed-county-areas 需要单独运行')
    from scheduler.county_areas import run

    print(json.dumps(run(args.load_reviewed_county_areas), ensure_ascii=False, default=str))
    return 0


def _refresh_city_tiers(parser, args) -> int:
    reject_other_flags(parser, args, allowed=("refresh_city_tiers",),
                       message='--refresh-city-tiers 需要单独运行')
    from scheduler.area_tiers import run

    report = run(args.refresh_city_tiers)
    print(json.dumps({'verified': report['verified'], 'updated': report['updated']}))
    return 0


def _seed_reviewed_ads_models(parser, args) -> int:
    reject_other_flags(parser, args, allowed=("seed_reviewed_ads_models",),
                       message='--seed-reviewed-ads-models 需要单独运行')
    from scheduler.reviewed_ads_catalog import run

    try:
        report = run(args.seed_reviewed_ads_models)
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0
    except Exception:
        logger.exception('已核验 ADS 车型建档失败')
        return 1


def _restore_city_history(parser, args) -> int:
    reject_other_flags(
        parser, args, allowed=("restore_city_history", "period"),
        message='--restore-city-history 需要单独运行',
    )
    if args.period is not None:
        try:
            if len(args.period) != 7:
                raise ValueError('month format')
            datetime.strptime(args.period + '-01', '%Y-%m-%d')
        except ValueError:
            parser.error('--restore-city-history --period 需要 YYYY-MM')
    require_etl_account(parser)
    from scheduler.city_history import run

    try:
        report = run(args.restore_city_history, period=args.period)
        return 0 if report['complete'] else 1
    except Exception:
        logger.exception('保留 ODS 城市数仓恢复失败')
        return 1


def _repair_futures_history(parser, args) -> int:
    reject_other_flags(parser, args, allowed=("repair_futures_history",),
                       message='--repair-futures-history 需要单独运行')
    require_etl_account(parser)
    from scheduler.futures_repair import run
    from scheduler.workflow import State

    try:
        with State().lock('load_futures_daily', timeout=10), State().lock('warehouse', timeout=120):
            report = run(args.repair_futures_history)
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0
    except Exception:
        logger.exception('期货历史证据修复失败')
        return 1


def _restore_national_history(parser, args) -> int:
    reject_other_flags(parser, args, allowed=("restore_national_history",),
                       message='--restore-national-history 需要单独运行')
    require_etl_account(parser)
    from scheduler.national_history import restore
    from scheduler.workflow import State

    try:
        with State().lock('warehouse', timeout=10):
            report = restore()
        output = Path(args.restore_national_history)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception:
        logger.exception('全国历史对账恢复失败')
        return 1


def _stock_history(parser, args) -> int:
    if (args.audit_stock_history and args.rebase_stock_history) or any((
        args.chain, args.period, args.step, args.tick, args.plan, args.status, args.resume,
        args.from_csv, args.preview_city_models, args.merge_oem_aliases, args.normalize_oem_names,
        args.reconcile_reviewed_companies, args.repair_stock_dates, args.stock_repair_evidence,
    )):
        parser.error('历史审计、后复权切换需要分别独立运行')
    if args.rebase_stock_history and args.adjust != 'hfq':
        parser.error('历史切换必须明确 --adjust hfq')
    require_etl_account(parser)
    from scheduler.stock_history import START, audit, run

    if args.start_date is not None and args.start_date != START:
        parser.error('当前业务历史起点固定为 2017-01-01')
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    last = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    if args.end_date is not None:
        if not START <= args.end_date <= last:
            parser.error('--end-date 必须在业务历史起点与当前已结束的业务日之间')
        last = args.end_date
    try:
        if args.audit_stock_history:
            _, report, _ = audit(START, last)
            from scheduler.stock_history import summarize

            print(json.dumps(summarize(report), ensure_ascii=False))
            return 0 if report['complete'] else 1
        return 0 if run(last=last, rebase=True) else 1
    except Exception:
        logger.exception('股票全历史审计或切换失败，未完成验收')
        return 1


def _stock_repair(parser, args) -> int:
    if args.repair_stock_dates and args.stock_repair_evidence:
        parser.error('股票补采与证据登记需分别运行')
    if any((args.chain, args.period, args.step, args.tick, args.plan, args.status, args.resume,
            args.from_csv, args.preview_city_models, args.merge_oem_aliases,
            args.normalize_oem_names, args.reconcile_reviewed_companies)):
        parser.error('--repair-stock-dates 需要单独运行')
    require_etl_account(parser)
    from scheduler.stock_repair import apply_evidence, repair

    if args.stock_repair_evidence:
        return 0 if apply_evidence(args.stock_repair_evidence) else 1
    return 0 if repair(args.repair_stock_dates) else 1


def _preview_city_models(parser, args) -> int:
    if args.chain != 'city' or any((args.plan, args.status, args.tick, args.resume, args.from_csv,
                                    args.step, args.normalize_oem_names, args.merge_oem_aliases,
                                    args.reconcile_reviewed_companies)):
        parser.error('--preview-city-models 只能与 --chain city 和可选 --period 同用')
    import sys

    from pipelines.dim.city_model_preview import preview, write_preview

    try:
        root = _root() / 'pipelines/ingest/sales'
        report = preview(root / 'crawl_16888_city/data', root / 'crawl_16888_style/data', period=args.period)
        output = write_preview(report, args.preview_city_models)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({'output_dir': str(output), **report['summary']}, ensure_ascii=False))
    return 0


# 子命令参数名 -> 处理器，顺序即历史 runner.main 中的分支先后
_STANDALONE_HANDLERS = (
    ("load_reviewed_county_areas", _load_county_areas),
    ("refresh_city_tiers", _refresh_city_tiers),
    ("seed_reviewed_ads_models", _seed_reviewed_ads_models),
    ("restore_city_history", _restore_city_history),
    ("repair_futures_history", _repair_futures_history),
    ("restore_national_history", _restore_national_history),
)


def dispatch_admin(parser, args) -> int | None:
    """按历史顺序处理管理类子命令；不匹配时返回 None。"""
    for flag, handler in _STANDALONE_HANDLERS:
        if getattr(args, flag):
            return handler(parser, args)
    if args.audit_stock_history or args.rebase_stock_history:
        return _stock_history(parser, args)
    if args.adjust or args.start_date or args.end_date:
        parser.error('--adjust/--start-date/--end-date 仅用于历史审计或后复权切换')
    if args.repair_stock_dates or args.stock_repair_evidence:
        return _stock_repair(parser, args)
    if args.preview_city_models:
        return _preview_city_models(parser, args)
    return None
