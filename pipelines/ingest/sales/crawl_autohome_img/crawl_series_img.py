"""汽车之家报价页车型图片地址 Playwright 爬虫主脚本。

抓取 ``https://www.autohome.com.cn/price#pvareaid=6861598`` 报价页全部车型的
**车型名称**与**图片 SRC 地址**两个字段，落地为 JSON + CSV 双格式文件。

数据来源策略（基于 Playwright 实测，非假设）:
    报价页存在多个 ``.ant-spin-nested-loading`` 节点，**最后一个**才是完整车型列表
    （无限滚动）。之前的 ``li:has(h4)`` 命中的是"精选/热门"区（仅 20 个），并非目标。

    正确节点（最后一个 .ant-spin-nested-loading）的 LI 真实结构:
        - 车型卡片: ``li[data-url]``（有 data-url 属性）
        - 车型名称: li 内**首个 ``<a>`` 标签**的文本（如"海豹08"）
        - 图片地址: li 内首个 ``img`` 的 src 属性

    **两阶段滚动**:
        1. 滚动加载更多卡片（该列表为无限滚动，li 随滚动持续增长）
        2. 逐个 scrollIntoView 卡片触发图片懒加载（视口外 img.src 为 data:image 占位图）

核心流程:
    1. 启动 Chromium（支持 ``--headless`` 与 UA 轮换反检测）
    2. 导航到报价页 → 滚动加载全部车型卡片（至卡片数稳定）
    3. 逐个 scrollIntoView 卡片触发图片懒加载
    4. 用 JS 从最后一个 .ant-spin-nested-loading 提取 (首个a文本, img.src)
    5. 结果写入 ``data/autohome_series_img_<时间戳>.json`` 与 ``.csv``

字段说明（仅 2 个）:
    - series_name: 车型/车系名称（如"海豹08"）
    - image_src:   图片 SRC 地址（如 //g.autoimg.cn/...png）

用法:
    # headed 模式（默认，便于调试）
    python scripts/crawl_autohome_series_img/crawl_series_img.py

    # headless 模式（适合服务器/定时任务）
    python scripts/crawl_autohome_series_img/crawl_series_img.py --headless

    # 限制最大抓取数量（调试用）
    python scripts/crawl_autohome_series_img/crawl_series_img.py --limit 50

依赖:
    - playwright（需先 ``pip install playwright`` 再 ``playwright install chromium``）

说明:
    - 本脚本仅抓取车型名 + 图片地址，不入库 MySQL
    - 反爬策略: UA 轮换 + 反 webdriver 检测注入 + 滚动间随机延迟
"""

from __future__ import annotations

import argparse
import csv
import json
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

# 处理包内导入：确保 selectors 可作为包内模块导入                            兼容直接运行与作为模块导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from selectors import (  # noqa: E402  (sys.path 已处理)
    CARD_SELECTOR,
    IMG_SELECTOR,
    IMG_SRC_ATTR,
    LIST_CONTAINER_SELECTOR,
    NAME_SELECTOR,
    PRICE_URL,
)

from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# 常量配置
# ============================================================================

# UA 轮换池：5 个真实桌面浏览器 UA                                  降低被识别为机器人的概率
USER_AGENTS = [
    # Windows 11 / Edge
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    # Windows 10 / Chrome
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36"),
    # macOS / Chrome
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36"),
    # macOS / Safari
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/17.4 Safari/605.1.15"),
    # Windows 11 / Firefox
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
]

# 滚动加载更多卡片（无限滚动列表）
SCROLL_WAIT_MS = 2000                                                             # 每次滚动后等待渲染毫秒数（实测需 ≥1.5s 才触发下一批）
SCROLL_MAX_ROUNDS = 200                                                           # 最大滚动轮数（防止无限卡死）
SCROLL_STABLE_THRESHOLD = 5                                                       # 连续多少轮卡片数不变则判定到底
MAX_CARDS = 2000                                                                  # 安全上限，防止异常页无限增长

# 图片懒加载触发
LAZY_WAIT_MS = 400                                                                # 单个卡片 scrollIntoView 后等待毫秒数

# 页面加载超时（毫秒）
PAGE_TIMEOUT_MS = 30_000


# ============================================================================
# 浏览器启动与反检测
# ============================================================================

def _inject_anti_detect(context: BrowserContext) -> None:
    """向浏览器上下文注入反 webdriver 检测脚本。

    :param context: Playwright 浏览器上下文
    """
    context.add_init_script("""
        // 移除 navigator.webdriver 标志
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        // 伪装 languages 与 plugins
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        // 伪装 chrome 对象
        window.chrome = { runtime: {} };
    """)


def launch_browser(
    pw: Playwright,
    headless: bool,
    ua_index: int = 0,
) -> tuple[Browser, BrowserContext, Page]:
    """启动 Chromium 浏览器并创建带反检测的上下文。

    :param pw:       Playwright 实例
    :param headless: 是否无头模式
    :param ua_index: UA 池索引
    :returns: (browser, context, page) 三元组
    """
    browser = pw.chromium.launch(headless=headless)
    user_agent = USER_AGENTS[ua_index % len(USER_AGENTS)]                         # 轮换 UA
    context = browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1366, "height": 900},
        locale="zh-CN",
    )
    _inject_anti_detect(context)
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    return browser, context, page


# ============================================================================
# 页面交互
# ============================================================================

def _count_cards(page: Page) -> int:
    """统计目标容器（最后一个 .ant-spin-nested-loading）内的车型卡片数。

    :param page: Playwright 页面对象
    :returns: li[data-url] 卡片数量
    """
    return page.evaluate("""(params) => {
        const containers = document.querySelectorAll(params.container);
        const target = containers[containers.length - 1];                        // 最后一个容器
        if (!target) return 0;
        return target.querySelectorAll(params.card).length;
    }""", {"container": LIST_CONTAINER_SELECTOR, "card": CARD_SELECTOR})


def scroll_to_load_all(page: Page, limit: int | None) -> int:
    """循环滚动页面直到车型卡片加载完毕（无限滚动列表）。

    终止条件（满足任一）:
        - 连续 ``SCROLL_STABLE_THRESHOLD`` 轮卡片数不再增长
        - 达到 ``SCROLL_MAX_ROUNDS`` 上限
        - 达到 ``limit`` 或 ``MAX_CARDS`` 安全上限

    :param page:  Playwright 页面对象
    :param limit: 用户指定的最大卡片数（调试用，None 表示不限制）
    :returns: 最终加载到的卡片总数
    """
    page.wait_for_selector(CARD_SELECTOR, state="attached")
    cap = min(limit or MAX_CARDS, MAX_CARDS)                                      # 安全上限
    prev_count = 0
    stable_rounds = 0

    for round_idx in range(SCROLL_MAX_ROUNDS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_WAIT_MS)

        curr_count = _count_cards(page)
        if curr_count >= cap:                                                     # 达到上限提前终止
            print(f"[OK] 已达抓取上限 {cap}，停止滚动（当前 {curr_count} 张卡片）。")
            return curr_count

        if curr_count == prev_count:
            stable_rounds += 1
            if stable_rounds >= SCROLL_STABLE_THRESHOLD:
                print(f"[OK] 卡片数连续 {stable_rounds} 轮不变（{curr_count}），判定加载完成。")
                return curr_count
        else:
            stable_rounds = 0
            prev_count = curr_count

    print(f"[WARN] 达到最大滚动轮数 {SCROLL_MAX_ROUNDS}，强制结束（当前 {prev_count} 张卡片）。")
    return prev_count


def trigger_lazy_load(page: Page) -> int:
    """逐个 scrollIntoView 车型卡片，触发图片懒加载。

    实测: 视口外卡片的 img.src 为 data:image 占位图，只有进入视口后
    懒加载库才会把真实 URL 写入 src。由于图片加载是异步的，需 Python
    循环逐个滚动并等待。

    :param page: Playwright 页面对象
    :returns: 触发懒加载的卡片总数
    """
    total = _count_cards(page)
    print(f"  → 检测到 {total} 个车型卡片，逐个触发图片懒加载 ...")
    logger.info(f"检测到 {total} 个车型卡片，开始触发懒加载")

    # Python 循环逐个 scrollIntoView（限定在目标容器内）
    for i in range(total):
        page.evaluate("""(params) => {
            const containers = document.querySelectorAll(params.container);
            const target = containers[containers.length - 1];
            if (!target) return;
            const cards = target.querySelectorAll(params.card);
            if (cards[params.index]) {
                cards[params.index].scrollIntoView({block: 'center'});
            }
        }""", {"container": LIST_CONTAINER_SELECTOR, "card": CARD_SELECTOR, "index": i})
        page.wait_for_timeout(LAZY_WAIT_MS)

    # 再统一等待一轮，确保所有异步图片请求完成
    page.wait_for_timeout(LAZY_WAIT_MS * 3)
    return total


# ============================================================================
# 字段提取
# ============================================================================

# 从最后一个 .ant-spin-nested-loading 提取全部卡片字段
# 车型名称取 li 内首个 a 标签文本；图片取 li 内首个 img 的 src
# 过滤 data:image 占位图，仅保留真实图片 URL
_EXTRACT_ALL_JS = """(params) => {
    const containers = document.querySelectorAll(params.container);
    const target = containers[containers.length - 1];                            // 最后一个容器
    if (!target) return [];
    const cards = target.querySelectorAll(params.card);
    const out = [];
    for (const card of cards) {
        const nameEl = card.querySelector(params.name);
        const imgEl = card.querySelector(params.img);
        const name = nameEl ? (nameEl.textContent || '').trim() : '';
        let src = imgEl ? (imgEl.getAttribute(params.imgSrcAttr) || '') : '';
        // 过滤 data:image 占位图（懒加载未触发时的 1x1 透明 gif）
        if (src.startsWith('data:image')) {
            src = '';
        }
        // 仅保留同时有名称和真实图片 URL 的记录
        if (name && src) {
            out.push({ series_name: name, image_src: src });
        }
    }
    return out;
}"""


def extract_all_cards(page: Page) -> list[dict[str, Any]]:
    """提取全部车型卡片的车型名称与图片地址。

    仅从最后一个 .ant-spin-nested-loading 容器提取（完整车型列表），
    避免抓到"精选/热门"区的重复数据。

    :param page: Playwright 页面对象（已完成滚动加载与懒加载触发）
    :returns: 结构化记录列表，每条含 series_name 与 image_src
    """
    total = _count_cards(page)
    print(f"  → 提取 {total} 张卡片 ...")

    raw_records = page.evaluate(_EXTRACT_ALL_JS, {
        "container":    LIST_CONTAINER_SELECTOR,
        "card":         CARD_SELECTOR,
        "name":         NAME_SELECTOR,
        "img":          IMG_SELECTOR,
        "imgSrcAttr":   IMG_SRC_ATTR,
    })

    print(f"  → 完成：{len(raw_records)} 条有效记录。")
    logger.info(f"提取完成：{len(raw_records)} 条有效记录（共 {total} 张卡片）")
    return raw_records


# ============================================================================
# 结果落地
# ============================================================================

def _data_dir() -> Path:
    """返回 data 目录路径（不存在则创建）。"""
    d = Path(__file__).resolve().parent / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_results(records: list[dict[str, Any]]) -> tuple[Path, Path]:
    """将抓取结果保存为 JSON 与 CSV 双格式文件。

    :param records: 车型记录列表，每条含 series_name 与 image_src
    :returns: (json_path, csv_path) 已写入的文件路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = _data_dir()
    json_path = data_dir / f"autohome_series_img_{timestamp}.json"
    csv_path = data_dir / f"autohome_series_img_{timestamp}.csv"

    # JSON: 完整结构化数据
    payload = {
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "source_url": PRICE_URL,
        "record_count": len(records),
        "records": records,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # CSV: 扁平表格，仅 2 列
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:               # utf-8-sig 兼容 Excel
        writer = csv.writer(f)
        writer.writerow(["series_name", "image_src"])
        for rec in records:
            writer.writerow([rec["series_name"], rec["image_src"]])

    return json_path, csv_path


# ============================================================================
# 主流程
# ============================================================================

def crawl(headless: bool, limit: int | None) -> None:
    """执行完整抓取流程。

    :param headless: 是否无头模式
    :param limit:    最大抓取卡片数（调试用，None 表示不限制）
    """
    print("=" * 70)
    print("汽车之家报价页车型图片地址爬虫启动")
    print(f"  模式: {'headless' if headless else 'headed'} | limit={limit}")
    print("=" * 70)
    logger.info(f"爬虫启动: headless={headless}, limit={limit}")

    records: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser, context, page = launch_browser(pw, headless, ua_index=0)

        try:
            # 1. 导航到报价页
            print("[1/5] 导航到报价页 ...")
            page.goto(PRICE_URL, wait_until="networkidle")
            page.wait_for_selector(CARD_SELECTOR, state="attached")
            print("[OK] 页面加载完成。")

            # 2. 滚动加载全部车型卡片（无限滚动列表）
            print("[2/5] 滚动加载全部车型卡片 ...")
            total = scroll_to_load_all(page, limit)

            # 3. 逐个触发图片懒加载
            print(f"[3/5] 逐个触发图片懒加载 ...")
            trigger_lazy_load(page)

            # 4. 提取全部卡片字段
            print(f"[4/5] 提取卡片字段 ...")
            records = extract_all_cards(page)

        finally:
            # 5. 保存结果
            print("[5/5] 保存结果 ...")
            if records:
                json_path, csv_path = save_results(records)
                print(f"  → JSON: {json_path}")
                print(f"  → CSV : {csv_path}")

                # 汇总统计
                print("\n" + "=" * 70)
                print("[完成] 抓取汇总")
                print(f"  记录总数: {len(records)}")
                print(f"  字段:     series_name, image_src")
                # 打印前 5 条样例
                for rec in records[:5]:
                    print(f"  样例: {rec['series_name']} -> {rec['image_src'][:60]}...")
                print("=" * 70)
                logger.info(f"抓取完成: {len(records)} 条记录")
            else:
                print("[WARN] 未抓取到任何数据。")
                logger.warning("未抓取到任何数据")

            context.close()
            browser.close()

    print("[完成] 爬虫流程结束。")


# ============================================================================
# 命令行入口
# ============================================================================

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="汽车之家报价页车型图片地址 Playwright 爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # headed 模式（默认，便于调试）
  python scripts/crawl_autohome_series_img/crawl_series_img.py

  # headless 模式（适合服务器/定时任务）
  python scripts/crawl_autohome_series_img/crawl_series_img.py --headless

  # 调试: 仅抓取前 50 个车型
  python scripts/crawl_autohome_series_img/crawl_series_img.py --limit 50
""",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行（不显示浏览器窗口，适合服务器/定时任务）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制最大抓取卡片数（调试用，如 --limit 50）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        crawl(headless=args.headless, limit=args.limit)
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止。")
        logger.warning("用户手动终止爬虫")
        sys.exit(130)
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[FATAL] 抓取失败: {exc}")
        logger.error(f"抓取失败: {exc}", exc_info=True)
        sys.exit(1)
    except Exception as exc:                                                       # 兜底捕获
        print(f"[FATAL] 未知异常: {exc}")
        logger.exception(f"未知异常: {exc}")
        raise
