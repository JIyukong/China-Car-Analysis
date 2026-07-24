"""应用包入口 - 初始化日志、配置、创建 FastAPI 实例。

在包被导入时自动初始化日志系统，确保所有模块都能使用统一的日志配置。
"""

from base_infra.logging_config import setup_logging

# 初始化日志系统（幂等操作，多次导入仅生效一次）
setup_logging()
