"""Capture one fresh monthly 16888 snapshot in a bounded browser process."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time
from importlib import import_module
from pathlib import Path

from base_infra.config import get_settings
from base_infra.logging_config import get_logger
from scheduler.runtime import AlreadyLoaded, business_period

logger = get_logger(__name__)

_POPEN_TIMEOUT_SEC = 170 * 60
_TAIL_CHARS = 4000


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """强杀子进程及其进程组（连同 Playwright/Chromium 孙进程一起收）。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("taskkill 失败 pid=%s err=%s", proc.pid, exc)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            logger.warning("killpg 失败 pid=%s err=%s", proc.pid, exc)


def crawl_snapshot(chain: str, period: str, path: Path) -> None:
    """Launch an isolated capture without consulting existing CSV or crawl progress."""
    root = Path(get_settings().PROJECT_ROOT)
    command = [sys.executable, "-m", "scheduler.jobs.crawl_16888_job", chain, period, str(path.resolve())]
    process_group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
                     else {"start_new_session": True})
    with (
        (path / "crawl.log").open("w", encoding="utf-8") as output,
        subprocess.Popen(
            command,
            cwd=root,
            stdout=output,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            **process_group,
        ) as proc,
    ):
        try:
            proc.wait(timeout=_POPEN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc)
            if proc.poll() is None:
                proc.kill()
            raise RuntimeError(f"16888 {chain} {period} 抓取超时，检查 {path / 'crawl.log'}") from exc

    if proc.returncode != 0:
        tail = (path / "crawl.log").read_text(encoding="utf-8", errors="replace")[-_TAIL_CHARS:]
        raise RuntimeError(f"16888 {chain} {period} 抓取失败，原始材料位于 {path}\n{tail}")


def _reported_months(page, chain: str) -> list[str]:
    if chain == "style":
        return page.locator("input.xl-date-input").evaluate_all("els => els.slice(0, 2).map(el => el.value)")
    return page.locator(".J_a_date .drop_checked").evaluate_all("els => els.map(el => el.dataset.id)")


def _navigate_capture(page, url: str):
    for attempt in range(3):
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.ok and page.url == url:
            return response
        status = getattr(response, 'status', None)
        if page.url != url or (status is not None and status != 429 and status < 500):
            break
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"请求 {url} 失败或重定向到 {page.url}，请检查站点响应")


def _duplicate_pages(pages: list[list[dict]], chain: str) -> set[int]:
    seen = {}
    duplicate_pages = set()
    column = 'manufacturer' if chain == 'style' else 'city'
    for number, records in enumerate(pages, 1):
        for record in records:
            key = (record.get('model_name', '').strip(), record.get(column, '').strip())
            if not all(key):
                raise ValueError('来源业务键缺失，不能局部修复')
            # Rank can change when tied rows move between adjacent pages.
            content = {k: v for k, v in record.items() if k not in ('rank', 'month')}
            if key in seen:
                previous_page, previous = seen[key]
                if previous != content:
                    raise ValueError(f'来源重复业务键内容冲突: {key!r}')
                duplicate_pages.update((previous_page, number))
            else:
                seen[key] = (number, content)
    return duplicate_pages


def _replace_duplicate_pages(pages, chain, fetch, source_total):
    candidate = [list(records) for records in pages]
    for _ in range(2):
        affected = _duplicate_pages(candidate, chain)
        if not affected:
            break
        if len(affected) > 6:
            raise ValueError('重复涉及超过六页，拒绝扩大局部重抓')
        for number in sorted(affected):
            records = fetch(number)
            if len(records) != len(candidate[number - 1]):
                raise ValueError('局部重抓行数变化，必须重新完整验收')
            candidate[number - 1] = records
    if _duplicate_pages(candidate, chain):
        raise ValueError('局部重抓两轮后仍有重复业务键，来源分页不稳定')
    if sum(map(len, candidate)) != source_total:
        raise ValueError('局部重抓后唯一业务键覆盖不足')
    return candidate


def _capture_pages(page, crawler, chain: str, period: str, path: Path) -> None:
    csv_path = path / f"{chain}_{period}.csv"
    page_rows: list[int] = []
    status = "published"
    source_total = None
    observed = 0
    captured_pages = []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=crawler.CSV_COLUMNS)
        writer.writeheader()
        while True:
            page_number = len(page_rows) + 1
            url = crawler.PAGE_URL_TEMPLATE.format(month=period, ym=period.replace("-", ""), page=page_number)
            try:
                _navigate_capture(page, url)
            except RuntimeError:
                (path / f"page_{page_number}.html").write_text(page.content(), encoding="utf-8")
                raise
            reported_months = _reported_months(page, chain)
            if reported_months != [period] * (2 if chain == "style" else 1):
                (path / f"page_{page_number}.html").write_text(page.content(), encoding="utf-8")
                raise RuntimeError(f"目标月 {period} 与站点实际月份 {reported_months!r} 不一致: {url}")

            total = crawler.get_total_count(page)
            records = crawler.extract_table_rows(page)
            if page_number == 1 and not records and total in (0, None):
                empty = "table.xl-table-a .table-nodata" if chain == "style" else ".mod-table table tr.nodata"
                if page.locator(empty).count() == 1 and (chain == "style" or total == 0):
                    status = "unpublished"
                    source_total = 0
                    break

            writer.writerows({**row, "month": period} for row in records)
            handle.flush()
            page_rows.append(len(records))
            captured_pages.append(records)
            if page_number == 1:
                source_total = total
            if not records or source_total is None or source_total <= 0 or total != source_total:
                (path / f"page_{page_number}.html").write_text(page.content(), encoding="utf-8")
                raise RuntimeError(f"第 {page_number} 页缺少有效站点总数或数据、或站点总数变化: {url} ({total!r})")

            pager = page.locator(crawler.PAGER_CONTAINER).first
            current = pager.locator(".curr").all_text_contents()
            has_next = pager.locator(".next").count() > 0
            single_page = page_number == 1 and not current and len(records) == source_total and not has_next
            if [value.strip() for value in current] != [str(page_number)] and not single_page:
                raise RuntimeError(f"第 {page_number} 页与分页器页码 {current!r} 不一致: {url}")
            observed += len(records)
            if observed > source_total or has_next != (observed < source_total):
                raise RuntimeError(f"分页覆盖无法与站点总数核对: 第 {page_number} 页，累计 {observed}/{source_total}")
            print(f"{chain} {period} 第 {page_number} 页 {len(records)} 行，累计 {observed}/{source_total}")
            if not has_next:
                break
            time.sleep(random.uniform(crawler.PAGE_SLEEP_MIN, crawler.PAGE_SLEEP_MAX))

    if status == 'published' and _duplicate_pages(captured_pages, chain):
        affected = sorted(_duplicate_pages(captured_pages, chain))
        if len(affected) > 6:
            raise ValueError('重复涉及超过六页，拒绝扩大局部重抓')
        # Compact original evidence only; successful pages never produce HTML.
        (path / 'page_retry.json').write_text(json.dumps({
            'chain': chain, 'period': period, 'affected_pages': affected,
            'original_rows': {str(n): captured_pages[n - 1] for n in affected},
        }, ensure_ascii=False, indent=2), encoding='utf-8')

        def refetch(number):
            time.sleep(random.uniform(crawler.PAGE_SLEEP_MIN, crawler.PAGE_SLEEP_MAX))
            url = crawler.PAGE_URL_TEMPLATE.format(month=period, ym=period.replace('-', ''), page=number)
            _navigate_capture(page, url)
            if _reported_months(page, chain) != [period] * (2 if chain == 'style' else 1):
                raise ValueError('局部重抓月份变化')
            pager = page.locator(crawler.PAGER_CONTAINER).first
            if ([v.strip() for v in pager.locator('.curr').all_text_contents()] != [str(number)]
                    or crawler.get_total_count(page) != source_total
                    or (pager.locator('.next').count() > 0) != (number < len(captured_pages))):
                raise ValueError('局部重抓分页或来源总数变化')
            return crawler.extract_table_rows(page)

        repaired = _replace_duplicate_pages(captured_pages, chain, refetch, source_total)
        with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=crawler.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows({**row, 'month': period} for records in repaired for row in records)

    (path / "manifest.json").write_text(json.dumps({
        "chain": chain,
        "period": period,
        "status": status,
        "pagination_complete": status == "published",
        "source_total": source_total,
        "page_rows": page_rows,
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _capture(chain: str, period: str, path: Path) -> None:
    crawler = import_module(f"pipelines.ingest.sales.crawl_16888_{chain}.crawl_{chain}")
    with crawler.sync_playwright() as playwright:
        browser, context, page = crawler.launch_browser(playwright, headless=True)
        try:
            _capture_pages(page, crawler, chain, period, path)
        finally:
            context.close()
            browser.close()


def _collect_legacy(chain: str, business_key: str | None) -> None:
    from scheduler.monthly_source import collect_month

    period = business_period(chain, business_key)
    collect_month(chain, period)
    raise AlreadyLoaded(f"16888 {chain} {period} 已按完整榜单核对 ODS")


def crawl_16888_style_monthly(business_key: str | None = None) -> None:
    _collect_legacy("style", business_key)


def crawl_16888_city_monthly(business_key: str | None = None) -> None:
    _collect_legacy("city", business_key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chain", choices=("style", "city"))
    parser.add_argument("period")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    _capture(args.chain, business_period(args.chain, args.period), args.path)
