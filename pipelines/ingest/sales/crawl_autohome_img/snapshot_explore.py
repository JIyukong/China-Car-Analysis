"""汽车之家报价页 DOM 结构探测脚本（一次性，用于确认选择器）。

目标:
    在编写正式抓取脚本前，先用 Playwright 打开目标页面，输出真实的 DOM 结构，
    确认每个目标元素的实际 tag / role / class / 层级，**不假设标准结构**。

探测内容:
    1. 确认 main#app → div.tw-flex-1 → div.infinite-scroll-component__outerdiv → li 的真实层级
    2. 打印首个 LI 的完整 outerHTML（看车型名称的真实 tag/位置，img 的真实属性）
    3. 统计 LI 总数（滚动加载后）
    4. 截图保存到 data/snapshot.png

用法:
    python scripts/crawl_autohome_series_img/snapshot_explore.py

说明:
    - 本脚本不落地业务数据，仅产出"选择器决策依据"
    - 运行后请人工分析输出，据此修正 selectors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from base_infra.logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# 常量配置
# ============================================================================

TARGET_URL = "https://www.autohome.com.cn/price#pvareaid=6861598"              # 目标报价页

PAGE_TIMEOUT_MS = 30_000                                                       # 页面加载超时（毫秒）
SCROLL_WAIT_MS = 1000                                                          # 每次滚动后等待毫秒数
SCROLL_MAX_ROUNDS = 15                                                         # 最大滚动轮数（探测用，够看结构即可）

# UA（避免被识别为机器人）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ============================================================================
# 工具函数
# ============================================================================

def _data_dir() -> Path:
    """返回 data 目录路径（不存在则创建）。"""
    d = Path(__file__).resolve().parent / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _inject_anti_detect(context: BrowserContext) -> None:
    """向浏览器上下文注入反 webdriver 检测脚本。

    :param context: Playwright 浏览器上下文
    """
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
    """)


def launch_browser(pw: Playwright) -> tuple[Browser, BrowserContext, Page]:
    """启动 Chromium 浏览器（headed 模式，便于观察）。

    :param pw: Playwright 实例
    :returns: (browser, context, page) 三元组
    """
    browser = pw.chromium.launch(headless=False)                               # 探测用 headed
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="zh-CN",
    )
    _inject_anti_detect(context)
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    return browser, context, page


# ============================================================================
# 探测逻辑
# ============================================================================

def explore(page: Page) -> None:
    """执行 DOM 结构探测，输出关键信息到控制台。

    :param page: Playwright 页面对象（已导航到目标页）
    """
    print("=" * 70)
    print("[1] 探测用户描述的层级链路是否存在 ...")
    print("=" * 70)

    # 逐层检查用户描述的层级：main#app → div.tw-flex-1 → ... → li
    chain_info = page.evaluate("""() => {
        const result = {};
        const app = document.querySelector('main#app');
        result.has_main_app = !!app;
        if (!app) return result;

        const flex = app.querySelector('div.tw-flex-1');
        result.has_tw_flex_1 = !!flex;
        if (!flex) {
            // 列出 main#app 下所有直接子 div 的 class，帮助定位
            result.main_children = Array.from(app.children).map(c => ({
                tag: c.tagName, id: c.id, className: c.className,
            }));
            return result;
        }

        const scroll = flex.querySelector('div.infinite-scroll-component__outerdiv');
        result.has_scroll_outer = !!scroll;
        if (!scroll) {
            result.flex_children = Array.from(flex.children).map(c => ({
                tag: c.tagName, id: c.id, className: c.className,
            }));
            return result;
        }

        const lis = scroll.querySelectorAll('li');
        result.li_count_in_scroll = lis.length;
        // 同时检查 scroll 下更深的 li（可能在子容器里）
        return result;
    }""")
    print(f"  main#app 存在: {chain_info.get('has_main_app')}")
    print(f"  div.tw-flex-1 存在: {chain_info.get('has_tw_flex_1')}")
    print(f"  div.infinite-scroll-component__outerdiv 存在: {chain_info.get('has_scroll_outer')}")
    print(f"  该层下 LI 数量: {chain_info.get('li_count_in_scroll', 'N/A')}")
    if "main_children" in chain_info:
        print(f"  [提示] main#app 直接子节点: {chain_info['main_children'][:5]}")
    if "flex_children" in chain_info:
        print(f"  [提示] div.tw-flex-1 直接子节点: {chain_info['flex_children'][:5]}")

    print()
    print("=" * 70)
    print("[2] 全局统计 LI（不限定层级，看页面总共有多少 li） ...")
    print("=" * 70)
    total_li = page.evaluate("document.querySelectorAll('li').length")
    print(f"  全页面 LI 总数: {total_li}")

    print()
    print("=" * 70)
    print("[3] 打印首个疑似车型 LI 的完整 outerHTML（看真实结构） ...")
    print("=" * 70)

    # 取用户描述路径下的首个 li；若不存在则取全页首个含 img 的 li
    first_li_html = page.evaluate("""() => {
        let li = null;
        // 优先按用户描述路径取
        const app = document.querySelector('main#app');
        if (app) {
            const flex = app.querySelector('div.tw-flex-1');
            if (flex) {
                const scroll = flex.querySelector('div.infinite-scroll-component__outerdiv');
                if (scroll) {
                    li = scroll.querySelector('li');
                }
            }
        }
        // 回退：取全页首个含 img 的 li
        if (!li) {
            const all = document.querySelectorAll('li');
            for (const l of all) {
                if (l.querySelector('img')) { li = l; break; }
            }
        }
        if (!li) return null;
        return li.outerHTML.slice(0, 3000);                                    // 截断防止过长
    }""")
    if first_li_html:
        print(first_li_html)
    else:
        print("  [WARN] 未找到任何含 img 的 li 节点。")

    print()
    print("=" * 70)
    print("[4] 分析 LI 内图片属性（src / data-src / lazy-src 等） ...")
    print("=" * 70)
    img_attrs = page.evaluate("""() => {
        let li = null;
        const app = document.querySelector('main#app');
        if (app) {
            const flex = app.querySelector('div.tw-flex-1');
            if (flex) {
                const scroll = flex.querySelector('div.infinite-scroll-component__outerdiv');
                if (scroll) li = scroll.querySelector('li');
            }
        }
        if (!li) {
            const all = document.querySelectorAll('li');
            for (const l of all) {
                if (l.querySelector('img')) { li = l; break; }
            }
        }
        if (!li) return null;
        const img = li.querySelector('img');
        if (!img) return { has_img: false };
        // 收集 img 的所有属性
        const attrs = {};
        for (const attr of img.attributes) {
            attrs[attr.name] = attr.value;
        }
        return { has_img: true, img_attrs: attrs, img_alt: img.alt };
    }""")
    if img_attrs:
        print(f"  含 img: {img_attrs.get('has_img')}")
        if img_attrs.get("has_img"):
            print(f"  img 全部属性: {img_attrs.get('img_attrs')}")
            print(f"  img alt: {img_attrs.get('img_alt')}")

    print()
    print("=" * 70)
    print("[5] 探测车型名称所在节点（li 内文本节点列表） ...")
    print("=" * 70)
    name_info = page.evaluate("""() => {
        let li = null;
        const app = document.querySelector('main#app');
        if (app) {
            const flex = app.querySelector('div.tw-flex-1');
            if (flex) {
                const scroll = flex.querySelector('div.infinite-scroll-component__outerdiv');
                if (scroll) li = scroll.querySelector('li');
            }
        }
        if (!li) {
            const all = document.querySelectorAll('li');
            for (const l of all) {
                if (l.querySelector('img')) { li = l; break; }
            }
        }
        if (!li) return null;
        // 收集 li 内所有有文本的直接/间接元素（取前 10 个有非空文本的）
        const texts = [];
        const walker = document.createTreeWalker(li, NodeFilter.SHOW_ELEMENT);
        let node = walker.currentNode;
        while (node && texts.length < 10) {
            const t = (node.textContent || '').trim();
            // 只取自身直接文本（不含子孙文本）的元素
            const directText = Array.from(node.childNodes)
                .filter(n => n.nodeType === 3)
                .map(n => n.textContent.trim())
                .join(' ');
            if (directText) {
                texts.push({
                    tag: node.tagName,
                    className: (node.className || '').toString().slice(0, 80),
                    direct_text: directText.slice(0, 50),
                });
            }
            node = walker.nextNode();
        }
        return texts;
    }""")
    if name_info:
        for item in name_info:
            print(f"  <{item['tag']} class='{item['className']}'> -> \"{item['direct_text']}\"")
    else:
        print("  [WARN] 未提取到文本节点信息。")


# ============================================================================
# 主流程
# ============================================================================

def main() -> None:
    """探测主流程：导航 → 滚动触发懒加载 → 输出 DOM 结构 → 截图。"""
    print("=" * 70)
    print("汽车之家报价页 DOM 结构探测脚本启动")
    print(f"  目标 URL: {TARGET_URL}")
    print("=" * 70)
    logger.info(f"探测脚本启动: {TARGET_URL}")

    with sync_playwright() as pw:
        browser, context, page = launch_browser(pw)
        try:
            # 1. 导航
            print("[1/4] 导航到目标页面 ...")
            page.goto(TARGET_URL, wait_until="networkidle")
            print("[OK] 页面加载完成。")

            # 2. 滚动若干次触发懒加载（探测用，不需要滚到底）
            print("[2/4] 滚动触发懒加载 ...")
            for i in range(SCROLL_MAX_ROUNDS):
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(SCROLL_WAIT_MS)
                curr = page.evaluate("document.querySelectorAll('li').length")
                print(f"  第 {i + 1} 轮滚动后，LI 总数: {curr}")
                if i >= 4:                                                       # 探测滚 5 轮足够看结构
                    break

            # 3. 探测 DOM 结构
            print("[3/4] 探测 DOM 结构 ...")
            explore(page)

            # 4. 截图保存
            print("[4/4] 保存截图 ...")
            shot_path = _data_dir() / "snapshot.png"
            page.screenshot(path=str(shot_path), full_page=False)
            print(f"[OK] 截图已保存: {shot_path}")

        except PlaywrightTimeoutError as exc:
            print(f"[ERROR] 页面操作超时: {exc}")
            logger.error(f"探测超时: {exc}")
        finally:
            context.close()
            browser.close()

    print("[完成] 探测结束。请根据以上输出修正 selectors.py。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止。")
        sys.exit(130)
    except Exception as exc:
        print(f"[FATAL] 探测失败: {exc}")
        logger.exception(f"探测失败: {exc}")
        raise
