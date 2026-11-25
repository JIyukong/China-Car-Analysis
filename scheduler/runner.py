"""定时启动或手动运行同一套 ETL。"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scheduler.runtime import business_period, chain_steps
from scheduler.task_config import CHAINS, DAILY_TIMES, MONTHLY_CRON

ROOT = Path(__file__).resolve().parents[1]
TIMEZONE = "Asia/Shanghai"
TICKS = (*MONTHLY_CRON, *DAILY_TIMES, "manual", "resume", "ads", "city-models", "ownership-research")


def print_plan(chain: str | None = None, period: str | None = None, *, from_csv=False, step=None, resume=False) -> None:
    for name in (chain,) if chain else CHAINS:
        if name == "daily":
            steps = (*chain_steps(name, step=step), "行情覆盖对账")
        else:
            source = "已验收 ODS" if resume else "CSV 与来源清单" if from_csv else "16888 采集"
            output = "全国 DWD / DWS 发布" if name == "style" else "城市 DWD 发布"
            steps = (source, "验收版本对账", output) if resume else (source, "来源对账", "ODS", output)
        label = f"{name} {period}" if period else name
        print(f"{label}: {' -> '.join(steps)}")
        if not chain:
            cron = MONTHLY_CRON.get(name)
            if cron:
                print(f"  北京时间每日 {cron['hour']:02}:{cron['minute']:02} 检查新批次与未完成月份")
            else:
                for task, (hour, minute) in DAILY_TIMES.items():
                    print(f"  北京时间每日 {hour:02}:{minute:02} {task}")
    if not chain:
        print("北京时间每 5 分钟同步飞书补录和独立 ADS 维表，并恢复已验收批次")


def run_scheduled(task: str) -> None:
    from scheduler.alert import send_alert

    args = [sys.executable, "-m", "scheduler.runner", "--tick", task]
    timeout_seconds = 21600 if task in MONTHLY_CRON else 7200
    try:
        result = subprocess.run(args, cwd=ROOT, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        send_alert("ERROR", task, business_period("daily", None), "ETL 执行超时，请查看日志")
        raise
    if result.returncode:
        send_alert("ERROR", task, business_period("daily", None), f"ETL 执行失败，退出码 {result.returncode}，请查看日志")


def create_scheduler(*, catch_up: bool = False):
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(
        timezone=TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    startup = {"next_run_time": datetime.now(ZoneInfo(TIMEZONE))} if catch_up else {}
    for chain, cron in MONTHLY_CRON.items():
        scheduler.add_job(run_scheduled, "cron", args=(chain,), id=chain, **cron, **startup)
    for step, (hour, minute) in DAILY_TIMES.items():
        scheduler.add_job(
            run_scheduled,
            "cron",
            args=(step,),
            id=step,
            hour=hour,
            minute=minute,
            **startup,
        )
    for task in ("manual", "resume"):
        scheduler.add_job(run_scheduled, "cron", args=(task,), id=task, minute="*/5", **startup)
    return scheduler


def run_tick(task: str) -> bool:
    from scheduler.workflow import State, process_month, resume_ready, target_months

    state = State()
    now = datetime.now(ZoneInfo(TIMEZONE))
    if task == 'ownership-research':
        from scheduler.ownership_research import run
        return run(state)
    if task == 'city-models':
        from scheduler.city_model_sync import run
        from scheduler.manual_workflow import poll

        return run(state) and poll(state)
    if task in MONTHLY_CRON:
        periods = target_months(state, task, now.date())
        last = business_period(task, None)
        results = []
        # A multi-year backlog must not delay the newly due month for hours.
        # Keep the historical order after checking the latest month first.
        ordered = [last, *(period for period in periods if period != last)]
        for period in ordered:
            batch = state.batch(task, period)
            if period != last and batch.get("digest") and batch["status"] == "waiting_manual":
                continue
            results.append(process_month(state, task, period, prefer_resume=True))
        return all(results)
    if task == "manual":
        from scheduler.ads_workflow import poll as poll_ads
        from scheduler.manual_workflow import poll

        from scheduler.city_model_sync import run
        from base_infra.config import get_settings
        city_ok = run(state) if get_settings().ETL_CITY_MODEL_SYNC_ENABLED else True
        manual_ok = poll(state)
        ads_ok = poll_ads(state)
        return city_ok and manual_ok and ads_ok
    if task == "ads":
        from scheduler.ads_workflow import poll

        return poll(state)
    if task == "resume":
        okay = resume_ready(state)
        state.service("resume", "ok" if okay else "incomplete")
        state.export()
        return okay
    from scheduler.market_workflow import process_market

    hour, minute = DAILY_TIMES[task]
    day = now.date() if (now.hour, now.minute) >= (hour, minute) else now.date() - timedelta(days=1)
    return process_market(state, task, day, catch_up=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CarApp 采集与 ETL")
    parser.add_argument("--chain", choices=CHAINS)
    parser.add_argument("--period", help="月链 YYYY-MM，日链 YYYY-MM-DD")
    parser.add_argument("--from-csv", action="store_true", help="用目标月的本地 CSV，跳过爬取")
    parser.add_argument("--resume", action="store_true", help="对账并重跑已验收月份的数仓发布")
    parser.add_argument("--step", choices=DAILY_TIMES, help="只运行一项独立的日链任务")
    parser.add_argument("--plan", action="store_true", help="离线预览步骤")
    parser.add_argument("--status", action="store_true", help="读取本地巡检摘要，不连库")
    parser.add_argument('--repair-stock-dates', metavar='CSV', help='按 code,date 清单补采 A/H 股 ODS')
    parser.add_argument('--stock-repair-evidence', metavar='JSON', help='登记已核实停牌及恢复备源遗漏的原成交额')
    parser.add_argument('--audit-stock-history', action='store_true', help='只读对账 A/H 股全历史及原价/因子验收证据')
    parser.add_argument('--rebase-stock-history', action='store_true', help='全历史单源原价/因子验收后事务切换后复权')
    parser.add_argument('--adjust', choices=['hfq'], help='历史切换仅支持后复权 hfq')
    parser.add_argument('--start-date', type=lambda value: datetime.fromisoformat(value).date(), help='历史起点（默认 2017-01-01）')
    parser.add_argument('--end-date', type=lambda value: datetime.fromisoformat(value).date(), help='历史审计/初始化截至业务日；不允许未来日期')
    parser.add_argument('--preview-city-models', metavar='OUTPUT_DIR',
                        help='只读本地CSV生成城市车型核查清单，不连库、不确认归属')
    parser.add_argument("--merge-oem-aliases", nargs="+", metavar="OLD:CANONICAL",
                        help="归并已确认的主机厂别名及销量关联")
    parser.add_argument("--normalize-oem-names", action="store_true",
                        help="按正式主机厂名更新已发布 DWD")
    parser.add_argument("--reconcile-reviewed-companies", action="store_true",
                        help="按已核验的主机厂归属修正已发布销量")
    parser.add_argument("--tick", choices=TICKS, help=argparse.SUPPRESS)
    parser.add_argument('--restore-national-history', metavar='REPORT_JSON',
                        help='按 ODS 核对保留的全国 DWD 后恢复历史 DWS，未知归属保留 OTHER')
    parser.add_argument('--restore-city-history', metavar='REPORT_JSON',
                        help='按保留 ODS 恢复城市独立 DWD；不接受网站新版本、不修改来源批次状态')
    parser.add_argument('--repair-futures-history', metavar='EVIDENCE_JSON',
                        help='按已保存交易所证据增量修正期货异常及历史日历')
    parser.add_argument('--seed-reviewed-ads-models', metavar='REPORT_JSON',
                        help='按已核验工信部车型身份增量建档 ADS 车型，不修改默认品牌桥接')
    parser.add_argument('--refresh-city-tiers', metavar='REPORT_JSON', help='按第一财经2025完整名单更新城市分级，未上榜为其他')
    parser.add_argument('--load-reviewed-county-areas', metavar='REPORT_JSON')
    args = parser.parse_args(argv)
    if args.load_reviewed_county_areas:
        others=vars(args).copy()
        others.pop('load_reviewed_county_areas')
        if any(others.values()): parser.error('--load-reviewed-county-areas 需要单独运行')
        from scheduler.county_areas import run
        print(json.dumps(run(args.load_reviewed_county_areas),ensure_ascii=False,default=str))
        return 0
    if args.refresh_city_tiers:
        others=vars(args).copy()
        others.pop('refresh_city_tiers')
        if any(others.values()):
            parser.error('--refresh-city-tiers 需要单独运行')
        from scheduler.area_tiers import run
        report=run(args.refresh_city_tiers)
        print(json.dumps({'verified':report['verified'],'updated':report['updated']}))
        return 0
    if args.seed_reviewed_ads_models:
        others = vars(args).copy()
        others.pop('seed_reviewed_ads_models')
        if any(others.values()):
            parser.error('--seed-reviewed-ads-models 需要单独运行')
        from scheduler.reviewed_ads_catalog import run
        try:
            report = run(args.seed_reviewed_ads_models)
            print(json.dumps(report, ensure_ascii=False, default=str))
            return 0
        except Exception:
            import logging
            logging.getLogger(__name__).exception('已核验 ADS 车型建档失败')
            return 1
    if args.restore_city_history:
        others = vars(args).copy()
        others.pop('restore_city_history')
        others.pop('period')
        if any(others.values()):
            parser.error('--restore-city-history 需要单独运行')
        if args.period is not None:
            try:
                if len(args.period) != 7:
                    raise ValueError('month format')
                datetime.strptime(args.period + '-01', '%Y-%m-%d')
            except ValueError:
                parser.error('--restore-city-history --period 需要 YYYY-MM')
        from base_infra.config import get_settings
        if get_settings().DB_USER.strip().casefold() in {'', 'root', 'admin', 'administrator'}:
            parser.error('请配置独立 ETL 账号')
        from scheduler.city_history import run
        try:
            report = run(args.restore_city_history, period=args.period)
            return 0 if report['complete'] else 1
        except Exception:
            import logging
            logging.getLogger(__name__).exception('保留 ODS 城市数仓恢复失败')
            return 1
    if args.repair_futures_history:
        others = vars(args).copy()
        others.pop('repair_futures_history')
        if any(others.values()):
            parser.error('--repair-futures-history 需要单独运行')
        from base_infra.config import get_settings
        if get_settings().DB_USER.strip().casefold() in {'', 'root', 'admin', 'administrator'}:
            parser.error('请配置独立 ETL 账号')
        from scheduler.futures_repair import run
        from scheduler.workflow import State
        try:
            with State().lock('load_futures_daily', timeout=10), State().lock('warehouse', timeout=120):
                report = run(args.repair_futures_history)
            print(json.dumps(report, ensure_ascii=False, default=str))
            return 0
        except Exception:
            import logging
            logging.getLogger(__name__).exception('期货历史证据修复失败')
            return 1
    if args.restore_national_history:
        others = vars(args).copy()
        others.pop('restore_national_history')
        if any(others.values()):
            parser.error('--restore-national-history 需要单独运行')
        from base_infra.config import get_settings
        if get_settings().DB_USER.strip().casefold() in {'', 'root', 'admin', 'administrator'}:
            parser.error('请配置独立 ETL 账号')
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
            import logging
            logging.getLogger(__name__).exception('全国历史对账恢复失败')
            return 1
    if args.audit_stock_history or args.rebase_stock_history:
        if (args.audit_stock_history and args.rebase_stock_history) or any((args.chain,args.period,args.step,
                args.tick,args.plan,args.status,args.resume,args.from_csv,args.preview_city_models,
                args.merge_oem_aliases,args.normalize_oem_names,args.reconcile_reviewed_companies,
                args.repair_stock_dates,args.stock_repair_evidence)):
            parser.error('历史审计、后复权切换需要分别独立运行')
        if args.rebase_stock_history and args.adjust != 'hfq':
            parser.error('历史切换必须明确 --adjust hfq')
        from base_infra.config import get_settings
        if get_settings().DB_USER.strip().casefold() in {'','root','admin','administrator'}:
            parser.error('请配置独立 ETL 账号')
        from scheduler.stock_history import START, audit, run
        if args.start_date is not None and args.start_date != START:
            parser.error('当前业务历史起点固定为 2017-01-01')
        now = datetime.now(ZoneInfo(TIMEZONE))
        last = now.date() if now.hour >= 18 else now.date()-timedelta(days=1)
        if args.end_date is not None:
            if not START<=args.end_date<=last:
                parser.error('--end-date 必须在业务历史起点与当前已结束的业务日之间')
            last=args.end_date
        try:
            if args.audit_stock_history:
                _, report, _ = audit(START, last)
                from scheduler.stock_history import summarize
                print(json.dumps(summarize(report), ensure_ascii=False))
                return 0 if report['complete'] else 1
            return 0 if run(last=last, rebase=True) else 1
        except Exception:
            import logging
            logging.getLogger(__name__).exception('股票全历史审计或切换失败，未完成验收')
            return 1
    if args.adjust or args.start_date or args.end_date:
        parser.error('--adjust/--start-date/--end-date 仅用于历史审计或后复权切换')
    if args.repair_stock_dates or args.stock_repair_evidence:
        if args.repair_stock_dates and args.stock_repair_evidence:
            parser.error('股票补采与证据登记需分别运行')
        if any((args.chain, args.period, args.step, args.tick, args.plan, args.status, args.resume,
                args.from_csv, args.preview_city_models, args.merge_oem_aliases,
                args.normalize_oem_names, args.reconcile_reviewed_companies)):
            parser.error('--repair-stock-dates 需要单独运行')
        from base_infra.config import get_settings
        if get_settings().DB_USER.strip().casefold() in {'', 'root', 'admin', 'administrator'}:
            parser.error('请配置独立 ETL 账号')
        from scheduler.stock_repair import apply_evidence, repair
        if args.stock_repair_evidence:
            return 0 if apply_evidence(args.stock_repair_evidence) else 1
        return 0 if repair(args.repair_stock_dates) else 1
    if args.preview_city_models:
        if args.chain != 'city' or any((args.plan, args.status, args.tick, args.resume, args.from_csv,
                args.step, args.normalize_oem_names, args.merge_oem_aliases, args.reconcile_reviewed_companies)):
            parser.error('--preview-city-models 只能与 --chain city 和可选 --period 同用')
        from pipelines.dim.city_model_preview import preview, write_preview

        try:
            root = ROOT / 'pipelines/ingest/sales'
            report = preview(root / 'crawl_16888_city/data', root / 'crawl_16888_style/data', period=args.period)
            output = write_preview(report, args.preview_city_models)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps({'output_dir': str(output), **report['summary']}, ensure_ascii=False))
        return 0
    if args.normalize_oem_names and any((
        args.status, args.plan, args.tick, args.chain, args.period, args.from_csv, args.step,
        args.resume, args.merge_oem_aliases, args.reconcile_reviewed_companies,
    )):
        parser.error("--normalize-oem-names 需要单独运行")
    if args.reconcile_reviewed_companies and any((args.status, args.plan, args.tick, args.chain,
                                                  args.period, args.from_csv, args.step, args.resume,
                                                  args.merge_oem_aliases)):
        parser.error("--reconcile-reviewed-companies 需要单独运行")
    if args.merge_oem_aliases and any((args.status, args.plan, args.tick, args.chain, args.period,
                                       args.from_csv, args.step, args.resume)):
        parser.error("--merge-oem-aliases 需要单独运行")

    if not args.chain and (args.period or args.from_csv or args.step):
        parser.error("--period、--from-csv、--step 需要 --chain")
    if args.resume and (args.chain not in MONTHLY_CRON or not args.period or args.from_csv):
        parser.error("--resume 需要月链和明确的 --period，且不能与 --from-csv 同用")
    if sum(bool(value) for value in (args.status, args.tick, args.chain or args.plan)) > 1:
        parser.error("--status、--tick 和链路运行参数互斥")
    try:
        period = business_period(args.chain, args.period) if args.chain else None
        if args.chain:
            chain_steps(args.chain, from_csv=args.from_csv, step=args.step)
    except ValueError as exc:
        parser.error(str(exc))

    if args.plan:
        print_plan(args.chain, period, from_csv=args.from_csv, step=args.step, resume=args.resume)
        return 0
    if args.status:
        try:
            status = (ROOT / "logs" / "etl" / "status.json").read_text(encoding="utf-8")
            print(json.dumps(json.loads(status), ensure_ascii=False, indent=2))
        except (OSError, ValueError):
            print("巡检摘要不可用，请检查常驻服务和状态文件", file=sys.stderr)
            return 1
        return 0

    from base_infra.config import get_settings
    from base_infra.logging_config import get_logger

    logger = get_logger(__name__)
    if get_settings().DB_USER.strip().casefold() in {"", "root", "admin", "administrator"}:
        logger.error("请为 CarApp 配置独立的 ETL 数据库账号")
        return 1
    if args.normalize_oem_names:
        from scheduler.oem_normalize import normalize_oem_names
        from scheduler.workflow import State

        try:
            with State().lock("warehouse", timeout=10):
                count = normalize_oem_names()
            print(json.dumps({"normalized_dwd_rows": count}, ensure_ascii=False))
            return 0
        except Exception:
            logger.exception("主机厂名称统一失败")
            return 1
    if args.reconcile_reviewed_companies:
        from scheduler.company_reconcile import reconcile_reviewed_companies
        from scheduler.workflow import State

        try:
            with State().lock("warehouse", timeout=10):
                report = reconcile_reviewed_companies()
            print(json.dumps(report, ensure_ascii=False))
            return 0
        except Exception:
            logger.exception("公司归属修正失败")
            return 1
    if args.merge_oem_aliases:
        from scheduler.oem_merge import merge_oems
        from scheduler.workflow import State

        try:
            pairs = [tuple(map(int, pair.split(":"))) for pair in args.merge_oem_aliases]
            with State().lock("warehouse", timeout=10):
                counts = merge_oems(pairs)
            print(json.dumps(counts, ensure_ascii=False))
            return 0
        except Exception:
            logger.exception("主机厂归并失败")
            return 1
    if args.chain or args.tick:
        try:
            if args.tick:
                return 0 if run_tick(args.tick) else 1
            from scheduler.workflow import State, process_month

            state = State()
            if args.chain in MONTHLY_CRON:
                if args.resume:
                    batch = state.batch(args.chain, period)
                    if not batch.get("digest") or batch["status"] in {"collecting", "waiting_source", "source_error"}:
                        logger.error("%s %s 缺少当前可用的来源验收版本，请先运行采集", args.chain, period)
                        return 1
                    state.enqueue(args.chain, period)
                return 0 if process_month(state, args.chain, period, from_csv=args.from_csv, resume=args.resume) else 1
            from scheduler.market_workflow import process_market

            tasks = chain_steps("daily", step=args.step)
            results = [process_market(state, task, datetime.fromisoformat(period).date()) for task in tasks]
            return 0 if all(results) else 1
        except Exception:
            logger.exception("ETL 启动失败")
            return 1

    scheduler = create_scheduler(catch_up=True)
    for job in scheduler.get_jobs():
        logger.info("定时任务 %s: %s", job.id, job.trigger)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
