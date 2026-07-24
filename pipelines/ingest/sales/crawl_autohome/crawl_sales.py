"""汽车之家车系月销榜 Playwright 爬虫主脚本。

抓取 ``https://www.autohome.com.cn/rank/1`` 的车系月销榜全部历史月份销量数据，
落地为 JSON + CSV 双格式文件，供后续入库或分析使用。

数据来源策略（基于真实页面探测）:
    汽车之家用 Next.js + React，销量榜为滚动懒加载列表。
    滚动加载全部卡片后，直接用 JS 读取每张卡片的 innerText，
    再用正则提取价格/销量/能源分项等文本字段即可。

核心流程:
    1. 启动 Chromium（支持 ``--headless`` 与 UA 轮换反检测）
    2. 进入页面 → 显式点击"车系月销榜"Tab → 打开时间下拉框
    3. 读取全部历史月份选项，按 ``--months`` 参数决定遍历范围
    4. 逐月切换 → 滚动加载全部卡片 → 一次性提取全部卡片文本字段
    5. 聚合结果写入 ``data/autohome_sales_<时间戳>.json`` 与 ``.csv``

字段说明:
    - 车型名称（seriesname，如"海狮06"）
    - 价格区间（priceinfo，如"12.99-19.98万"）
    - 车系销量（salecount，整数，如 18856）
    - 能源分项明细（可选 dict，从 rcmdesc 解析，如 ``{"纯电动": 14868, "插电混合": 3988}``）

用法:
    # 抓取最近一个月（headed 模式，便于调试）
    python scripts/crawl_autohome/crawl_sales.py --months latest

    # 抓取全部历史月份（headless 模式，适合批量回溯）
    python scripts/crawl_autohome/crawl_sales.py --months all --headless

    # 调试: 只抓前 3 个月份
    python scripts/crawl_autohome/crawl_sales.py --months all --limit 3

依赖:
    - playwright（需先 ``pip install playwright`` 再 ``playwright install chromium``）

说明:
    - 本脚本为独立数据源，与项目主链路 AKShare 采集解耦，互不影响
    - 反爬策略: UA 轮换 + 月份间随机延迟(2~5s) + 反 webdriver 检测注入
    - 滚动加载: 循环滚至底部，终止条件为"卡片数不再增长"或"出现'已经到底了~'"
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ElementHandle,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# 处理包内导入：确保 selectors 可作为包内模块导入                            兼容直接运行与作为模块导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from selectors import (  # noqa: E402  (sys.path 已处理)
    CARD_SELECTOR,
    END_HINT_TEXT,
    RANK_URL,
    TAB_MONTHLY_SERIES_TEXT,
    TIME_DROPDOWN_TRIGGER_TEXT_REGEX,
    TIME_OPTION_TEXT_REGEX,
)

# 添加结构化日志支持
from base_infra.logging_config import get_logger
logger = get_logger(__name__)


# ============================================================================
# 常量配置
# ============================================================================

# UA 轮换池：5 个真实桌面浏览器 UA，每个月份切换一个                        降低被识别为机器人的概率
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

# 反爬延迟区间（秒）：月份间随机休眠                                     避免高频请求触发风控
SLEEP_MIN = 2.0
SLEEP_MAX = 5.0

# 滚动加载相关
SCROLL_WAIT_MS = 800          # 每次滚动后等待渲染的毫秒数
SCROLL_MAX_ROUNDS = 40        # 最大滚动轮数（防止无限滚动卡死）
SCROLL_STABLE_THRESHOLD = 3   # 连续多少轮卡片数不变则认为加载完毕
END_HINT_RETRIES = 2          # 检测到「到底」文本后额外重试滚动的次数                # 规避首屏即出现到底文本的误判

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
    user_agent = USER_AGENTS[ua_index % len(USER_AGENTS)]           # 轮换 UA           # 按索引取 UA
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

def ensure_tab_monthly_series(page: Page) -> None:
    """确保当前处于"车系月销榜"Tab。

    页面默认即此 Tab，但显式点击以保证状态正确。点击策略：
    用 Playwright 文本选择器直接定位（Tab 不是 .ant-dropdown-trigger，
    而是普通 div，class 含 tw-bg-[#D3EBFF] 表示当前选中）。

    :param page: Playwright 页面对象
    """
    # 用文本定位 Tab（更稳定，不依赖具体 tag/class）                     # 避免假设标准
    tab = page.get_by_text(TAB_MONTHLY_SERIES_TEXT, exact=True).first
    try:
        tab.wait_for(state="visible", timeout=5_000)
        tab.click()
        page.wait_for_load_state("networkidle")
        print(f"[OK] 已切换到「{TAB_MONTHLY_SERIES_TEXT}」Tab。")
        logger.info("已切换到「车系月销榜」Tab")
    except PlaywrightTimeoutError:
        # 找不到时报警但不中断（可能默认就是该 Tab）                   # 容错处理
        print(f"[WARN] 未找到「{TAB_MONTHLY_SERIES_TEXT}」Tab 按钮，按默认 Tab 继续。")
        logger.warning("未找到「车系月销榜」Tab 按钮，按默认 Tab 继续")


def _has_visible_dropdown_item(page: Page) -> bool:
    """检测页面当前是否有可见的下拉选项（用于 wait_for_function）。"""
    return page.evaluate("""() => {
        const items = document.querySelectorAll('.ant-dropdown-menu-item');
        for (const it of items) {
            if (it.offsetParent !== null) return true;
        }
        return false;
    }""")


def open_time_dropdown(page: Page) -> ElementHandle:
    """打开时间选择下拉框并返回触发器元素。

    实测页面上有多个 .ant-dropdown-trigger，需按文本（形如"2026年05月"）定位。
    用 JS 检测可见 item（``offsetParent !== null``）规避 antd 动画导致的可见性误判。

    :param page: Playwright 页面对象
    :returns: 时间下拉触发器元素
    :raises RuntimeError: 找不到时间下拉触发器时
    """
    # 用文本正则匹配定位触发器                                              # 避免索引漂移
    trigger = page.locator(
        f".ant-dropdown-trigger >> text=/{TIME_DROPDOWN_TRIGGER_TEXT_REGEX}/"
    ).first
    trigger.wait_for(state="visible")

    # 若下拉已打开（有可见 item），直接返回，避免重复点击导致状态错乱      # 修正 Escape 后状态不一致
    if _has_visible_dropdown_item(page):
        print("[OK] 时间下拉框已处于打开状态。")
        logger.debug("时间下拉框已处于打开状态")
        return trigger.element_handle()

    trigger.click()
    # 用 wait_for_function 检测可见 item 出现（规避 CSS 动画的可见性误判）
    page.wait_for_function("""() => {
        const items = document.querySelectorAll('.ant-dropdown-menu-item');
        for (const it of items) {
            if (it.offsetParent !== null) return true;
        }
        return false;
    }""", timeout=10_000)
    print("[OK] 已打开时间下拉框。")
    logger.debug("已打开时间下拉框")
    return trigger.element_handle()


def read_all_month_options(page: Page) -> list[str]:
    """读取时间下拉框的全部月份选项文本。

    只读取当前可见的下拉选项（用 ``offsetParent`` JS 过滤残留隐藏容器）。

    :param page: Playwright 页面对象（下拉框已打开）
    :returns: 月份选项文本列表，形如 ["2026年05月", "2026年04月", ...]
    """
    # 用 JS 直接取可见选项的文本（比 Playwright :visible 更可靠）          # 修正多 dropdown 干扰
    raw_texts: list[str] = page.evaluate("""() => {
        const items = document.querySelectorAll('.ant-dropdown-menu-item');
        const out = [];
        for (const it of items) {
            if (it.offsetParent !== null) {
                out.push((it.textContent || '').trim());
            }
        }
        return out;
    }""")
    months: list[str] = []
    for text in raw_texts:
        if re.match(TIME_OPTION_TEXT_REGEX, text):                  # 仅保留 yyyy年mm月 格式
            months.append(text)
    if months:
        print(f"[OK] 读取到 {len(months)} 个月份选项：{months[0]} ~ {months[-1]}")
        logger.info(f"读取到 {len(months)} 个月份选项：{months[0]} ~ {months[-1]}")
    else:
        print("[WARN] 未读取到任何月份选项。")
        logger.warning("未读取到任何月份选项")
    return months


def get_current_month(page: Page) -> str | None:
    """读取时间下拉触发器上显示的当前月份。

    :param page: Playwright 页面对象
    :returns: 当前月份文本（如 "2026年05月"），读取失败返回 None
    """
    try:
        text = page.locator(
            f".ant-dropdown-trigger >> text=/{TIME_DROPDOWN_TRIGGER_TEXT_REGEX}/"
        ).first.inner_text(timeout=3_000)
        return (text or "").strip()
    except PlaywrightTimeoutError:
        return None


def click_month_option(page: Page, month_text: str) -> None:
    """切换到指定的月份选项。

    优化: 若当前触发器显示的月份已是目标月份，则跳过切换（避免无谓的
    开关下拉导致的 antd 状态错乱）。

    :param page:       Playwright 页面对象
    :param month_text: 月份文本，如 "2026年05月"
    :raises RuntimeError: 找不到对应月份选项时
    """
    # 先检查当前是否已是目标月份                                        # 避免无谓切换
    current = get_current_month(page)
    if current == month_text:
        print(f"[OK] 当前月份已是 {month_text}，无需切换。")
        # 确保下拉关闭（若意外打开）
        if _has_visible_dropdown_item(page):
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        return

    # 重新打开下拉框（上一次点击选项后会自动关闭）
    open_time_dropdown(page)
    # 用 JS 在可见 item 中精确匹配文本并点击                                # 修正多 dropdown 干扰
    found = page.evaluate("""(targetText) => {
        const items = document.querySelectorAll('.ant-dropdown-menu-item');
        for (const it of items) {
            if (it.offsetParent !== null && (it.textContent || '').trim() === targetText) {
                it.click();
                return true;
            }
        }
        return false;
    }""", month_text)
    if not found:
        logger.error(f"未在下拉框中找到月份选项: {month_text}")
        raise RuntimeError(f"未在下拉框中找到月份选项: {month_text}")
    page.wait_for_load_state("networkidle")
    # 等待首个卡片渲染
    page.wait_for_selector(CARD_SELECTOR, state="attached")
    print(f"[OK] 已切换到月份：{month_text}")


def scroll_to_load_all(page: Page) -> int:
    """循环滚动页面直到所有卡片加载完毕。

    终止条件（满足任一）:
        - 出现"已经到底了~"文本后，再重试 ``END_HINT_RETRIES`` 次滚动，
          卡片数依旧不增加才判定完成（规避首屏即出现到底文本的误判）
        - 连续 ``SCROLL_STABLE_THRESHOLD`` 轮卡片数不再增长
        - 达到 ``SCROLL_MAX_ROUNDS`` 上限

    :param page: Playwright 页面对象
    :returns: 最终加载到的卡片总数
    """
    prev_count = 0
    stable_rounds = 0
    end_hint_count = 0                                          # 连续出现「到底」文本的次数         # 配合重试逻辑

    for round_idx in range(SCROLL_MAX_ROUNDS):
        # 检查是否出现"已经到底了~"
        body_text = page.inner_text("body") or ""
        if END_HINT_TEXT in body_text:
            end_hint_count += 1                                # 累计出现次数
            curr_count = page.locator(CARD_SELECTOR).count()
            if end_hint_count > END_HINT_RETRIES:              # 重试次数用尽
                print(f"[OK] 检测到「{END_HINT_TEXT}」且重试 {END_HINT_RETRIES} 次后卡片数"
                      f"依旧不增长，判定加载完成（共 {curr_count} 张卡片）。")
                return curr_count
            print(f"[INFO] 检测到「{END_HINT_TEXT}」(第 {end_hint_count}/{END_HINT_RETRIES} 次)，"
                  f"继续重试滚动验证（当前 {curr_count} 张卡片）。")
            # 不立即返回，继续走下面的滚动流程验证卡片是否还会增长
        else:
            end_hint_count = 0                                 # 文本消失则重置计数

        # 滚动到页面底部
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_WAIT_MS)

        # 统计当前卡片数
        curr_count = page.locator(CARD_SELECTOR).count()
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


# ============================================================================
# 字段提取（DOM 文本 + React fiber 图片 URL，边滚边提）
# ============================================================================

# 提取用的正则（DOM 文本解析）
PRICE_REGEX = r"(\d+\.\d+-\d+\.\d+)万"
ENERGY_BREAKDOWN_REGEX_LOCAL = (
    r"(纯电动|插电混合|增程式|油电混合|汽油|柴油|天然气):\s*(\d+)辆"
)
SALES_LABEL_TEXT = "车系销量"


def _parse_price_range(card_text: str) -> str | None:
    """从卡片文本提取价格区间。"""
    m = re.search(PRICE_REGEX, card_text)
    return (m.group(1) + "万") if m else None


def _parse_sales_volume(card_text: str) -> int | None:
    """从卡片文本提取车系销量（数字 + "车系销量"标签模式）。"""
    m = re.search(r"(\d+)\s*" + SALES_LABEL_TEXT, card_text)
    return int(m.group(1)) if m else None


def _parse_energy_breakdown_from_text(card_text: str) -> dict[str, int]:
    """从卡片文本解析能源分项明细。"""
    matches = re.findall(ENERGY_BREAKDOWN_REGEX_LOCAL, card_text)
    return {energy: int(num) for energy, num in matches}


# 提取全部卡片字段的 JS 代码（一次性读取 innerText + rank + 车型名）       # 纯文本字段，无图片
_EXTRACT_ALL_JS = """() => {
    const cards = document.querySelectorAll('div[data-rank-num]');
    const out = [];
    for (const card of cards) {
        const img = card.querySelector('img');
        out.push({
            rank: parseInt(card.getAttribute('data-rank-num') || '0', 10),
            model_name: img ? (img.getAttribute('alt') || '') : '',
            card_text: card.innerText || '',
        });
    }
    return out;
}"""


def extract_all_cards(page: Page) -> list[dict[str, Any]]:
    """提取全部车型卡片字段（一次性读取，无需分批滚动）。

    滚动加载完成后，卡片 DOM 节点已全部存在，直接用 JS 读取每张卡片的
    innerText 与排名属性，再用正则提取价格/销量/能源分项即可。

    :param page: Playwright 页面对象（已完成滚动加载）
    :returns: 结构化记录列表
    """
    total = page.eval_on_selector_all(CARD_SELECTOR, "els => els.length")
    print(f"  → 提取 {total} 张卡片 ...")

    raw_records = page.evaluate(_EXTRACT_ALL_JS)

    all_records: list[dict[str, Any]] = []
    for rec in raw_records:
        card_text = rec.pop("card_text", "")
        rec["price_range"] = _parse_price_range(card_text)
        rec["sales_volume"] = _parse_sales_volume(card_text)
        rec["energy_breakdown"] = _parse_energy_breakdown_from_text(card_text)
        all_records.append(rec)

    # 按 rank 排序（滚动加载可能乱序）
    all_records.sort(key=lambda r: r["rank"])

    print(f"  → 完成：{len(all_records)} 条记录。")
    return all_records


# ============================================================================
# 结果落地
# ============================================================================

def _data_dir() -> Path:
    """返回 data 目录路径（不存在则创建）。"""
    d = Path(__file__).resolve().parent / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_results(months_data: list[dict[str, Any]]) -> tuple[Path, Path]:
    """将抓取结果保存为 JSON 与 CSV 双格式文件。

    :param months_data: 月份维度聚合的数据列表
    :returns: (json_path, csv_path) 已写入的文件路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = _data_dir()
    json_path = data_dir / f"autohome_sales_{timestamp}.json"
    csv_path = data_dir / f"autohome_sales_{timestamp}.csv"

    # JSON: 完整结构化数据
    payload = {
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "source_url": RANK_URL,
        "months": months_data,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # CSV: 扁平表格，energy_breakdown 序列化为 JSON 字符串列
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:    # utf-8-sig 兼容 Excel
        writer = csv.writer(f)
        writer.writerow([
            "month", "rank", "model_name", "price_range",
            "sales_volume", "energy_breakdown",
        ])
        for month_data in months_data:
            month = month_data["month"]
            for rec in month_data["records"]:
                writer.writerow([
                    month,
                    rec["rank"],
                    rec["model_name"],
                    rec["price_range"] or "",
                    rec["sales_volume"] if rec["sales_volume"] is not None else "",
                    json.dumps(rec["energy_breakdown"], ensure_ascii=False),
                ])

    return json_path, csv_path


# ============================================================================
# 主流程
# ============================================================================

def crawl(headless: bool, months_mode: str, limit: int | None) -> None:
    """执行完整抓取流程。

    :param headless:    是否无头模式
    :param months_mode: ``all`` 遍历全部历史月份 / ``latest`` 仅最近一月
    :param limit:       限制最大月份数（调试用，None 表示不限制）
    """
    print("=" * 70)
    print("汽车之家车系月销榜爬虫启动")
    print(f"  模式: {'headless' if headless else 'headed'} | months={months_mode} | limit={limit}")
    print("=" * 70)
    logger.info(f"爬虫启动: headless={headless}, months={months_mode}, limit={limit}")

    months_data: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        # 启动浏览器（首个 UA）
        browser, context, page = launch_browser(pw, headless, ua_index=0)

        try:
            # 1. 导航到榜单页
            print("[1/6] 导航到榜单页 ...")
            page.goto(RANK_URL, wait_until="networkidle")

            # 2. 确认 Tab
            print("[2/6] 确认「车系月销榜」Tab ...")
            ensure_tab_monthly_series(page)

            # 3. 打开时间下拉框并读取全部月份
            print("[3/6] 读取时间下拉框选项 ...")
            open_time_dropdown(page)
            all_months = read_all_month_options(page)
            # 关闭下拉，交给 click_month_option 统一管理状态               # 避免下拉残留
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            if not all_months:
                logger.error("时间下拉框未读取到任何月份选项，页面结构可能已变化")
                raise RuntimeError("时间下拉框未读取到任何月份选项，页面结构可能已变化。")

            # 4. 决定遍历范围
            if months_mode == "latest":
                target_months = all_months[:1]                       # 仅最近一月
            else:
                target_months = all_months
            if limit is not None:
                target_months = target_months[:limit]                # 调试截断
            print(f"[OK] 本次将抓取 {len(target_months)} 个月份。")

            # 5. 逐月抓取
            for idx, month_text in enumerate(target_months):
                print(f"\n[4/6] 抓取第 {idx + 1}/{len(target_months)} 个月份: {month_text}")
                # 切换 UA（每个月份用不同 UA）
                new_ua_idx = idx
                if idx > 0:                                          # 首月已在启动时设置
                    context.close()
                    browser, context, page = launch_browser(
                        pw, headless, ua_index=new_ua_idx
                    )
                    page.goto(RANK_URL, wait_until="networkidle")
                    ensure_tab_monthly_series(page)

                # 切换月份
                click_month_option(page, month_text)

                # 滚动加载全部
                print("  → 滚动加载全部卡片 ...")
                total = scroll_to_load_all(page)

                # 提取全部卡片字段
                print(f"  → 提取 {total} 张卡片字段 ...")
                records = extract_all_cards(page)

                # 解析月份为 YYYY-MM 格式
                m = re.match(TIME_OPTION_TEXT_REGEX, month_text)
                month_iso = f"{m.group(1)}-{int(m.group(2)):02d}" if m else month_text

                months_data.append({
                    "month": month_iso,
                    "month_raw": month_text,
                    "record_count": len(records),
                    "records": records,
                })
                print(f"  → 完成：{len(records)} 条记录。")

                # 反爬延迟（最后一个月不延迟）
                if idx < len(target_months) - 1:
                    sleep_s = random.uniform(SLEEP_MIN, SLEEP_MAX)
                    print(f"  → 反爬休眠 {sleep_s:.1f}s ...")
                    time.sleep(sleep_s)

        finally:
            # 6. 保存结果
            print("\n[5/6] 保存结果 ...")
            if months_data:
                json_path, csv_path = save_results(months_data)
                print(f"  → JSON: {json_path}")
                print(f"  → CSV : {csv_path}")

                # 汇总统计
                print("\n[6/6] 抓取汇总")
                print("=" * 70)
                total_records = sum(m["record_count"] for m in months_data)
                print(f"  月份总数: {len(months_data)}")
                print(f"  记录总数: {total_records}")
                for m in months_data:
                    print(f"    {m['month']}: {m['record_count']} 条")
                print("=" * 70)
            else:
                print("[WARN] 未抓取到任何数据。")

            context.close()
            browser.close()

    print("[完成] 爬虫流程结束。")


# ============================================================================
# 命令行入口
# ============================================================================

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="汽车之家车系月销榜 Playwright 爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # 抓取最近一个月（默认 headed，便于调试）
  python scripts/crawl_autohome/crawl_sales.py --months latest

  # 抓取全部历史月份（headless 模式）
  python scripts/crawl_autohome/crawl_sales.py --months all --headless

  # 调试: 仅抓前 3 个月份
  python scripts/crawl_autohome/crawl_sales.py --months all --limit 3
""",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行（不显示浏览器窗口，适合服务器/定时任务）",
    )
    parser.add_argument(
        "--months",
        choices=["all", "latest"],
        default="latest",
        help="月份范围: all=遍历全部历史月份 / latest=仅最近一月（默认 latest）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制最大月份数（调试用，如 --limit 3 只抓前3个月份）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        crawl(
            headless=args.headless,
            months_mode=args.months,
            limit=args.limit,
        )
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止。")
        logger.warning("用户手动终止爬虫")
        sys.exit(130)
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[FATAL] 抓取失败: {exc}")
        logger.error(f"抓取失败: {exc}", exc_info=True)
        sys.exit(1)
    except Exception as exc:                                          # 兜底捕获
        print(f"[FATAL] 未知异常: {exc}")
        logger.exception(f"未知异常: {exc}")
        raise
