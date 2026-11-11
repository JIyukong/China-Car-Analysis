"""车主之家城市上牌数据 Playwright 爬虫主脚本。

抓取 ``https://xl.16888.com/city.html`` 的全国汽车上牌数据，
按月份遍历，逐页点击"下一页"翻页，落地为 JSON + CSV 双格式文件。

数据来源策略（基于真实页面 DOM 探测）:
    车主之家为传统 jQuery 站点，数据是服务端渲染的标准 HTML ``<table>``。
    每页 50 条，每月约 500+ 页（25318 条/月）。直接读取 tbody tr 的 td 文本即可。

核心流程:
    1. 启动 Chromium（支持 ``--headless`` 与 UA 轮换反检测）
    2. 进入页面 → 从第一套月份下拉读取全部可选月份
    3. 按 ``--months`` 参数决定遍历范围（all 全部历史 / latest 仅最近一月）
    4. 逐月切换 → 循环点击"下一页"直到按钮消失（最后一页终止条件）
    5. 每页数据实时追加写 CSV，每月完成后写 JSON，支持断点续传

字段说明（对应用户要求的 5 个字段 + 排名 + 月份）:
    - 排名（rank，整数，如 1）
    - 车型（model_name，如"驱逐舰05"）
    - 城市（city，如"宁波"）
    - 上牌量（sales_volume，整数，如 2446）
    - 级别（level，如"紧凑型车"）
    - 指导价（price_range，如"7.98-13.68"，单位万元）

页面结构关键点（实测）:
    - 全页仅 1 个 ``<table>``，数据在 ``<tbody><tr><td>``
    - 月份下拉 ``.drop_down_list.mod-downlist > a[data-id]``，页面上有两套，
      用户确认操作 **第一套**（索引 0）
    - "下一页"按钮 ``.xl-data-pageing .next``，**到最后一页时从 DOM 完全消失**
      （非 disabled），是最可靠的终止条件

用法:
    # 抓取最近一个月（默认 headed，便于调试）
    python scripts/crawl_16888_city/crawl_city.py --months latest

    # 抓取全部历史月份（headless 模式，适合批量回溯）
    python scripts/crawl_16888_city/crawl_city.py --months all --headless

    # 断点续传（中断后从上次位置继续）
    python scripts/crawl_16888_city/crawl_city.py --months all --headless --resume

    # 调试: 只抓前 2 个月份
    python scripts/crawl_16888_city/crawl_city.py --months all --limit 2

依赖:
    - playwright（需先 ``pip install playwright`` 再 ``playwright install chromium``）

说明:
    - 本脚本为独立数据源，与汽车之家爬虫解耦，互不影响
    - 反爬策略: UA 轮换 + 翻页间随机延迟(1~2s) + 月份间随机延迟(2~5s) + 反 webdriver 检测注入
    - 断点续传: 每页落地 progress.json，每月数据实时追加写 CSV（避免内存堆积）
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
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# 处理包内导入：确保 selectors 可作为包内模块导入                            兼容直接运行与作为模块导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from selectors import (  # noqa: E402  (sys.path 已处理)
    CITY_URL,
    MONTH_DROPDOWN_INDEX,
    NEXT_PAGE_BUTTON,
    PAGE_URL_TEMPLATE,
    PAGER_CONTAINER,
    TABLE_ROW_SELECTOR,
    TOTAL_COUNT_TEXT_REGEX,
)


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

# 反爬延迟区间（秒）
PAGE_SLEEP_MIN = 1.0                                # 翻页间最小延迟           # 翻页频繁，延迟较短
PAGE_SLEEP_MAX = 2.0                                # 翻页间最大延迟
MONTH_SLEEP_MIN = 2.0                               # 月份间最小延迟
MONTH_SLEEP_MAX = 5.0                               # 月份间最大延迟

# 风控检测与冷却: 提取到 0 条数据时判定为疑似风控，长休眠后重试
# 对齐 style 爬虫；city 每月约 500 页，被风控概率更高，必须有重试保护
RISK_CONTROL_EMPTY_THRESHOLD = 0                    # 单页提取条数 <= 此值视为疑似风控
RISK_CONTROL_COOLDOWN_MIN_MINUTES = 3              # 风控冷却最小分钟数
RISK_CONTROL_COOLDOWN_MAX_MINUTES = 6              # 风控冷却最大分钟数
RISK_CONTROL_MAX_RETRIES_PER_PAGE = 3              # 单页风控重试上限

# 页面加载超时（毫秒）
PAGE_TIMEOUT_MS = 30_000

# CSV 列名（对应用户要求的字段 + 排名 + 月份）
CSV_COLUMNS = [
    "month", "rank", "model_name", "city",
    "sales_volume", "level", "price_range",
]


# ============================================================================
# 浏览器启动与反检测
# ============================================================================

def _inject_anti_detect(context: BrowserContext) -> None:
    """向浏览器上下文注入反 webdriver 检测脚本。

    覆盖常见 headless 检测特征:
        - navigator.webdriver / navigator.permissions
        - HeadlessChrome UA 特征
        - window.chrome 对象缺失
        - plugins/mimeTypes 空数组
        - WebGL vendor 渲染器（VMware/SwiftShader 是 headless 标志）

    :param context: Playwright 浏览器上下文
    """
    context.add_init_script("""
        // 移除 navigator.webdriver 标志
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 伪装 languages 与 plugins
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {name: 'Chrome PDF Plugin'},
                {name: 'Chrome PDF Viewer'},
                {name: 'Native Client'}
            ]
        });

        // 伪装 chrome 对象（headless 模式下缺失）
        window.chrome = window.chrome || { runtime: {} };

        // 伪装 permissions API（headless 下 permissions.query 异常）
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(parameters);

        // 伪装 WebGL 渲染器（避免暴露 SwiftShader/VMware）
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
            return getParameter.call(this, parameter);
        };
    """)


def launch_browser(
    pw: Playwright,
    headless: bool,
    ua_index: int = 0,
) -> tuple[Browser, BrowserContext, Page]:
    """启动 Chromium 浏览器并创建带反检测的上下文。

    headless 模式优化:
        使用 ``headless="new"`` 启用 Chromium 新版 headless 模式（112+），
        而非默认的旧版 headless。新版 headless 行为更接近真实浏览器，
        不暴露 ``HeadlessChrome`` UA 特征，反检测能力更强。
        实测: 旧版 headless 会被 16888 站点反爬拦截（部分月份页面返回空白）。

    :param pw:       Playwright 实例
    :param headless: 是否无头模式
    :param ua_index: UA 池索引
    :returns: (browser, context, page) 三元组
    """
    # headless 模式优化: 通过 args 传 --headless=new 启用 Chromium 新版 headless        # 旧版会被 16888 拦截
    # Playwright Python 的 headless 参数只接受 bool，新版 headless 需用 args 传入
    launch_args = ["--headless=new"] if headless else []
    browser = pw.chromium.launch(headless=False, args=launch_args)
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

def read_all_month_options(page: Page) -> list[str]:
    """从第一套月份下拉读取全部可选月份的 data-id。

    实测: 页面上有两套 ``.drop_down_list.mod-downlist``（按品牌/车型查询区 +
    按级别/价格查询区），两套 data-id 完全一致，用户确认操作第一套。

    :param page: Playwright 页面对象
    :returns: 月份 ID 列表，形如 ["2026-05", "2026-04", ...]
    :raises RuntimeError: 读不到任何月份选项时
    """
    # 用 JS 从第一套下拉容器读取全部 a[data-id]                         # 避免第二套干扰
    month_ids: list[str] = page.evaluate("""(dropdownIndex) => {
        const containers = document.querySelectorAll('.drop_down_list.mod-downlist');
        if (dropdownIndex >= containers.length) return [];
        const container = containers[dropdownIndex];
        const links = container.querySelectorAll('a[data-id]');
        const out = [];
        for (const a of links) {
            const id = a.getAttribute('data-id');
            if (id) out.push(id);
        }
        return out;
    }""", MONTH_DROPDOWN_INDEX)

    if month_ids:
        print(f"[OK] 读取到 {len(month_ids)} 个月份选项：{month_ids[0]} ~ {month_ids[-1]}")
    else:
        print("[WARN] 未读取到任何月份选项。")
    return month_ids


def _navigate_with_retry(page: Page, url: str, retries: int = 3) -> None:
    """带重试的页面导航（防止偶发超时中断整个任务）。

    页面加载偶发超时（网络波动/服务端响应慢），单次失败不应中断整个任务。
    每次失败后等待 5 秒再重试。

    导航策略说明:
        使用 ``wait_until="domcontentloaded"`` 而非 ``networkidle``。
        实测: 该站点部分月份页面有持续的网络请求（广告/统计轮询），
        导致 ``networkidle`` 永远无法达到（要求 500ms 内无请求）。
        但数据表格是服务端渲染，DOM 就绪后即可提取，无需等待网络空闲。

    :param page:    Playwright 页面对象
    :param url:     目标 URL
    :param retries: 最大重试次数
    :raises PlaywrightTimeoutError: 重试耗尽仍失败时抛出
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")         # DOM 就绪即可   # 数据为服务端渲染
            page.wait_for_selector(TABLE_ROW_SELECTOR, state="attached")
            return                                                # 成功则直接返回
        except PlaywrightTimeoutError as exc:
            last_exc = exc
            print(f"  → [重试 {attempt}/{retries}] 页面加载超时，5s 后重试: {url}")
            time.sleep(5)
    raise last_exc                                                # 重试耗尽，抛出最后异常


def click_month_option(page: Page, month_id: str) -> None:
    """切换到指定月份（直接 URL 跳转，最稳定）。

    实测: 月份下拉选项 href 为 ``javascript:;``，且容器默认 ``display:none``，
    需先点击触发器展开后选项才可点击。但切换月份的本质是 JS 跳转到
    ``city-{month}-0-0-0-0-0-1.html``，故直接用 URL 跳转更稳定、更快，
    且不依赖下拉展开状态。

    :param page:     Playwright 页面对象
    :param month_id: 月份 ID，如 "2026-04"
    """
    url = PAGE_URL_TEMPLATE.format(month=month_id, page=1)
    _navigate_with_retry(page, url)
    print(f"[OK] 已切换到月份：{month_id}")


def has_next_page(page: Page) -> bool:
    """检测是否存在"下一页"按钮。

    实测: 到最后一页时，``.xl-data-pageing .next`` 元素从 DOM 完全消失
    （而非 disabled），这是最可靠的终止条件。

    :param page: Playwright 页面对象
    :returns: 存在返回 True，已到最后一页返回 False
    """
    return page.locator(NEXT_PAGE_BUTTON).count() > 0


def click_next_page(page: Page) -> None:
    """点击"下一页"按钮并等待新数据渲染。

    使用 ``domcontentloaded`` 而非 ``networkidle``（与 _navigate_with_retry 一致），
    避免站点持续网络请求导致永远达不到 networkidle。

    :param page: Playwright 页面对象
    :raises PlaywrightTimeoutError: 等待超时时
    """
    page.locator(NEXT_PAGE_BUTTON).first.click()
    page.wait_for_load_state("domcontentloaded")                   # DOM 就绪即可     # 避免 networkidle 卡死
    page.wait_for_selector(TABLE_ROW_SELECTOR, state="attached")


def get_total_count(page: Page) -> int | None:
    """从分页器解析"共N条数据"文本，返回总数据量。

    :param page: Playwright 页面对象
    :returns: 总数据量（如 25318），解析失败返回 None
    """
    try:
        text = page.locator(PAGER_CONTAINER).first.inner_text(timeout=3_000)
    except PlaywrightTimeoutError:
        return None
    m = re.search(TOTAL_COUNT_TEXT_REGEX, text or "")
    return int(m.group(1)) if m else None


# ============================================================================
# 字段提取（一次性读取当前页全部行）
# ============================================================================

# 提取当前页全部数据行的 JS 代码                                      # 直接读 td 文本，无需正则
_EXTRACT_ROWS_JS = """() => {
    const rows = document.querySelectorAll('table tbody tr');
    const out = [];
    for (const tr of rows) {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 6) continue;                              // 跳过不完整行
        // 安全读取 helper：取元素 innerText 并 trim，元素不存在返回空串
        const txt = (el) => el ? ((el.innerText || '').trim()) : '';
        const num = (el) => {
            const t = txt(el);
            const m = t.match(/\\d+/);
            return m ? parseInt(m[0], 10) : 0;
        };
        out.push({
            rank:         num(tds[0].querySelector('strong')),
            model_name:   txt(tds[1].querySelector('a')),
            city:         txt(tds[2].querySelector('a')),
            sales_volume: num(tds[3].querySelector('em')),
            level:        txt(tds[4].querySelector('a')),
            price_range:  txt(tds[5].querySelector('span')),
        });
    }
    return out;
}"""


def extract_table_rows(page: Page) -> list[dict[str, Any]]:
    """提取当前页 table 全部数据行。

    每行结构（实测）:
        td[0] strong    → 排名（如 "1"）
        td[1] a         → 车型（如 "驱逐舰05"）
        td[2] a         → 城市（如 "宁波"）
        td[3] a em      → 上牌量（如 "2446"）
        td[4] a         → 级别（如 "紧凑型车"）
        td[5] a span    → 指导价（如 "7.98-13.68"，单位万元）

    :param page: Playwright 页面对象
    :returns: 结构化记录列表
    """
    return page.evaluate(_EXTRACT_ROWS_JS)


# ============================================================================
# 断点续传与数据落地
# ============================================================================

def _data_dir() -> Path:
    """返回 data 目录路径（不存在则创建）。"""
    d = Path(__file__).resolve().parent / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _progress_path() -> Path:
    """返回断点续传进度文件路径。"""
    return _data_dir() / "progress.json"


def _is_month_locally_complete(month_id: str) -> bool:
    """检查本地是否已有该月份的完整数据（CSV + JSON 双文件均存在）。

    用于断点续传的双校验: 即使 progress.json 被覆盖丢失，只要本地
    CSV + JSON 文件都在，就视为已完成，避免重复抓取。对齐 style 爬虫。

    :param month_id: 月份 ID，如 "2024-01"
    :returns: 完整返回 True
    """
    csv_path = _data_dir() / f"city_{month_id}.csv"
    json_path = _data_dir() / f"city_{month_id}.json"
    return csv_path.exists() and json_path.exists()


def load_progress() -> dict[str, Any]:
    """读取断点续传进度文件。

    :returns: 进度字典，不存在时返回空结构
    """
    p = _progress_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("[WARN] progress.json 解析失败，从头开始。")
    return {"completed_months": [], "months_in_progress": {}}


def save_progress(progress: dict[str, Any]) -> None:
    """落地断点续传进度文件（覆盖写）。

    :param progress: 进度字典
    """
    progress["last_run"] = datetime.now().isoformat(timespec="seconds")
    _progress_path().write_text(
        json.dumps(progress, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_month_csv(month_id: str, records: list[dict[str, Any]]) -> Path:
    """将本月本页数据追加写入月份 CSV 文件。

    文件名: ``data/city_<YYYY-MM>.csv``，断点续传时追加不覆盖。

    :param month_id: 月份 ID，如 "2026-05"
    :param records:  本页记录列表
    :returns: CSV 文件路径
    """
    csv_path = _data_dir() / f"city_{month_id}.csv"
    # 文件不存在时写表头                                         # 断点续传追加时不重复写表头
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:   # utf-8-sig 兼容 Excel
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for rec in records:
            writer.writerow({**rec, "month": month_id})
    return csv_path


def save_month_json(
    month_id: str,
    records: list[dict[str, Any]],
    total_count: int | None,
    page_count: int,
) -> Path:
    """将月份数据写入 JSON 文件（覆盖写，月份完成后调用）。

    :param month_id:    月份 ID
    :param records:     全部记录
    :param total_count: 页面显示的总数据量
    :param page_count:  实际翻页数
    :returns: JSON 文件路径
    """
    json_path = _data_dir() / f"city_{month_id}.json"
    payload = {
        "month": month_id,
        "total_count": total_count,
        "page_count": page_count,
        "record_count": len(records),
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "records": records,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path


# ============================================================================
# 主流程
# ============================================================================

def crawl_one_month(
    page: Page,
    month_id: str,
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    """抓取单个指定月份的全部数据。

    流程: 切换月份 → 循环（提取当前页 → 追加 CSV → 落地进度 →
          检测是否有下一页 → 有则点击下一页）→ 返回全部记录。

    断点续传: 若 progress 中记录了该月份的已抓页码，则从断点页继续，
              已抓页的数据从已存在的 CSV 重新读入内存。

    :param page:     Playwright 页面对象
    :param month_id: 月份 ID，如 "2026-05"
    :param progress: 断点续传进度字典（会被原地修改）
    :returns: 该月份的全部记录列表
    """
    # 切换月份
    click_month_option(page, month_id)
    total_count = get_total_count(page)
    if total_count:
        print(f"  → 页面显示共 {total_count} 条数据。")
    else:
        # 空数据月（站点已列月份但未发布数据）：不写 CSV/JSON、不标记 completed，
        # 交由调用方决定是否稍后重试。对齐 style 爬虫的保护逻辑。
        print(f"  → [WARN] {month_id} 未读取到总数据量，可能数据未发布，跳过本月。")
        return []

    # 断点续传: 检查是否有已抓进度
    in_progress = progress.get("months_in_progress", {}).get(month_id)
    all_records: list[dict[str, Any]] = []

    if in_progress:
        start_page = in_progress.get("page", 0) + 1                 # 从上次页码 +1 继续
        # 从已存在的 CSV 重新读入已抓数据                                       # 保证 JSON 汇总完整
        csv_path = _data_dir() / f"city_{month_id}.csv"
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 类型转换: rank/sales_volume 转回 int
                    row["rank"] = int(row["rank"]) if row.get("rank") else 0
                    row["sales_volume"] = int(row["sales_volume"]) if row.get("sales_volume") else 0
                    all_records.append(row)
        print(f"  → 断点续传：已读入 {len(all_records)} 条历史记录，从第 {start_page} 页继续。")
        # 跳转到断点页（避免重复点击下一页多次）
        _goto_page_directly(page, month_id, start_page)
    else:
        start_page = 1                                              # 从第 1 页开始

    page_num = start_page

    while True:
        # 提取当前页数据（带风控检测与重试）
        records = _extract_with_risk_control(page, month_id, page_num)
        if records is None:
            # 风控重试耗尽，返回已抓数据（断点续传可后续补全）
            print(f"  → [WARN] {month_id} 第 {page_num} 页风控重试耗尽，本月暂停，已抓 {len(all_records)} 条。")
            return all_records

        if records:
            append_month_csv(month_id, records)                    # 实时追加 CSV          # 断点安全
            all_records.extend(records)
        print(f"  → 第 {page_num} 页：提取 {len(records)} 条（累计 {len(all_records)} 条）")

        # 落地断点进度
        progress.setdefault("months_in_progress", {})[month_id] = {
            "page": page_num,
            "records": len(all_records),
        }
        save_progress(progress)

        # 检测是否到最后一页（.next 按钮消失）
        if not has_next_page(page):
            print(f"  → 已到最后一页（第 {page_num} 页），本月抓取完成。")
            break

        # 点击下一页
        click_next_page(page)

        # 翻页间反爬延迟
        sleep_s = random.uniform(PAGE_SLEEP_MIN, PAGE_SLEEP_MAX)
        time.sleep(sleep_s)
        page_num += 1

    return all_records


def _extract_with_risk_control(
    page: Page, month_id: str, page_num: int,
) -> list[dict[str, Any]] | None:
    """提取当前页数据，带风控检测与重试。

    当提取到 0 条数据时，判定为疑似被风控（headless 被识别后页面返回空表格），
    触发长休眠冷却后重新加载该页重试。对齐 style 爬虫的同名机制。

    :param page:     Playwright 页面对象
    :param month_id: 月份 ID（用于 URL 重载）
    :param page_num: 当前页码（用于 URL 重载）
    :returns: 记录列表；重试耗尽仍失败返回 None（调用方应暂停该月）
    """
    for attempt in range(1, RISK_CONTROL_MAX_RETRIES_PER_PAGE + 1):
        records = extract_table_rows(page)
        if len(records) > RISK_CONTROL_EMPTY_THRESHOLD:
            return records                                          # 正常获取数据

        # 疑似风控: 提取到 0 条数据
        if attempt < RISK_CONTROL_MAX_RETRIES_PER_PAGE:
            cooldown_min = random.uniform(
                RISK_CONTROL_COOLDOWN_MIN_MINUTES,
                RISK_CONTROL_COOLDOWN_MAX_MINUTES,
            )
            print(f"  → [风控疑似] {month_id} 第 {page_num} 页提取 0 条，"
                  f"冷却 {cooldown_min:.1f} 分钟后重试 ({attempt}/{RISK_CONTROL_MAX_RETRIES_PER_PAGE}) ...")
            time.sleep(cooldown_min * 60)
            # 重新加载该页
            _goto_page_directly(page, month_id, page_num)
        else:
            print(f"  → [风控疑似] {month_id} 第 {page_num} 页重试 {attempt} 次仍为空。")
            return None                                             # 重试耗尽
    return None


def _goto_page_directly(page: Page, month_id: str, page_num: int) -> None:
    """直接跳转到指定页码（用于断点续传，避免重复点击下一页）。

    :param page:     Playwright 页面对象
    :param month_id: 月份 ID
    :param page_num: 目标页码（从 1 开始）
    """
    url = PAGE_URL_TEMPLATE.format(month=month_id, page=page_num)
    _navigate_with_retry(page, url)
    print(f"  → 直接跳转到第 {page_num} 页: {url}")


def _find_latest_available_month(page: Page, all_months: list[str]) -> str:
    """从最新月份往前探测，找到第一个有数据的月份。

    系统当前月可能是未来月份（站点已列月份但尚未发布数据），需往前回溯直到
    找到有数据的月份。判据：``共N条数据`` 文本存在且 N > 0。
    对齐 style 爬虫的同名机制（city 原先直接取 all_months[0]，会抓到空月）。

    :param page:       Playwright 页面对象
    :param all_months: 倒序排列的全部月份列表（最新在前）
    :returns: 最新有数据的月份 ID
    :raises RuntimeError: 全部月份都无数据时
    """
    print("[探测] 查找最新有数据的月份 ...")
    for month_id in all_months[:6]:                                # 最多探测最近 6 个月 # 避免过度请求
        url = PAGE_URL_TEMPLATE.format(month=month_id, page=1)
        try:
            page.goto(url, wait_until="domcontentloaded")
            count = get_total_count(page)
            if count and count > 0:
                print(f"[OK] 最新有数据的月份: {month_id}（共 {count} 条）")
                return month_id
            print(f"  → {month_id} 无数据（count={count}），继续往前探测 ...")
        except PlaywrightTimeoutError:
            print(f"  → {month_id} 访问超时，继续往前探测 ...")
        time.sleep(0.5)                                            # 探测间轻量延迟
    raise RuntimeError("最近 6 个月份均无数据，请检查站点是否可用。")


def crawl(
    headless: bool,
    months_mode: str,
    limit: int | None,
    resume: bool,
    target_months_arg: str | None = None,
) -> None:
    """执行完整抓取流程。

    :param headless:          是否无头模式
    :param months_mode:       ``all`` 遍历全部历史月份 / ``latest`` 仅最近一月
    :param limit:             限制最大月份数（调试用，None 表示不限制）
    :param resume:            是否启用断点续传（跳过已完成月份）
    :param target_months_arg: 指定月份（逗号分隔，如 "2026-05,2026-03"），
                              优先于 months_mode，None 表示用 months_mode 决定
    """
    print("=" * 70)
    print("车主之家城市上牌数据爬虫启动")
    print(f"  模式: {'headless' if headless else 'headed'} | months={months_mode} | "
          f"limit={limit} | resume={resume} | target={target_months_arg}")
    print("=" * 70)

    # 加载断点续传进度
    progress = load_progress() if resume else {"completed_months": [], "months_in_progress": {}}
    if resume and progress.get("completed_months"):
        print(f"[OK] 断点续传：已完成月份 {progress['completed_months']}")

    months_summary: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser, context, page = launch_browser(pw, headless, ua_index=0)

        try:
            # 1. 导航到目标页
            print("[1/5] 导航到目标页 ...")
            page.goto(CITY_URL, wait_until="domcontentloaded")      # DOM 就绪即可     # 避免 networkidle 卡死

            # 2. 读取全部月份选项
            print("[2/5] 读取月份下拉选项 ...")
            all_months = read_all_month_options(page)
            if not all_months:
                raise RuntimeError("月份下拉框未读取到任何选项，页面结构可能已变化。")

            # 3. 决定遍历范围
            if target_months_arg:                                     # --target 优先
                # 解析逗号分隔的月份列表，过滤掉下拉中不存在的月份
                wanted = [m.strip() for m in target_months_arg.split(",") if m.strip()]
                target_months = [m for m in all_months if m in wanted]  # 保持下拉原始顺序
                invalid = set(wanted) - set(target_months)            # 用户输入但下拉中不存在的
                if invalid:
                    print(f"[WARN] 以下月份不在可选范围内，已忽略: {sorted(invalid)}")
            elif months_mode == "latest":
                # latest 模式: 自动探测最新有数据的月份                  # 系统当前月可能是未来月，无数据
                target_months = [_find_latest_available_month(page, all_months)]
            else:
                target_months = all_months
            if limit is not None:
                target_months = target_months[:limit]                # 调试截断
            print(f"[OK] 本次将抓取 {len(target_months)} 个月份。")

            # 4. 逐月抓取
            for idx, month_id in enumerate(target_months):
                # 断点续传: 跳过已完成月份（双校验: progress.json + 本地文件）
                # 双校验原因: progress.json 可能因单月测试运行被覆盖丢失历史进度，
                #             本地 CSV+JSON 文件存在则视为已完成，避免重复抓取
                if resume:
                    in_progress_list = month_id in progress.get("completed_months", [])
                    local_done = _is_month_locally_complete(month_id)
                    if in_progress_list or local_done:
                        print(f"\n[3/5] ({idx + 1}/{len(target_months)}) {month_id} 已完成，跳过。")
                        # 修复 progress.json 缺失: 本地有数据但 progress 没记录时补录
                        if not in_progress_list and local_done:
                            progress.setdefault("completed_months", []).append(month_id)
                            save_progress(progress)
                        continue

                print(f"\n[3/5] ({idx + 1}/{len(target_months)}) 抓取月份: {month_id}")

                # 切换 UA（每个月份用不同 UA）
                if idx > 0:                                          # 首月已在启动时设置
                    context.close()
                    browser, context, page = launch_browser(pw, headless, ua_index=idx)
                    page.goto(CITY_URL, wait_until="domcontentloaded")  # DOM 就绪即可  # 避免 networkidle 卡死

                # 抓取单月数据（捕获异常，单月失败不中断整体流程）
                try:
                    records = crawl_one_month(page, month_id, progress)
                except (PlaywrightTimeoutError, Exception) as exc:
                    print(f"  → [ERROR] {month_id} 抓取异常: {exc}")
                    print(f"  → {month_id} 跳过，后续月份继续，可 --resume 补全。")
                    months_summary.append({"month": month_id, "record_count": 0})
                    continue

                if not records:
                    # 空数据月（未发布或风控重试耗尽）：不写 JSON、不标记 completed，
                    # 次日 --resume 会重新抓取。对齐 style 爬虫保护逻辑。
                    print(f"  → {month_id} 无数据，跳过 JSON 写入，不标记完成。")
                    months_summary.append({"month": month_id, "record_count": 0})
                    continue

                # 写月份 JSON
                json_path = save_month_json(
                    month_id, records,
                    total_count=None,                               # 在 crawl_one_month 内已打印
                    page_count=progress.get("months_in_progress", {}).get(month_id, {}).get("page", 0),
                )
                print(f"  → JSON: {json_path}")

                # 标记该月份完成
                progress.setdefault("completed_months", []).append(month_id)
                progress.get("months_in_progress", {}).pop(month_id, None)
                save_progress(progress)

                months_summary.append({
                    "month": month_id,
                    "record_count": len(records),
                })
                print(f"  → 完成：{len(records)} 条记录。")

                # 月份间反爬延迟（最后一个月不延迟）
                if idx < len(target_months) - 1:
                    sleep_s = random.uniform(MONTH_SLEEP_MIN, MONTH_SLEEP_MAX)
                    print(f"  → 反爬休眠 {sleep_s:.1f}s ...")
                    time.sleep(sleep_s)

        finally:
            # 5. 保存汇总
            print("\n[4/5] 保存汇总 ...")
            if months_summary:
                _save_summary(months_summary)
            else:
                print("[WARN] 未抓取到任何数据（可能全部月份已通过断点续传跳过）。")

            context.close()
            browser.close()

    print("\n[5/5] 抓取汇总")
    print("=" * 70)
    total_records = sum(m["record_count"] for m in months_summary)
    print(f"  本次抓取月份总数: {len(months_summary)}")
    print(f"  本次抓取记录总数: {total_records}")
    for m in months_summary:
        print(f"    {m['month']}: {m['record_count']} 条")
    print("=" * 70)
    print("[完成] 爬虫流程结束。")


def _save_summary(months_summary: list[dict[str, Any]]) -> None:
    """保存本次运行汇总信息（含全部月份的记录数统计）。

    :param months_summary: 月份维度汇总列表
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = _data_dir()
    summary_path = data_dir / f"city_summary_{timestamp}.json"
    payload = {
        "crawl_time": datetime.now().isoformat(timespec="seconds"),
        "source_url": CITY_URL,
        "months": months_summary,
        "total_records": sum(m["record_count"] for m in months_summary),
    }
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  → 汇总: {summary_path}")


# ============================================================================
# 命令行入口
# ============================================================================

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="车主之家城市上牌数据 Playwright 爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  # 抓取最近一个月（默认 headed，便于调试）
  python scripts/crawl_16888_city/crawl_city.py --months latest

  # 抓取全部历史月份（headless 模式）
  python scripts/crawl_16888_city/crawl_city.py --months all --headless

  # 抓取指定月份（逗号分隔，优先于 --months）
  python scripts/crawl_16888_city/crawl_city.py --target 2026-05,2026-03

  # 断点续传（中断后从上次位置继续）
  python scripts/crawl_16888_city/crawl_city.py --months all --headless --resume

  # 调试: 仅抓前 2 个月份
  python scripts/crawl_16888_city/crawl_city.py --months all --limit 2
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
        "--target",
        type=str,
        default=None,
        help="指定月份（逗号分隔，如 --target 2026-05,2026-03），优先于 --months",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制最大月份数（调试用，如 --limit 2 只抓前2个月份）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="启用断点续传（跳过已完成月份，从断点页继续）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        crawl(
            headless=args.headless,
            months_mode=args.months,
            limit=args.limit,
            resume=args.resume,
            target_months_arg=args.target,
        )
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止，进度已保存，可用 --resume 继续。")
        sys.exit(130)
    except (PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[FATAL] 抓取失败: {exc}")
        sys.exit(1)
    except Exception as exc:                                          # 兜底捕获
        print(f"[FATAL] 未知异常: {exc}")
        raise
