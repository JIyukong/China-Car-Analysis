# 中国乘用车投资分析

CarApp 是面向汽车行业投资者、分析师和数据开发者的数据底座。它从 16888 和 AKShare 采集销量及行情，在 MySQL 中清洗和汇总，供 Power BI 通过只读账号消费。报表和公开发布仍在路线图中。

## 快速开始

在仓库根目录用 Python 3.11+ 执行以下命令，离线查看任务顺序和触发时间。

```powershell
python -m scheduler.runner --plan
```

需要运行 ETL 时，安装 [运行依赖](requirements.txt)，将 [配置示例](.env.example) 复制为 `.env`，填写已有 MySQL 库的专用 ETL 账号。抓取销量需要执行 `python -m playwright install chromium`。启动定时任务、补跑和账号配置见 [调度和 ETL](docs/scheduler_guide.md)。

数据库操作遵守 [工作区说明](AGENTS.md)。

真实数据库账号和飞书凭据放在本地 `.env`，仓库提交配置模板。常驻服务执行采集、补录同步与数仓发布，Codex 读取本地巡检摘要。补录使用指定的飞书多维表格。部署顺序和恢复命令见 [调度和 ETL](docs/scheduler_guide.md)。

## 冒烟测试

在仓库根目录执行以下命令安装依赖、启用本地提交检查并手动验证。

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
git config --local core.hooksPath .githooks
python -X utf8 -m scheduler.runner --plan
python -X utf8 -m pytest
```

提交钩子对暂存区快照运行调度计划与 pytest，任一步失败都会阻止提交。测试使用临时 CSV、模拟数据库连接和模拟任务，覆盖 API 健康状态、任务注册、调度失败处理、月度重放与增量写入约束。

GitHub Actions 在推送、Pull Request 和合并队列中运行 `CarApp smoke`。工作流在 GitHub 上首次运行后，由仓库管理员在 `main` 的分支保护或规则集中要求通过 Pull Request 合并，并将 `CarApp smoke` 设为必需状态检查。这样本地未安装钩子的提交也必须通过检查才能合并。

## 文档导航

| 想了解什么 | 阅读 |
| --- | --- |
| 怎么启动、补跑、排查跳过和补维 | [调度和 ETL](docs/scheduler_guide.md) |
| 数据从哪里来、经过哪些表 | [数仓分层](docs/warehouse-spec.md) |
| 表的字段、业务键和 DDL 在哪 | [数据字典](docs/data-dictionary.md) |
| 销量、市占率、同比等怎么算 | [指标口径](docs/metric-definition.md) |
| 项目做到哪、下一步验收什么 | [项目路线图](docs/roadmap.md) |
| 查早期设计和评审记录 | [历史归档](docs/archive/README.md) |

## 健康检查

可用 `python -m uvicorn backend.app.main:app` 启动可选的 FastAPI 服务。 `GET /` 返回服务信息，`GET /health` 会实际探测配置的 MySQL，成功返回 HTTP 200，连接失败返回 HTTP 503。开发验证使用上面的离线入口。[服务实现](backend/app/main.py)
