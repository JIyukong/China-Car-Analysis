"""通过统一月度验收流程重放销量 CSV。"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def _load_ods(chain: str, business_key: str | None) -> bool:
    if not business_key:
        logger.error("%s ODS 重放需要指定 YYYY-MM 月份", chain)
        return False

    from scheduler.monthly_source import collect_month

    try:
        collect_month(chain, business_key, from_csv=True)
        return True
    except Exception:
        logger.exception("%s %s ODS 重放失败", chain, business_key)
        return False


def load_style_ods(business_key: str | None = None) -> bool:
    """验收并写入指定月份的车型销量 CSV。"""
    return _load_ods("style", business_key)


def load_city_ods(business_key: str | None = None) -> bool:
    """验收并写入指定月份的城市销量 CSV。"""
    return _load_ods("city", business_key)


def replay_cli(chain: str | None = None) -> int:
    """兼容旧命令名，使用统一入口重放单个月份。"""
    from scheduler.runner import main

    selector = "--chain {style,city} " if chain is None else ""
    command = f"python -m scheduler.runner --chain {chain or '{style,city}'} --period YYYY-MM --from-csv"
    parser = argparse.ArgumentParser(
        description="重放指定月份的 CSV 与来源验收清单",
        usage=f"%(prog)s {selector}--period YYYY-MM\n       {command}",
        allow_abbrev=False,
    )
    if chain is None:
        parser.add_argument("--chain", choices=("style", "city"), required=True)
    parser.add_argument("--period", metavar="YYYY-MM", required=True)
    args = parser.parse_args()
    return main(["--chain", chain or args.chain, "--period", args.period, "--from-csv"])


if __name__ == "__main__":
    raise SystemExit(replay_cli())
