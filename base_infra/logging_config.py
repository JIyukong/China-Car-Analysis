"""日志配置模块 - 全局单例，backend 和 pipelines 共用。

核心设计：
- 使用 Python 标准库 logging，无需额外依赖
- 双输出：控制台（彩色）+ 文件（按天轮转）
- 日志级别从环境变量或 Settings 读取
- 兼容 FastAPI uvicorn 日志集成

使用方式：
    from base_infra.logging_config import get_logger
    logger = get_logger(__name__)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ----------------------------------------------------------------------------
# 配置常量
# ----------------------------------------------------------------------------

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))                     # 日志目录
LOG_FILE = "app.log"                                              # 日志文件名
LOG_MAX_BYTES = 10 * 1024 * 1024                                   # 单文件最大 10MB
LOG_BACKUP_COUNT = 7                                               # 保留 7 天轮转文件
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()               # 日志级别
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"  # 是否写入文件
LOG_TO_CONSOLE = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"  # 是否输出控制台
DISABLE_COLOR = os.getenv("DISABLE_COLOR", "false").lower() == "true"  # 禁用彩色输出

# 彩色输出转义码（Windows Terminal / Unix 通用的 ANSI 码）
if DISABLE_COLOR or not sys.stdout.isatty():  # 非交互终端禁用颜色
    RESET = YELLOW = GREEN = RED = CYAN = ""
else:
    RESET = "\033[0m"
    YELLOW = "\033[33m"    # WARNING
    GREEN = "\033[32m"     # INFO
    RED = "\033[31m"       # ERROR
    CYAN = "\033[36m"      # DEBUG

# 日志级别对应的颜色
LEVEL_COLORS = {
    "DEBUG": CYAN,
    "INFO": GREEN,
    "WARNING": YELLOW,
    "ERROR": RED,
    "CRITICAL": RED,
}

# ----------------------------------------------------------------------------
# 格式化器
# ----------------------------------------------------------------------------

class ColoredFormatter(logging.Formatter):
    """自定义格式化器：为不同日志级别添加颜色。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录，为级别添加颜色。"""
        # 获取级别对应的颜色
        level_color = LEVEL_COLORS.get(record.levelname, RESET)
        # 格式化级别为固定宽度（8字符）
        level_name = record.levelname.ljust(8)
        # 组装彩色级别
        record.levelname = f"{level_color}{level_name}{RESET}"
        # 调用父类格式化
        return super().format(record)


def _build_formatter(is_console: bool = False) -> logging.Formatter:
    """构建日志格式化器。

    :param is_console: True=控制台格式（带颜色），False=文件格式（纯文本）
    :returns: 格式化器实例
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    if is_console:
        return ColoredFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
    else:
        return logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# 日志初始化
# ----------------------------------------------------------------------------

_initialized = False


def setup_logging() -> None:
    """初始化全局日志配置（幂等操作，多次调用仅生效一次）。"""
    global _initialized
    if _initialized:
        return

    # 创建日志目录（如果不存在）
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 获取根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    # 清除已有的 handlers（避免重复添加）
    root_logger.handlers.clear()

    # 添加控制台处理器
    if LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(LOG_LEVEL)
        console_handler.setFormatter(_build_formatter(is_console=True))
        root_logger.addHandler(console_handler)

    # 添加文件处理器（按大小轮转）
    if LOG_TO_FILE:
        log_path = LOG_DIR / LOG_FILE
        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(LOG_LEVEL)
        file_handler.setFormatter(_build_formatter(is_console=False))
        root_logger.addHandler(file_handler)

    # 标记已初始化
    _initialized = True

    # 记录启动日志
    logger = logging.getLogger(__name__)
    logger.info(
        f"日志系统已初始化: 级别={LOG_LEVEL}, "
        f"控制台={'开启' if LOG_TO_CONSOLE else '关闭'}, "
        f"文件={'开启' if LOG_TO_FILE else '关闭'}, "
        f"目录={LOG_DIR}"
    )


def get_logger(name: str) -> logging.Logger:
    """获取已配置的日志记录器。

    :param name: 日志记录器名称，通常使用 __name__
    :returns: 配置好的 logger 实例
    """
    # 确保日志系统已初始化
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)


# ----------------------------------------------------------------------------
# uvicorn 日志集成
# ----------------------------------------------------------------------------

def setup_uvicorn_logging() -> dict:
    """配置 uvicorn 日志。

    :returns: uvicorn 日志配置字典
    """
    # 确保日志已初始化
    if not _initialized:
        setup_logging()

    # 返回 uvicorn 日志配置，使用项目的 logger
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "base_infra.logging_config._build_formatter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
