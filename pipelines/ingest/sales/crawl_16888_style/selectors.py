"""车主之家车型销量页面选择器与正则常量集中管理。

将所有 DOM 选择器、URL 模板、字段提取正则集中在本文件，
便于车主之家页面改版时只需修改一处即可恢复抓取。

页面结构（基于真实 DOM 探测，非假设）:
    - 全页 1 个 ``<table class="xl-table-def xl-table-a">``，
      表头行混在 ``<tbody>`` 第一个 ``<tr>``（``<th>``），后续为数据行（``<td>``）
    - 年月份选择为两个 ``input.xl-date-input``（readonly），共享 1 个
      ``.xl-select-date`` 面板（含 ``.xl-select-year`` 与 ``.xl-select-month``）
    - 分页器 ``.xl-data-pageing``，"下一页"按钮 ``.next``
      **到最后一页时该按钮从 DOM 完全消失**（非 disabled）
    - URL 模式: ``style-{YYYYMM}-{YYYYMM}-{页码}.html``，
      单月抓取时起始=结束（如 ``style-202401-202401-1.html``）
"""

# ----------------------------------------------------------------------------
# 页面 URL
# ----------------------------------------------------------------------------
STYLE_URL = "https://xl.16888.com/style.html"

# 单月分页 URL 模板（起始=结束=同一月）
# 实测规律: style-{YYYYMM}-{YYYYMM}-{页码}.html
PAGE_URL_TEMPLATE = "https://xl.16888.com/style-{ym}-{ym}-{page}.html"

# ----------------------------------------------------------------------------
# 数据表格（全页仅 1 个 table）
# 实测: 表头行混在 tbody 第一个 tr（th），数据行后续 tr（td）
#       thead th 为空，数据从 tbody tr 开始
# ----------------------------------------------------------------------------
TABLE_SELECTOR = "table"
TABLE_BODY_SELECTOR = "table tbody"
TABLE_ROW_SELECTOR = "table tbody tr"

# ----------------------------------------------------------------------------
# 日期选择器（实测，本方案不依赖，仅记录备用）
# 实测: 两个 input.xl-date-input（readonly）共享 1 个 .xl-select-date 面板；
#       年份列表 .xl-select-year li（2017-2026），选中 class="on"；
#       月份列表 .xl-select-month li（12-01 倒序），未来月份 class="prohibit"；
#       查询按钮 .J_xl-btn，点击后跳转 URL
# ----------------------------------------------------------------------------
DATE_INPUT_SELECTOR = "input.xl-date-input"
DATE_SELECT_CONTAINER = ".xl-select-date"
YEAR_LIST_SELECTOR = ".xl-select-year li"
MONTH_LIST_SELECTOR = ".xl-select-month li"
DATE_OPTION_ACTIVE_CLASS = "on"                    # 当前选中项的 class
DATE_OPTION_DISABLED_CLASS = "prohibit"            # 禁用（未来月份）项的 class
QUERY_BUTTON_SELECTOR = ".J_xl-btn"

# ----------------------------------------------------------------------------
# 分页器
# 实测: .xl-data-pageing 容器内含"共N条数据"、页码、上一页/下一页
#       **到最后一页时 .next 按钮从 DOM 完全消失**（最可靠终止条件）
# ----------------------------------------------------------------------------
PAGER_CONTAINER = ".xl-data-pageing"
NEXT_PAGE_BUTTON = ".xl-data-pageing .next"        # 下一页按钮             # 最后一页时消失
CURRENT_PAGE_CLASS = "curr"                         # 当前页码的 class
TOTAL_COUNT_TEXT_REGEX = r"共(\d+)条数据"          # 解析总数据量

# ----------------------------------------------------------------------------
# 年份范围（实测页面最早 2017）
# ----------------------------------------------------------------------------
MIN_YEAR = 2017

# ----------------------------------------------------------------------------
# 字段提取正则
# ----------------------------------------------------------------------------
MONTH_ID_REGEX = r"(\d{4})-(\d{2})"                # data-id 格式 YYYY-MM
PRICE_REGEX = r"(\d+\.\d+\s*-\s*\d+\.\d+)"         # 售价区间             # 如 "6.48 - 9.48"
