"""应用配置模块。

基于 pydantic-settings 从环境变量与 ``.env`` 文件加载配置。
v2.0 采用分字段配置（DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME），
由 ``database_url`` 属性内部拼接为 SQLAlchemy 连接串，
避免用户手填整条 DATABASE_URL。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。

    所有字段均可由 ``.env`` 文件或进程环境变量覆盖。
    字段名大小写敏感（``case_sensitive=True``），
    故环境变量名必须与字段名完全一致。
    """

    # 云 MySQL 连接信息（分字段，由 database_url 属性拼接）
    DB_HOST: str = "127.0.0.1"                                       # 数据库主机
    DB_PORT: int = 3306                                              # 数据库端口
    DB_USER: str = ""                                                # 写账号用户名
    DB_PASSWORD: str = ""                                            # 写账号密码（必填，缺失时启动报错）
    DB_NAME: str = "dw_auto_stock"                                   # 数据库名

    # Power BI 只读账号（仅文档占位，实际在 02_create_powerbi_user.sql 注入）
    POWERBI_READONLY_USER: str = ""                                  # 只读账号用户名
    POWERBI_READONLY_PASSWORD: str = ""                              # 只读账号密码

    # 应用运行配置
    APP_ENV: str = "local"                                           # 运行环境：local/dev/prod
    APP_DEBUG: bool = False                                          # 调试模式（生产应置为 False）
    BACKEND_HOST: str = "127.0.0.1"                                  # 后端监听地址
    BACKEND_PORT: int = 8000                                         # 后端监听端口

    # 路径与告警配置
    PROJECT_ROOT: str = str(Path(__file__).resolve().parents[1])     # 项目根目录（scheduler 等模块定位日志/脚本用）
    ALERT_WEBHOOK_URL: str = ""                                      # 调度器告警机器人 Webhook（飞书/企业微信，留空则不告警）
    ALERT_WEBHOOK_TYPE: str = "feishu"                               # 告警机器人类型：feishu / wecom

    # 模型配置：从 .env 文件读取，字段大小写敏感
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @property
    def database_url(self) -> str:
        """拼接 SQLAlchemy 连接串。

        :returns: mysql+pymysql://user:password@host:port/dbname?charset=utf8mb4
        :raises ValueError: DB_PASSWORD 为空时抛出，提示用户配置
        """
        if not self.DB_PASSWORD:
            raise ValueError("请配置 DB_PASSWORD（在 .env 文件中填写数据库写账号密码）")
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    """返回配置单例。

    使用 ``functools.lru_cache`` 缓存，进程内只解析一次 ``.env``。
    单元测试如需重置，可调用 ``get_settings.cache_clear()``。
    """
    return Settings()
