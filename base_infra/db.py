"""数据库引擎与会话工厂。

采用 SQLAlchemy 2.0 风格：使用 ``create_engine`` 与 ``sessionmaker``。
M0 阶段只暴露引擎与会话工厂，不定义 ORM 模型类。
连接串由 ``Settings.database_url`` 属性从分字段拼接而来。
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from base_infra.config import get_settings
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# 从配置单例读取数据库连接串（分字段拼接）
settings = get_settings()

# 全局引擎：开启 pool_pre_ping 避免长连接被云 MySQL 主动断开后报错；
# pool_recycle 设为 3600 秒，与云数据库常见 wait_timeout 错峰回收
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# 会话工厂：关闭 autoflush 防止意外的隐式刷写，关闭 autocommit 强制显式提交
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def check_database_connection() -> bool:
    """检查数据库连接是否可用。

    执行 ``SELECT 1`` 作为探针语句：
    - 成功：返回 ``True``；
    - 异常：捕获后记录错误日志并返回 ``False``，保证调用方（如健康检查接口）
      不会因数据库故障而崩溃。
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001 - 健康检查需要兜底捕获所有异常
        logger.error("数据库连接检查失败：%s", e)
        return False
