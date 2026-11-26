"""兼容层：主机厂归属调研流程已迁移至 scheduler.feishu_pkg。

新代码请直接从 scheduler.feishu_pkg.ownership_research 导入。
"""

from scheduler.feishu_pkg.ownership_research import *  # noqa: F401,F403
from scheduler.feishu_pkg.ownership_research import run  # noqa: F401
