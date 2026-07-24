# 后端 API 接口文档

> FastAPI 后端接口规范，当前仅有健康检查，后续随里程碑扩展。

---

## 基础信息

| 项目 | 值 |
|------|-----|
| 基础地址 | `http://localhost:8000` |
| 框架 | FastAPI + Uvicorn |
| 启动命令 | `uvicorn backend.app.main:app --reload` |
| API 文档 | `http://localhost:8000/docs`（Swagger UI） |

---

## 已有接口

### GET /

- **描述**：根路径欢迎信息
- **响应示例**：

```json
{
    "message": "Welcome to CarApp Data Platform"
}
```

### GET /health

- **描述**：健康检查，探测数据库连接状态
- **成功响应**（200）：

```json
{
    "status": "ok",
    "database": "connected"
}
```

- **降级响应**（503）：

```json
{
    "status": "degraded",
    "database": "disconnected",
    "error": "..."
}
```

---

## 规划接口（已废弃）

> **以下接口在 Power BI 直连云 MySQL 方案下不再需要**（需求文档 v1.3 已明确砍掉给前端用的查询 API）。Power BI 通过导入模式直接从云 MySQL 拉取数据，无需后端查询接口。保留此表仅作历史参考。

| 接口 | 方法 | 原计划里程碑 | 描述 | 状态 |
|------|------|--------|------|------|
| `/api/v1/sales/monthly` | GET | M4 | 查询月度销量数据 | ❌ 已废弃 |
| `/api/v1/sales/company` | GET | M4 | 按上市公司查询销量汇总 | ❌ 已废弃 |
| `/api/v1/stock/daily` | GET | M2 | 查询股价日线数据 | ❌ 已废弃 |
| `/api/v1/dim/models` | GET | M4 | 查询车型维度列表 | ❌ 已废弃 |
| `/api/v1/scheduler/jobs` | GET/POST | M3 | 查询/创建调度任务 | ❌ 已废弃 |
