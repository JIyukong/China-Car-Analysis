"""兼容层：飞书 ADS 维表同步已迁移至 scheduler.feishu_pkg。

新代码请直接从 scheduler.feishu_pkg.ads_workflow 导入。
"""

from scheduler.feishu_pkg.ads_workflow import *  # noqa: F401,F403
from scheduler.feishu_pkg.ads_workflow import poll, sync  # noqa: F401
