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


def _capture_pages(page, crawler, chain: str, period: str, path: Path) -> None:
    csv_path = path / f"{chain}_{period}.csv"
    page_rows: list[int] = []
    status = "published"
    source_total = None
    observed = 0
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=crawler.CSV_COLUMNS)
        writer.writeheader()
        while True:
            page_number = len(page_rows) + 1
            url = crawler.PAGE_URL_TEMPLATE.format(month=period, ym=period.replace("-", ""), page=page_number)
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None or not response.ok or page.url != url:
                (path / f"page_{page_number}.html").write_text(page.content(), encoding="utf-8")
                raise RuntimeError(f"请求 {url} 失败或重定向到 {page.url}，请检查站点响应")
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
