"""批次状态机的全部合法状态。

状态以字符串形式持久化在 state.sqlite3 的 batch 表中，StrEnum 保证枚举值与
历史库中已存的字符串完全一致，可直接用于 SQL 参数与 JSON 序列化。
"""

from enum import StrEnum


class BatchStatus(StrEnum):
    # 月链采集中，尚未产出可对账的来源清单
    COLLECTING = "collecting"
    # 已排队等待来源数据（日链缺源或月链入队）
    WAITING_SOURCE = "waiting_source"
    # 新浪单源后复权初始化切换未完成，阻塞 load_stock_daily 链
    WAITING_REBASE = "waiting_rebase"
    # 缺少交易日历，无法判断是否应采
    WAITING_CALENDAR = "waiting_calendar"
    # 当日休市或无应采标的，终态
    CLOSED = "closed"
    # 来源已采集并通过对账，等待发布
    READY = "ready"
    # 财报批次已探测到新 ODS 数据，等待后续发布（仅 load_stock_finance 链使用）
    RECONCILING = "reconciling"
    # 来源存在缺口，等待飞书人工补录
    WAITING_MANUAL = "waiting_manual"
    # 采集或来源入库失败
    SOURCE_ERROR = "source_error"
    # ODS 已验收，但 DWD/DWS 尚未发布
    ODS_ACCEPTED = "ods_accepted"
    # 发布过程失败，可重试
    PUBLISH_ERROR = "publish_error"
    # 全链路发布完成，终态
    PUBLISHED = "published"
