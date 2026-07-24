"""FastAPI 应用入口。

M0 阶段仅提供健康检查接口 ``GET /health`` 与根路径欢迎信息。
健康检查会真实探测云 MySQL 连接，但不允许因数据库异常而崩溃。
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from base_infra.db import check_database_connection
from base_infra.logging_config import get_logger

logger = get_logger(__name__)

# FastAPI 应用实例：采集层服务
app = FastAPI(title="中国乘用车投资分析平台 - 采集层", version="0.1.0")


@app.get("/")
def root() -> dict:
    """根路径，返回简单的欢迎信息。"""
    return {"message": "中国乘用车投资分析平台 - 采集层已启动，访问 /health 检查服务状态"}


@app.get("/health")
def health() -> JSONResponse:
    """健康检查接口。

    - 数据库连接正常：返回 200，状态 ``ok``；
    - 数据库连接异常：返回 503，状态 ``degraded``，并在响应体附带错误信息。
    任何异常都被捕获，不会向上抛出导致进程崩溃。
    """
    try:
        if check_database_connection():
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "database": "connected"},
            )

        # 探针返回 False（已记录日志），按降级处理
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "disconnected",
                "error": "数据库连接检查未通过",
            },
        )
    except Exception as e:  # noqa: BLE001 - 健康检查接口必须兜底，禁止向外抛出
        logger.exception("健康检查发生未预期异常")
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "disconnected",
                "error": str(e),
            },
        )
