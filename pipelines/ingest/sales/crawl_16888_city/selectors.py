"""车主之家城市上牌数据页面选择器与正则常量集中管理。

将所有 DOM 选择器、URL 模板、字段提取正则集中在本文件，
便于车主之家页面改版时只需修改一处即可恢复抓取。

设计原则:
    - 优先按结构化标签（table/tbody/tr/td）、``data-*`` 属性、
      稳定 class（16888 为传统 jQuery 站点，class 较稳定）定位元素。
    - 所有正则使用原始字符串 ``r"..."``，避免反斜杠转义问题。

页面结构（基于真实 DOM 探测，非假设）:
    - 全页仅 1 个 ``<table>``，数据在 ``<tbody><tr><td>``
    - 月份下拉为 ``.drop_down_list.mod-downlist > a[data-id]``，
      页面上有 **两套** 同 data-id 的下拉，用户确认操作 **第一套**
    - 分页器为 ``.xl-data-pageing``，"下一页"按钮 ``.next``
      **到最后一页时该按钮从 DOM 完全消失**（非 disabled）
"""

# ----------------------------------------------------------------------------
# 页面 URL
# ----------------------------------------------------------------------------
CITY_URL = "https://xl.16888.com/city.html"

# 分页 URL 模板（用于导航与断点续传）
# 实测规律: city-{YYYY-MM}-0-0-0-0-0-{页码}.html
PAGE_URL_TEMPLATE = "https://xl.16888.com/city-{month}-0-0-0-0-0-{page}.html"

# ----------------------------------------------------------------------------
# 数据表格（全页仅 1 个 table）
# 实测: 标准 HTML <table>，表头 thead，数据 tbody
# ----------------------------------------------------------------------------
TABLE_SELECTOR = "table"
TABLE_BODY_SELECTOR = "table tbody"
TABLE_ROW_SELECTOR = "table tbody tr"

# ----------------------------------------------------------------------------
# 月份下拉（页面上有两套，取第一套）
# 实测: 页面有"按品牌/车型查询"和"按级别/价格查询"两个区域，各含一套下拉；
#       两套 data-id 完全一致，用户确认操作第一套（索引 0）
# ----------------------------------------------------------------------------
MONTH_DROPDOWN_CONTAINER = ".drop_down_list.mod-downlist"
MONTH_DROPDOWN_INDEX = 0                            # 第一套下拉             # 两套同 data-id，取第一套
MONTH_OPTION_SELECTOR = "a[data-id]"
MONTH_OPTION_ACTIVE_CLASS = "on"                    # 当前选中项的 class     # <a class="on" data-id="2026-05">
MONTH_DROPDOWN_TRIGGER = "a.select.drop_checked"

# ----------------------------------------------------------------------------
# 分页器
# 实测: .xl-data-pageing.lbBox 容器内含"共N条数据"、页码、上一页/下一页
#       **到最后一页时 .next 按钮从 DOM 完全消失**（最可靠终止条件）
# ----------------------------------------------------------------------------
PAGER_CONTAINER = ".xl-data-pageing"
NEXT_PAGE_BUTTON = ".xl-data-pageing .next"         # 下一页按钮             # 最后一页时消失
CURRENT_PAGE_CLASS = "curr"                          # 当前页码的 class      # <a class="lineBlock num curr">
TOTAL_COUNT_TEXT_REGEX = r"共(\d+)条数据"           # 解析总数据量

# ----------------------------------------------------------------------------
# 字段提取正则
# ----------------------------------------------------------------------------
MONTH_ID_REGEX = r"(\d{4})-(\d{2})"                 # data-id 格式 YYYY-MM
PRICE_REGEX = r"(\d+\.\d+-\d+\.\d+)"                # 指导价区间             # 如 "7.98-13.68"
