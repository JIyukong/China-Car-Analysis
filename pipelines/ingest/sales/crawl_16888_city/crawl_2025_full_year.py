# -*- coding: utf-8 -*-
"""
提取全年数据，支持断点续传，自动检测并重试未完成的月份
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 数据目录
DATA_DIR = Path(__file__).parent / "data"

# 要抓取的月份列表
def get_target_months():
    """获取目标月份列表"""
    return [
        "2023-01", "2023-02", "2023-03", "2023-04",
        "2023-05", "2023-06", "2023-07", "2023-08",
        "2023-09", "2023-10", "2023-11", "2023-12"
    ]

REST_MINUTES = 10
REST_SECONDS = REST_MINUTES * 60


def get_page_total(month_id: str) -> int | None:
    """通过浏览器获取月份总数据量"""
    try:
        from playwright.sync_api import sync_playwright

        url = f"https://xl.16888.com/city-{month_id}-0-0-0-0-0-1.html"
        with sync_playwright() as p:
            # 使用 headless=new 模式
            browser = p.chromium.launch(headless=True, args=["--headless=new"])
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # 提取总数据量
            text = page.inner_text("body")
            browser.close()

            import re
            match = re.search(r"共(\d+)", text)
            if match:
                return int(match.group(1))
    except Exception as e:
        print(f"  [WARN] 获取 {month_id} 总数据量失败: {e}")
    return None


def get_local_records(month_id: str) -> int:
    """获取本地已抓取的记录数"""
    csv_file = DATA_DIR / f"city_{month_id}.csv"
    if csv_file.exists():
        with open(csv_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return max(0, len(lines) - 1)  # 减去表头
    return 0


def check_incomplete_months(target_months: list[str]) -> list[tuple[str, int, int]]:
    """检查所有月份，找出未完成或断点的月份"""
    incomplete = []

    for month_id in target_months:
        csv_file = DATA_DIR / f"city_{month_id}.csv"
        if not csv_file.exists():
            print(f"  {month_id}: 无本地文件，需要抓取")
            incomplete.append((month_id, 0, 0))
            continue

        total = get_page_total(month_id)
        local = get_local_records(month_id)

        if total is None:
            print(f"  {month_id}: 无法获取总数据量，跳过检查")
            continue

        if local < total:
            print(f"  {month_id}: {local}/{total} 条 ({local/total*100:.1f}%)")
            incomplete.append((month_id, local, total))
        else:
            print(f"  {month_id}: {local}/{total} 条 [完成]")

    return incomplete


def run_crawl(target_month: str, resume: bool = True) -> bool:
    """执行单月份爬取"""
    cmd = [
        sys.executable,
        "scripts/crawl_16888_city/crawl_city.py",
        "--target", target_month,
        "--headless"
    ]
    if resume:
        cmd.append("--resume")

    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始提取: {target_month}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    target_months = get_target_months()

    print(f"\n{'#'*60}")
    print(f"# 车主之家城市上牌数据 - 全年提取")
    print(f"# 年份: 2023")
    print(f"# 总月份数: {len(target_months)}")
    print(f"# 每月份间隔: {REST_MINUTES}分钟")
    print(f"# 断点续传: 已启用")
    print(f"{'#'*60}\n")

    # 第一遍：检查并抓取所有月份
    print("=" * 60)
    print("【第一遍】检查并抓取所有月份")
    print("=" * 60)

    for i, month in enumerate(target_months, 1):
        print(f"\n>>> [{i}/{len(target_months)}] 处理月份: {month}")

        # 检查本地已有数据
        local = get_local_records(month)
        if local > 0:
            print(f"    本地已有: {local} 条，将使用断点续传")

        if run_crawl(month, resume=True):
            print(f"[OK] {month} 提取完成")
        else:
            print(f"[FAIL] {month} 提取失败")

        # 除了最后一个月份，都休息10分钟
        if i < len(target_months):
            next_month = target_months[i]
            print(f"\n[INFO] 休息{REST_MINUTES}分钟后检查数据完整性...")
            time.sleep(REST_SECONDS)

    # 第二遍及之后：检查并补全断点
    round_num = 2
    max_rounds = 10  # 最多重试10轮，防止无限循环

    while round_num <= max_rounds:
        print(f"\n{'='*60}")
        print(f"【第{round_num}遍】检查数据完整性")
        print(f"{'='*60}")

        incomplete = check_incomplete_months(target_months)

        if not incomplete:
            print("\n[OK] 所有月份数据完整，抓取完成！")
            break

        print(f"\n发现 {len(incomplete)} 个月份数据不完整，需要补全：")

        for month_id, local, total in incomplete:
            print(f"  - {month_id}: {local}/{total} 条")

        print(f"\n{'='*60}")
        print(f"【第{round_num}遍】补全断点数据")
        print(f"{'='*60}")

        for i, (month_id, local, total) in enumerate(incomplete, 1):
            print(f"\n>>> [{i}/{len(incomplete)}] 补全月份: {month_id}")
            print(f"    当前进度: {local}/{total} 条 ({local/total*100:.1f}%)")

            if run_crawl(month_id, resume=True):
                print(f"[OK] {month_id} 补全完成")
            else:
                print(f"[FAIL] {month_id} 补全失败")

            # 休息10分钟
            if i < len(incomplete):
                print(f"\n[INFO] 休息{REST_MINUTES}分钟后继续...")
                time.sleep(REST_SECONDS)

        round_num += 1

    # 最终汇总
    print(f"\n{'#'*60}")
    print(f"# 最终数据检查")
    print(f"{'#'*60}")

    total_records = 0
    complete_count = 0

    for month_id in target_months:
        local = get_local_records(month_id)
        total = get_page_total(month_id)
        total_records += local

        if total and local >= total:
            complete_count += 1
            status = "[完成]"
        elif total:
            status = f"[{local}/{total} {local/total*100:.1f}%]"
        else:
            status = "[未知]"

        print(f"  {month_id}: {local:,} 条 {status}")

    print(f"\n{'#'*60}")
    print(f"# 抓取汇总")
    print(f"# 总记录数: {total_records:,} 条")
    print(f"# 完成月份: {complete_count}/{len(target_months)}")
    print(f"# 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")


if __name__ == "__main__":
    main()
