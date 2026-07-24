"""华为车型页 ADS 车型抓取脚本。

抓取 ``https://auto.huawei.com/cn/vehicle-models`` 页面中全部车型卡片，
重点识别哪些车型的解决方案文案包含 ``ADS``，并输出结构化 JSON。

字段设计说明：
    - 车型卡片内的“该车型搭载的华为乾崑解决方案：”后面并不是简单平铺文本，
      而是“方案名 -> 特征列表”的层级结构，因此使用嵌套 JSON 更合适。
    - 每条记录保留完整 ``solution_groups``，并额外产出 ``has_huawei_ads`` 与
      ``ads_features``，便于后续直接筛选 ADS 车型。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from base_infra.logging_config import get_logger  # noqa: E402

try:
    from .selectors import (  # noqa: E402
        CARD_SELECTOR,
        DETAIL_LINK_SELECTOR,
        EXTRA_TIP_SELECTOR,
        IMAGE_SELECTOR,
        MORE_SECTION_SELECTOR,
        SOLUTION_FEATURE_SELECTOR,
        SOLUTION_GROUP_NAME_SELECTOR,
        SOLUTION_GROUP_SELECTOR,
        SOLUTIONS_LABEL_SELECTOR,
        SOURCE_URL,
        SUBTITLE_SELECTOR,
        TITLE_SELECTOR,
        TOTAL_COUNT_REGEX,
    )
except ImportError:
    from pipelines.ingest.huawei_ads.selectors import (  # noqa: E402
        CARD_SELECTOR,
        DETAIL_LINK_SELECTOR,
        EXTRA_TIP_SELECTOR,
        IMAGE_SELECTOR,
        MORE_SECTION_SELECTOR,
        SOLUTION_FEATURE_SELECTOR,
        SOLUTION_GROUP_NAME_SELECTOR,
        SOLUTION_GROUP_SELECTOR,
        SOLUTIONS_LABEL_SELECTOR,
        SOURCE_URL,
        SUBTITLE_SELECTOR,
        TITLE_SELECTOR,
        TOTAL_COUNT_REGEX,
    )

logger = get_logger(__name__)


PAGE_TIMEOUT_MS = 30_000
SCROLL_WAIT_MS = 1_500
SCROLL_MAX_ROUNDS = 120
SCROLL_STABLE_THRESHOLD = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ADS_PATTERN = re.compile(r"ADS", re.IGNORECASE)

EXTRACT_CARDS_JS = """(params) => {
    const cleanText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const uniqueTexts = (items) => {
        const out = [];
        const seen = new Set();
        for (const item of items) {
            const cleaned = cleanText(item);
            if (!cleaned || seen.has(cleaned)) {
                continue;
            }
            seen.add(cleaned);
            out.push(cleaned);
        }
        return out;
    };

    const cards = document.querySelectorAll(params.card);
    const records = [];

    for (const card of cards) {
        const img = card.querySelector(params.image);
        const h3 = card.querySelector(params.title);
        const h4 = card.querySelector(params.subtitle);
        const detailLink = card.querySelector(params.detailLink);
        const solutionsLabel = card.querySelector(params.solutionsLabel);
        const moreSection = card.querySelector(params.moreSection);

        const solutionGroups = [];
        for (const group of card.querySelectorAll(params.solutionGroup)) {
            const solutionName = cleanText(
                group.querySelector(params.solutionGroupName)?.textContent || ''
            );
            const features = uniqueTexts(
                Array.from(group.querySelectorAll(params.solutionFeature)).map(
                    (node) => node.textContent || ''
                )
            );
            if (solutionName && features.length > 0) {
                solutionGroups.push({
                    solution_name: solutionName,
                    features,
                });
            }
        }

        const extraTips = uniqueTexts(
            Array.from(card.querySelectorAll(params.extraTip)).map(
                (node) => node.textContent || ''
            )
        );

        let footnote = '';
        if (moreSection) {
            footnote = cleanText((moreSection.innerText || '').replace(/了解详情/g, ''));
        }

        records.push({
            vehicle_name: cleanText(img?.getAttribute('alt') || h3?.textContent || ''),
            h3_model_name: cleanText(h3?.textContent || ''),
            model_subtitle: cleanText(h4?.textContent || ''),
            detail_url: detailLink ? detailLink.href : '',
            image_url: cleanText(img?.currentSrc || img?.getAttribute('src') || ''),
            image_alt: cleanText(img?.getAttribute('alt') || ''),
            solutions_label: cleanText(solutionsLabel?.textContent || ''),
            solution_groups: solutionGroups,
            extra_tip: extraTips.join(' | '),
            footnote,
        });
    }

    return records;
}"""


def _inject_anti_detect(context: BrowserContext) -> None:
    """向浏览器上下文注入反 webdriver 检测脚本。"""
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
    """)


def launch_browser(
    pw: Playwright,
    headless: bool,
) -> tuple[Browser, BrowserContext, Page]:
    """启动 Playwright 浏览器上下文。"""
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1440, "height": 960},
        locale="zh-CN",
    )
    _inject_anti_detect(context)
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    return browser, context, page


def normalize_text(value: Any) -> str:
    """清洗文本中的多余空白。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_solution_groups(
    raw_groups: list[dict[str, Any]],
) -> list[dict[str, list[str]]]:
    """清洗解决方案分组并按原顺序去重。"""
    cleaned_groups: list[dict[str, list[str]]] = []
    for group in raw_groups:
        solution_name = normalize_text(group.get("solution_name"))
        if not solution_name:
            continue

        features: list[str] = []
        seen_features: set[str] = set()
        for feature in group.get("features", []):
            cleaned_feature = normalize_text(feature)
            if not cleaned_feature or cleaned_feature in seen_features:
                continue
            seen_features.add(cleaned_feature)
            features.append(cleaned_feature)

        if features:
            cleaned_groups.append(
                {
                    "solution_name": solution_name,
                    "features": features,
                }
            )
    return cleaned_groups


def extract_ads_features(solution_groups: list[dict[str, list[str]]]) -> list[str]:
    """提取命中 ADS 关键词的特征文案。"""
    ads_features: list[str] = []
    for group in solution_groups:
        for feature in group.get("features", []):
            if ADS_PATTERN.search(feature):
                ads_features.append(feature)
    return ads_features


def build_model_record(raw_card: dict[str, Any]) -> dict[str, Any]:
    """将浏览器侧提取的原始卡片结构清洗为最终记录。"""
    solution_groups = normalize_solution_groups(raw_card.get("solution_groups", []))
    ads_features = extract_ads_features(solution_groups)

    vehicle_name = normalize_text(raw_card.get("vehicle_name"))
    h3_model_name = normalize_text(raw_card.get("h3_model_name"))

    return {
        "vehicle_name": vehicle_name or h3_model_name,
        "h3_model_name": h3_model_name,
        "model_subtitle": normalize_text(raw_card.get("model_subtitle")),
        "detail_url": normalize_text(raw_card.get("detail_url")),
        "image_url": normalize_text(raw_card.get("image_url")),
        "image_alt": normalize_text(raw_card.get("image_alt")),
        "solutions_label": normalize_text(raw_card.get("solutions_label")),
        "solution_groups": solution_groups,
        "extra_tip": normalize_text(raw_card.get("extra_tip")),
        "footnote": normalize_text(raw_card.get("footnote")),
        "has_huawei_ads": bool(ads_features),
        "ads_features": ads_features,
    }


def parse_expected_total(page: Page) -> int | None:
    """解析页面展示的车型总数。"""
    body_text = page.evaluate("document.body.innerText || ''")
    matched = re.search(TOTAL_COUNT_REGEX, body_text)
    if not matched:
        return None
    return int(matched.group(1))


def count_cards(page: Page) -> int:
    """统计已加载的车型卡片数量。"""
    return page.locator(CARD_SELECTOR).count()


def should_stop_scrolling(
    current_count: int,
    previous_count: int,
    stable_rounds: int,
    expected_total: int | None,
    stable_threshold: int = SCROLL_STABLE_THRESHOLD,
) -> tuple[bool, int]:
    """判断是否可以停止滚动。"""
    if expected_total is not None and current_count >= expected_total:
        return True, stable_rounds

    if current_count == previous_count:
        stable_rounds += 1
    else:
        stable_rounds = 0

    return stable_rounds >= stable_threshold, stable_rounds


def build_record_key(record: dict[str, Any]) -> str:
    """构建卡片级唯一键，避免同链接不同车型被误去重。"""
    return "||".join(
        [
            normalize_text(record.get("detail_url")),
            normalize_text(record.get("vehicle_name")),
            normalize_text(record.get("h3_model_name")),
        ]
    )


def scroll_to_load_all(page: Page, expected_total: int | None) -> int:
    """循环滚动页面，直到车型卡片数稳定或达到目标总数。"""
    page.wait_for_selector(CARD_SELECTOR, state="attached")

    previous_count = 0
    stable_rounds = 0

    for round_idx in range(SCROLL_MAX_ROUNDS):
        current_count = count_cards(page)
        should_stop, stable_rounds = should_stop_scrolling(
            current_count=current_count,
            previous_count=previous_count,
            stable_rounds=stable_rounds,
            expected_total=expected_total,
        )
        logger.info(
            "滚动检测: round=%s current_count=%s expected_total=%s stable_rounds=%s",
            round_idx + 1,
            current_count,
            expected_total,
            stable_rounds,
        )

        if should_stop and current_count > 0:
            return current_count

        previous_count = current_count
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_WAIT_MS)

    logger.warning("达到最大滚动轮数，按当前已加载卡片数结束")
    return count_cards(page)


def extract_all_cards(page: Page) -> list[dict[str, Any]]:
    """从页面提取全部车型卡片数据。"""
    raw_cards = page.evaluate(
        EXTRACT_CARDS_JS,
        {
            "card": CARD_SELECTOR,
            "image": IMAGE_SELECTOR,
            "title": TITLE_SELECTOR,
            "subtitle": SUBTITLE_SELECTOR,
            "detailLink": DETAIL_LINK_SELECTOR,
            "solutionsLabel": SOLUTIONS_LABEL_SELECTOR,
            "solutionGroup": SOLUTION_GROUP_SELECTOR,
            "solutionGroupName": SOLUTION_GROUP_NAME_SELECTOR,
            "solutionFeature": SOLUTION_FEATURE_SELECTOR,
            "extraTip": EXTRA_TIP_SELECTOR,
            "moreSection": MORE_SECTION_SELECTOR,
        },
    )

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw_card in raw_cards:
        record = build_model_record(raw_card)
        dedupe_key = build_record_key(record)
        if not dedupe_key or dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        records.append(record)
    return records


def _data_dir() -> Path:
    """返回输出目录。"""
    data_dir = MODULE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def save_results(
    records: list[dict[str, Any]],
    expected_total: int | None,
) -> Path:
    """保存抓取结果到 JSON。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = _data_dir() / f"huawei_ads_models_{timestamp}.json"
    ads_model_count = sum(1 for record in records if record["has_huawei_ads"])

    payload = {
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "source_url": SOURCE_URL,
        "expected_total": expected_total,
        "record_count": len(records),
        "ads_model_count": ads_model_count,
        "records": records,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def crawl(headless: bool, limit: int | None) -> Path:
    """执行完整抓取流程。"""
    logger.info("华为 ADS 车型抓取启动: headless=%s limit=%s", headless, limit)

    with sync_playwright() as pw:
        browser, context, page = launch_browser(pw, headless=headless)
        try:
            page.goto(SOURCE_URL, wait_until="domcontentloaded")
            page.wait_for_selector(CARD_SELECTOR, state="attached")
            page.wait_for_timeout(SCROLL_WAIT_MS)
            expected_total = parse_expected_total(page)
            if limit is not None and expected_total is not None:
                expected_total = min(expected_total, limit)

            loaded_count = scroll_to_load_all(page, expected_total=expected_total)
            logger.info("页面滚动结束，已加载车型卡片数: %s", loaded_count)
            if expected_total is None:
                expected_total = loaded_count

            records = extract_all_cards(page)
            if limit is not None:
                records = records[:limit]

            output_path = save_results(records=records, expected_total=expected_total)
            logger.info("抓取完成: record_count=%s output=%s", len(records), output_path)
            return output_path
        finally:
            context.close()
            browser.close()


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="抓取华为车型页中采用 ADS 方案的车型信息",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="使用无头模式运行浏览器",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制输出记录数，便于调试",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        result_path = crawl(headless=cli_args.headless, limit=cli_args.limit)
        print(f"[完成] 结果已保存: {result_path}")
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止。")
        sys.exit(130)
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[FATAL] 抓取失败: {exc}")
        logger.error("抓取失败: %s", exc, exc_info=True)
        sys.exit(1)
    except Exception as exc:
        print(f"[FATAL] 未知异常: {exc}")
        logger.exception("未知异常: %s", exc)
        raise
