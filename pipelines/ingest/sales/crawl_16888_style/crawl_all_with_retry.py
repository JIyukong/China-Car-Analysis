# -*- coding: utf-8 -*-
"""车型销量数据 - 全量历史抓取调度脚本（带断点续传 + 重试）。

循环调用 ``crawl_style.py --months all --headless --resume`` 抓取
2017-01 至今的全部历史月份，支持最多 20 次重试，每次重试间隔 5 分钟。

设计原理:
    - ``crawl_style.py`` 自带断点续传（``--resume``），已完成的月份会自动跳过，
      未完成的月份从断点页继续，因此本脚本只需反复调用即可逐步补全数据。
    - 单次调用可能因网络波动/反爬封禁/超时等原因中途失败，
      本脚本捕获异常后休息 ``REST_MINUTES`` 分钟再次调用，最多 ``MAX_ROUNDS`` 轮。
    - 每轮结束后打印各月份的本地数据完整性检查结果，便于监控进度。

用法:
    python scripts/crawl_16888_style/crawl_all_with_retry.py

    # 自定义参数
    python scripts/crawl_16888_style/crawl_all_with_retry.py --max-rounds 30 --rest-minutes 3
"""
import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 数据目录
DATA_DIR = Path(__file__).parent / "data"

# 默认参数
DEFAULT_MAX_ROUNDS = 20                          # 最大重试轮数           # 防止无限循环
DEFAULT_REST_MINUTES = 5                         # 每轮间隔休息分钟数     # 反爬冷却


def get_target_months() -> list[str]:
    """生成 2017-01 至当前月的目标月份列表（倒序，最新在前）。

    :returns: 月份 ID 列表，形如 ["2026-07", "2026-06", ..., "2017-01"]
    """
    from datetime import datetime as dt
    today = dt.now()
    months: list[str] = []
    y, m = today.year, today.month
    while (y, m) >= (2017, 1):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return months


def get_local_records(month_id: str) -> int:
    """获取本地已抓取的记录数（CSV 行数 - 表头）。

    :param month_id: 月份 ID，如 "2024-01"
    :returns: 本地记录数，文件不存在返回 0
    """
    csv_file = DATA_DIR / f"style_{month_id}.csv"
    if csv_file.exists():
        with open(csv_file, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
            return max(0, len(lines) - 1)                            # 减去表头行
    return 0


def is_month_complete(month_id: str) -> bool:
    """判断某月份数据是否完整（本地记录数 > 0 且有对应 JSON 汇总）。

    判据: CSV 文件存在且记录数 > 0，且 JSON 文件存在（JSON 在月份抓取完成后才写）。

    :param month_id: 月份 ID
    :returns: 完整返回 True，否则 False
    """
    csv_file = DATA_DIR / f"style_{month_id}.csv"
    json_file = DATA_DIR / f"style_{month_id}.json"
    if not csv_file.exists() or not json_file.exists():
        return False
    return get_local_records(month_id) > 0


def check_completion(target_months: list[str]) -> tuple[int, int, list[str]]:
    """检查全部目标月份的完成情况。

    :param target_months: 目标月份列表
    :returns: (完成数, 总数, 未完成月份列表)
    """
    completed = 0
    incomplete: list[str] = []
    for month_id in target_months:
        if is_month_complete(month_id):
            completed += 1
        else:
            incomplete.append(month_id)
    return completed, len(target_months), incomplete


def print_progress_report(target_months: list[str]) -> None:
    """打印各月份的本地数据情况。

    :param target_months: 目标月份列表
    """
    print(f"\n{'─' * 60}")
    print("本地数据进度报告")
    print(f"{'─' * 60}")
    for month_id in target_months:
        local = get_local_records(month_id)
        json_exists = (DATA_DIR / f"style_{month_id}.json").exists()
        if local > 0 and json_exists:
            status = "[完成]"
        elif local > 0:
            status = f"[断点 {local} 条]"
        else:
            status = "[未抓取]"
        print(f"  {month_id}: {local:>6} 条 {status}")
    print(f"{'─' * 60}")


def run_crawl_round(resume: bool = True) -> bool:
    """执行一轮全量抓取（调用 crawl_style.py 子进程）。

    :param resume: 是否启用断点续传
    :returns: 子进程成功返回 True，失败返回 False
    """
    cmd = [
        sys.executable,
        "scripts/crawl_16888_style/crawl_style.py",
        "--months", "all",
        "--headless",
    ]
    if resume:
        cmd.append("--resume")

    print(f"\n{'=' * 60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始执行抓取子进程")
    print(f"命令: {' '.join(cmd)}")
    print(f"{'=' * 60}")

    try:
        result = subprocess.run(cmd, capture_output=False)
        return result.returncode == 0
    except Exception as exc:
        print(f"[ERROR] 子进程执行异常: {exc}")
        return False


def main() -> None:
    """主调度流程: 循环抓取 + 重试 + 休息。

    流程:
        1. 生成目标月份列表（2017-01 至今）
        2. 循环最多 ``max_rounds`` 轮:
           a. 检查完成情况，全部完成则退出
           b. 执行一轮抓取（带断点续传）
           c. 打印进度报告
           d. 若未完成，休息 ``rest_minutes`` 分钟后进入下一轮
    """
    parser = argparse.ArgumentParser(
        description="车型销量数据全量抓取调度脚本（断点续传 + 重试）",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help=f"最大重试轮数（默认 {DEFAULT_MAX_ROUNDS}）",
    )
    parser.add_argument(
        "--rest-minutes",
        type=int,
        default=DEFAULT_REST_MINUTES,
        help=f"每轮间隔休息分钟数（默认 {DEFAULT_REST_MINUTES}）",
    )
    args = parser.parse_args()

    max_rounds = args.max_rounds
    rest_seconds = args.rest_minutes * 60

    target_months = get_target_months()

    print(f"\n{'#' * 60}")
    print(f"# 车型销量数据 - 全量历史抓取（断点续传 + 重试）")
    print(f"# 年份范围: 2017-01 至今")
    print(f"# 总月份数: {len(target_months)}")
    print(f"# 最大重试: {max_rounds} 轮")
    print(f"# 每轮休息: {args.rest_minutes} 分钟")
    print(f"# 断点续传: 已启用")
    print(f"{'#' * 60}\n")

    # 初始进度检查
    completed, total, incomplete = check_completion(target_months)
    print(f"初始状态: {completed}/{total} 个月份已完成，{len(incomplete)} 个待抓取")

    if completed == total:
        print("\n[OK] 所有月份数据已完整，无需抓取！")
        return

    # 主循环
    for round_num in range(1, max_rounds + 1):
        print(f"\n{'#' * 60}")
        print(f"# 第 {round_num}/{max_rounds} 轮抓取开始")
        print(f"# 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#' * 60}")

        # 执行一轮抓取
        success = run_crawl_round(resume=True)

        # 抓取后进度检查
        completed, total, incomplete = check_completion(target_months)
        print(f"\n第 {round_num} 轮结束: {completed}/{total} 个月份已完成")

        # 打印详细进度报告
        print_progress_report(target_months)

        # 全部完成则退出
        if completed == total:
            print(f"\n{'#' * 60}")
            print(f"# [OK] 全部 {total} 个月份数据已完整！")
            print(f"# 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'#' * 60}")
            return

        # 未完成，休息后继续（最后一轮不休息）
        if round_num < max_rounds:
            print(f"\n[INFO] 尚有 {len(incomplete)} 个月份未完成，休息 {args.rest_minutes} 分钟后继续第 {round_num + 1} 轮 ...")
            print(f"[INFO] 未完成月份: {incomplete[:10]}{'...' if len(incomplete) > 10 else ''}")
            time.sleep(rest_seconds)

    # 重试耗尽
    print(f"\n{'#' * 60}")
    print(f"# [结束] 已达最大重试轮数 ({max_rounds})")
    print(f"# 最终完成: {completed}/{total} 个月份")
    print(f"# 未完成月份: {incomplete}")
    print(f"# 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#' * 60}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n[中断] 用户手动终止，进度已保存，可重新运行继续。")
        sys.exit(130)
