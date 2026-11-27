"""runner 的命令行参数定义。

flag 名称、help、choices、报错行为与历史版本保持一致，
smoke 离线测试与线上调度都依赖这些既有参数。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from scheduler.task_config import CHAINS, DAILY_TIMES, MONTHLY_CRON

# 调度器内部 tick 标识（含 APScheduler 不会直接配置的手工/恢复任务）
TICKS = (*MONTHLY_CRON, *DAILY_TIMES, "manual", "resume", "ads", "city-models", "ownership-research")


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        '--start-date', type=lambda value: datetime.fromisoformat(value).date(),
        help='历史起点（默认 2017-01-01）',
    )
    parser.add_argument(
        '--end-date', type=lambda value: datetime.fromisoformat(value).date(),
        help='历史审计/初始化截至业务日；不允许未来日期',
    )
    parser.add_argument(
        '--preview-city-models', metavar='OUTPUT_DIR',
        help='只读本地CSV生成城市车型核查清单，不连库、不确认归属',
    )
    parser.add_argument(
        "--merge-oem-aliases", nargs="+", metavar="OLD:CANONICAL",
        help="归并已确认的主机厂别名及销量关联",
    )
    parser.add_argument("--normalize-oem-names", action="store_true", help="按正式主机厂名更新已发布 DWD")
    parser.add_argument(
        "--reconcile-reviewed-companies", action="store_true",
        help="按已核验的主机厂归属修正已发布销量",
    )
    parser.add_argument("--tick", choices=TICKS, help=argparse.SUPPRESS)
    parser.add_argument(
        '--restore-national-history', metavar='REPORT_JSON',
        help='按 ODS 核对保留的全国 DWD 后恢复历史 DWS，未知归属保留 OTHER',
    )
    parser.add_argument(
        '--restore-city-history', metavar='REPORT_JSON',
        help='按保留 ODS 恢复城市独立 DWD；不接受网站新版本、不修改来源批次状态',
    )
    parser.add_argument(
        '--repair-futures-history', metavar='EVIDENCE_JSON',
        help='按已保存交易所证据增量修正期货异常及历史日历',
    )
    parser.add_argument(
        '--seed-reviewed-ads-models', metavar='REPORT_JSON',
        help='按已核验工信部车型身份增量建档 ADS 车型，不修改默认品牌桥接',
    )
    parser.add_argument(
        '--refresh-city-tiers', metavar='REPORT_JSON',
        help='按第一财经2025完整名单更新城市分级，未上榜为其他',
    )
    parser.add_argument('--load-reviewed-county-areas', metavar='REPORT_JSON')
    parser.add_argument('--seed-reviewed-private-companies', metavar='REPORT_JSON',
                        help='按已核实独立经营法人增量补公司归属，不发送飞书消息')
    parser.add_argument('--apply-research-exclusions', metavar='REPORT_JSON',
                        help='应用用户确认的34个非研究范围车企归属，不发送消息')
    return parser
