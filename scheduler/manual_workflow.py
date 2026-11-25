"""兼容层：飞书手工补录轮询已迁移至 scheduler.feishu_pkg。

新代码请直接从 scheduler.feishu_pkg.manual_workflow 导入；
本 shim 保留旧导入路径（测试与 runner 的延迟 import 仍在使用）。
"""

from scheduler.feishu_pkg.manual_workflow import *  # noqa: F401,F403
from scheduler.feishu_pkg.manual_workflow import (  # noqa: F401
    _bind,
    _ensure_ticket,
    _submit,
    _sync,
    _tickets,
    poll,
)
