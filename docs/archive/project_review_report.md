# CarApp 项目诊断审查报告

> 历史快照，保留当时的决策和实现记录。当前入口见 [项目首页](../../README.md)。

> 审查日期：2026-08-12  
> 审查范围：代码与架构、数据工程链路、功能与里程碑进度、文档治理  
> 审查方式：基于仓库真实代码的静态阅读与分析；未运行数据库相关脚本，未修改任何代码  
> 报告形式：每个维度分别列出“做得好的/当前无需改动”与“需要优化的”，并按 P0/P1/P2 分级建议

---

## 1. 执行摘要

`CarApp`（中国乘用车投资分析平台）已经从“方案原型”进化为“主链可跑、调度框架较完整”的数据工程仓库。核心优势是：**模块分层清晰、调度框架健壮、数据管道幂等设计到位、文档意识强**。但当前最突出的问题是**文档与代码状态存在偏差**——部分 README/执行大纲/warehouse-spec 中的“断点”描述已经被代码实现，但测试文件没有同步更新，导致 `pytest` 很可能无法通过。其次是**工程工具链（lint、typecheck、CI）缺失**，以及**部分数据管道脚本存在高度重复**，长期维护成本会上升。

按优先级排序，建议立即处理：

1. **P0**：修复已失效的测试引用，同步文档与代码状态，避免误导新人与维护者。
2. **P1**：引入最小开发工具链（ruff/mypy/pytest 配置），抽象重复的数据采集/调度薄壳。
3. **P2**：补齐 CI、统一日志目录、整理过时文档路径引用。

---

## 2. 总体判断

| 维度 | 状态 | 一句话判断 |
|------|------|-----------|
| 代码与架构 | 良 | 分层清晰、配置/日志/数据库连接/调度框架都较为成熟，但缺少 lint/typecheck 和 CI。 |
| 数据工程链路 | 良 | ODS→DIM→DWD→DWS 链路完整且幂等，采集脚本有预检、重试、fallback，但脚本间重复较多。 |
| 功能与里程碑 | 部分文档滞后 | 代码中 M3 调度链路实际已补齐大部分，但文档仍停留在“部分完成/未接线”阶段。 |
| 文档治理 | 中 | 文档体系完整，但存在与代码路径、状态不一致的问题，需要一次同步治理。 |

---

## 3. 分维度审查

### 3.1 代码与架构

#### 做得好的 / 当前无需改动

| 项 | 说明 | 关键文件 |
|----|------|---------|
| 模块分层清晰 | `base_infra`（配置/日志/DB）、`backend`（API）、`pipelines`（数据管道）、`scheduler`（调度）、`db`（DDL/SP）职责边界明确，符合数据工程仓库惯例。 | 项目根目录结构 |
| 配置管理合理 | 使用 `pydantic-settings` 从 `.env` 加载；分字段拼接 `DATABASE_URL`，避免手填整串连接串；敏感字段不打印。 | [base_infra/config.py](../../base_infra/config.py) |
| 数据库连接健壮 | `pool_pre_ping=True` + `pool_recycle=3600` 避免云 MySQL 长连接被主动断开；`SessionLocal` 关闭 autoflush/autocommit，强制显式提交。 | [base_infra/db.py](../../base_infra/db.py) |
| 日志体系完整 | 标准库 logging + 彩色控制台 + 按大小轮转文件；支持 `LOG_LEVEL`/`LOG_DIR` 环境变量；uvicorn 集成配置就绪。 | [base_infra/logging_config.py](../../base_infra/logging_config.py) |
| 健康检查设计得当 | FastAPI 只暴露根路径与健康检查，健康检查探针失败时返回 503 而非崩溃，符合采集层定位。 | [backend/app/main.py](../../backend/app/main.py) |
| 调度框架健壮 | 子进程执行、超时强杀进程树、失败重试、下游依赖触发、启动恢复（僵尸任务清理/缺口补触发）、心跳、告警，覆盖较全。 | [scheduler/runtime.py](../../scheduler/runtime.py)、[scheduler/alert.py](../../scheduler/alert.py) |
| 交易日历守卫 | 日链任务通过 `trade_calendar` 配置做交易日守卫，非交易日自动跳过。 | [scheduler/task_config.py](../../scheduler/task_config.py) |
| 类型注解覆盖较好 | 核心模块使用 `from __future__ import annotations` 和类型注解，提升可读性和 IDE 支持。 | 多个管道模块 |

#### 需要优化的

| 优先级 | 问题 | 影响 | 建议 | 关键文件 |
|--------|------|------|------|---------|
| **P0** | 测试文件 `test_sales_dimension_rules.py` 引用了 `build_sales_dimensions.py` 中已不存在的函数 `build_oem_brand_lookup`、`render_dim_model_upsert_sql`，会导致导入失败。 | 当前测试套件无法通过，CI 或本地 `pytest` 直接报错，误导项目质量。 | 要么恢复/迁移这些函数到公共模块，要么更新测试用例，使其与当前 [build_sales_dimensions.py](../../pipelines/dim/build_sales_dimensions.py) 的实际 API 对齐。 | `tests/test_sales_dimension_rules.py` |
| P1 | 缺少 lint/typecheck/formatter 工具链配置。 | 代码风格一致性靠人工，长期容易出现 import 混乱、类型漂移、未使用变量累积。 | 在 `pyproject.toml` 增加 `ruff`/`mypy` 配置，并在 `requirements.txt`（或 `requirements-dev.txt`）声明开发依赖；逐步修复现存问题。 | [pyproject.toml](../../pyproject.toml)、[requirements.txt](../../requirements.txt) |
| P1 | 日志目录默认 `LOG_DIR=logs` 为相对路径，可能污染项目根目录。 | 默认运行会在仓库内生成 `logs/` 目录，容易被误提交或误删。 | 将默认日志目录改为项目根目录之外的系统日志目录（如 `/var/log/carapp` 或 `~/.carapp/logs`），或显式加入 `.gitignore` 并提醒用户配置。 | [base_infra/logging_config.py](../../base_infra/logging_config.py) |
| P2 | 没有 CI/CD 配置（如 GitHub Actions）。 | 无法自动运行测试、lint、typecheck，质量问题难以及时发现。 | 增加最小 CI workflow：安装依赖 → 运行 `ruff check` / `mypy` / `pytest`（Mock 掉数据库依赖）。 | 项目根目录 `.github/workflows/` |
| P2 | 缺少 Makefile 或任务脚本汇总。 | 新人需要反复查阅 README 才能知道常用命令。 | 增加 `Makefile` 或 `task` 脚本，封装 `init-db`、`run-scheduler`、`run-tests`、`lint` 等高频命令。 | 项目根目录 |
| P2 | 部分 broad `except Exception` 虽然业务合理，但可进一步分类。 | 长期告警噪音可能增加，难以区分网络/数据/代码缺陷。 | 在 `network_runtime.classify_fetch_error` 基础上，统一各管道的异常分类体系，避免在 `except` 块中重新写字符串判断。 | [pipelines/ingest/stock/load_stock_daily_akshare.py](../../pipelines/ingest/stock/load_stock_daily_akshare.py) 等采集脚本 |

---

### 3.2 数据工程链路

#### 做得好的 / 当前无需改动

| 项 | 说明 | 关键文件 |
|----|------|---------|
| 数仓分层明确 | ODS→DIM→DWD→DWS→ADS 各层目录与表前缀清晰，且文档反复强调“以代码现状为准”。 | [docs/warehouse-spec.md](../warehouse-spec.md)、[db/](../../db) 分层目录 |
| ODS 幂等入库 | style/city 销量文件通过 `row_hash` + `ON DUPLICATE KEY UPDATE` 写入 ODS，支持重复运行。 | [pipelines/ingest/sales/load_16888_style_to_ods.py](../../pipelines/ingest/sales/load_16888_style_to_ods.py)、[pipelines/ingest/sales/load_16888_city_to_ods.py](../../pipelines/ingest/sales/load_16888_city_to_ods.py) |
| 采集网络层健壮 | 统一代理/NO_PROXY 处理、预检、指数退避重试、异常分类、fallback 源切换。 | [pipelines/ingest/stock/network_runtime.py](../../pipelines/ingest/stock/network_runtime.py) |
| 多数据源 fallback | A 股日线提供 em/tx/sina 三源 fallback，港股提供 em/sina 两源。 | [pipelines/ingest/stock/load_stock_daily_akshare.py](../../pipelines/ingest/stock/load_stock_daily_akshare.py) |
| 数据质量校验有入口 | `scripts/data_quality_check.py` 覆盖 ODS 行数/空值/唯一性、DIM/Bridge 一致性。 | [scripts/data_quality_check.py](../../scripts/data_quality_check.py)、[pipelines/common/quality.py](../../pipelines/common/quality.py) |
| DWD 清洗有阻断保护 | 城市销量清洗会检测 `dim_model` 未匹配行数和同名车型扇出，超过阈值主动报错，避免脏数据写入。 | [pipelines/staging/clean_sales_city_dwd.py](../../pipelines/staging/clean_sales_city_dwd.py) |
| 财务比率归一化 | DWD 财务清洗将 [0,1] 区间的 roe/gross_margin/debt_ratio 乘以 100，避免单位混乱。 | [pipelines/staging/clean_stock_finance_dwd.py](../../pipelines/staging/clean_stock_finance_dwd.py) |
| 股票 DWD 按年分批 | 股价日线清洗按年份分批 `INSERT IGNORE`，并独立回补 `pct_change`，降低单事务锁持有。 | [pipelines/staging/clean_stock_daily_dwd.py](../../pipelines/staging/clean_stock_daily_dwd.py) |
| 存储过程下沉合理 | 复杂集合运算（维度派生、DWS 聚合）下沉到 MySQL SP，Python 只保留薄壳调用，符合数据工程最佳实践。 | [db/procedures/](../../db/procedures) |

#### 需要优化的

| 优先级 | 问题 | 影响 | 建议 | 关键文件 |
|--------|------|------|------|---------|
| P1 | 股票日线、股票财务、同花顺指数、商品期货四个采集脚本结构高度重复（`STATS`、参数解析、重试、汇总、入库等）。 | 新增数据源时需要复制粘贴大量模板代码，维护一致性成本高。 | 抽象一个 `BaseIngestJob` / `BaseAkshareIngest` 类或模板函数，将 `parse_args`、`get_all_items`、`fetch_with_retry`、`transform`、`insert`、`print_summary` 等通用逻辑收口。 | [pipelines/ingest/stock/load_stock_daily_akshare.py](../../pipelines/ingest/stock/load_stock_daily_akshare.py)、[pipelines/ingest/stock/load_stock_finance_akshare.py](../../pipelines/ingest/stock/load_stock_finance_akshare.py)、[pipelines/ingest/index/load_ths_index_daily_akshare.py](../../pipelines/ingest/index/load_ths_index_daily_akshare.py)、[pipelines/ingest/futures/load_futures_main_sina.py](../../pipelines/ingest/futures/load_futures_main_sina.py) |
| P1 | 调度器 job 薄壳 `load_stock_ods_job.py` / `load_index_ods_job.py` / `load_futures_ods_job.py` 的 `_build_scheduler_config` 函数重复。 | 同样的运行时配置构造逻辑分散在三处。 | 抽到 `scheduler/runtime.py` 或 `scheduler/config.py` 中的公共工厂函数。 | [scheduler/jobs/load_stock_ods_job.py](../../scheduler/jobs/load_stock_ods_job.py)、[scheduler/jobs/load_index_ods_job.py](../../scheduler/jobs/load_index_ods_job.py)、[scheduler/jobs/load_futures_ods_job.py](../../scheduler/jobs/load_futures_ods_job.py) |
| P1 | `scripts/data_quality_check.py` 未接入调度器，且未覆盖 DWD  stock/index/futures 表、DWD 城市销量表。 | 数据质量检查成为一次性脚本，难以在每次调度后自动运行。 | 将数据质量校验拆分为“每日调度后自动校验”和“月度深度校验”两类，分别接入 `task_config.py`；或至少让 DWD 清洗任务成功后触发轻量级校验。 | [scripts/data_quality_check.py](../../scripts/data_quality_check.py)、[scheduler/task_config.py](../../scheduler/task_config.py) |
| P2 | `load_stock_finance` 调度任务配置为 `mon-fri` 17:30，但没有 `trade_calendar` 守卫；财务数据并非每日发布。 | 任务会每天运行，虽然采集脚本内部按 `MAX(report_date)` 增量，但逻辑上更像日检而非财务发布驱动。 | 明确财务采集策略：要么改为低频次（如每周一次），要么保留日检但文档说明原因。 | [scheduler/task_config.py](../../scheduler/task_config.py) |
| P2 | `data_quality_check.py` 中的阈值（如 `min_rows=10000`、`min_rows=100`）硬编码，缺少说明。 | 阈值调整时需要改代码，且不同环境可能要求不同。 | 将阈值抽出到配置或命令行参数，并增加“与上月环比”的动态阈值。 | [scripts/data_quality_check.py](../../scripts/data_quality_check.py) |

---

### 3.3 功能与里程碑进度

#### 做得好的 / 当前无需改动

| 项 | 说明 | 关键文件 |
|----|------|---------|
| M0/M1/M2 已基本完成 | 脚手架、数据库、销量主链、AKShare 股价/指数/期货采集脚本、网络运行时均已落地。 | 项目整体 |
| 调度器任务配置已较完整 | `task_config.py` 中销量月链、城市月链、日链（股票/指数/期货）、财务数据均已注册，并配置了下游依赖。 | [scheduler/task_config.py](../../scheduler/task_config.py) |
| 自研前端未开始 | 项目定位是数据底座，Power BI 是目标消费端，当前不做自研前端符合阶段目标。 | [README.md](../../README.md)、[需求文档.md](需求文档.md) |
| Power BI 资产未开始 | 报表开发需要等数据层稳定，当前仅准备只读账号 DDL 是合理的。 | [db/system/01_create_powerbi_user.sql](../../db/system/01_create_powerbi_user.sql) |
| 手工维度支线独立 | 能源类型、ADS 车型匹配、地区、车型级别等手工维度通过独立脚本/SP 维护，与自动派生链解耦，避免被覆盖。 | [pipelines/dim/refresh_dim_energy_vehicle.py](../../pipelines/dim/refresh_dim_energy_vehicle.py)、[pipelines/dim/refresh_dim_model_ads.py](../../pipelines/dim/refresh_dim_model_ads.py) |

#### 需要优化的

| 优先级 | 问题 | 影响 | 建议 | 关键文件 |
|--------|------|------|------|---------|
| **P0** | 文档与代码状态偏差：README、执行大纲、warehouse-spec、需求文档反复强调“调度器未自动接 `build_sales_dimensions` / `agg_sales_monthly` / 股价/指数/期货采集”，但 `task_config.py` 已完整配置这些任务，且 `runner.py` 会遍历全部配置注册。 | 新人和维护者会被文档误导，认为大量功能缺失；实际代码已跑通大部分链路，测试和验收会被文档带偏。 | 立即同步文档：根据 `task_config.py` 的实际依赖链更新 README/执行大纲/warehouse-spec/需求文档中的 M3 状态；补充说明“哪些任务已注册但尚未在生产环境稳定运行”与“哪些真正未实现”的区别。 | [README.md](../../README.md)、[执行大纲.md](执行大纲.md)、[需求文档.md](需求文档.md)、[docs/warehouse-spec.md](../warehouse-spec.md)、[scheduler/task_config.py](../../scheduler/task_config.py) |
| P1 | M3 里程碑状态应更新为“基本完成”或“待生产验证”，但文档仍写“部分完成”。 | 里程碑状态表与真实代码脱节，影响项目节奏判断。 | 更新执行大纲中的里程碑状态表，区分“代码已落地”与“生产运行验证”两个状态列。 | [执行大纲.md](执行大纲.md) |
| P2 | 没有明确的“生产验证 checklist”。 | 代码落地不等于链路可稳定跑在生产环境。 | 在执行大纲或 README 中增加 M3 生产验证项：调度器连续运行 N 天、各任务成功/失败/告警统计、DWS 输出与预期对比。 | [执行大纲.md](执行大纲.md) |

---

### 3.4 文档治理

#### 做得好的 / 当前无需改动

| 项 | 说明 | 关键文件 |
|----|------|---------|
| 文档体系完整 | 有 README、需求文档、执行大纲、warehouse-spec、data-dictionary、api-spec、metric-definition、code-wiki 等，覆盖架构、数据、接口、指标、开发约定。 | [docs/](..) |
| 数据字典较详细 | 按维度/桥接/ODS/DWD/DWS 分层列出字段、类型、说明，便于分析师和开发者查字段。 | [docs/data-dictionary.md](../data-dictionary.md) |
| 文档反复强调“以代码现状为准” | 避免把未来规划写成当前能力，这是成熟的文档治理意识。 | 多个文档开头 |
| 命令示例具体到文件路径 | README 中给出可直接运行的命令和路径，便于 onboarding。 | [README.md](../../README.md) |

#### 需要优化的

| 优先级 | 问题 | 影响 | 建议 | 关键文件 |
|--------|------|------|------|---------|
| P1 | `docs/warehouse-spec.md` 中仍引用旧的 `db/ddl/01_create_tables.sql` 等路径，与实际 `db/<layer>/` 分层目录不一致。 | 按文档路径找不到文件，影响新人初始化。 | 全文替换旧 `db/ddl/` 引用为实际分层目录，如 `db/system/00_create_database.sql`、`db/ods/`、`db/dim/` 等。 | [docs/warehouse-spec.md](../warehouse-spec.md) |
| P1 | `.env.example` 第 3 行注释写 “由 `backend/app/config.py` 内部拼接”，实际路径是 `base_infra/config.py`。 | 小错误，但会让新人按错误路径找代码。 | 修正注释为 `base_infra/config.py`。 | [.env.example](../../.env.example) |
| P1 | `requirements.txt` 没有区分开发依赖与运行时依赖，且缺少 `pytest` 等测试工具。 | 新环境安装后无法直接运行测试；生产镜像可能安装不必要的开发包。 | 拆分为 `requirements.txt`（运行时）和 `requirements-dev.txt`（测试/lint），或改用 `pyproject.toml` 的 `[project.optional-dependencies]`。 | [requirements.txt](../../requirements.txt)、[pyproject.toml](../../pyproject.toml) |
| P2 | `pyproject.toml` 只声明了项目元数据和包发现，缺少开发工具配置。 | 无法统一团队的 lint、format、typecheck 行为。 | 增加 `[tool.ruff]`、`[tool.mypy]`、`[tool.pytest.ini_options]` 段落。 | [pyproject.toml](../../pyproject.toml) |
| P2 | 文档未说明“生产验证”与“代码落地”的区别。 | 容易把“代码写了”等同于“链路可稳定运行”。 | 在执行大纲增加“生产验证”章节，列出调度运行、告警、数据质量、DWS 输出对比等验收项。 | [执行大纲.md](执行大纲.md) |

---

## 4. 优先级行动清单

### P0（建议立即处理）

1. **修复失效测试**：`tests/test_sales_dimension_rules.py` 引用了 `pipelines/dim/build_sales_dimensions.py` 中已删除的函数，需要同步更新测试或恢复公共函数。
2. **同步文档与代码状态**：README、执行大纲、warehouse-spec、需求文档中关于“调度链未接 `build_sales_dimensions` / `agg_sales_monthly` / 股价/指数/期货”的描述已过时，需要按 `scheduler/task_config.py` 的实际配置重写。

### P1（建议近期处理）

3. **引入开发工具链**：在 `pyproject.toml` 配置 `ruff` + `mypy` + `pytest`，拆分 `requirements-dev.txt`，并修复现有 lint/type 问题。
4. **抽象采集脚本模板**：将股票/财务/指数/期货四个高度重复的采集脚本抽象为公共基类或模板，减少复制粘贴。
5. **抽象调度器 job 配置**：将 `load_stock_ods_job` / `load_index_ods_job` / `load_futures_ods_job` 中重复的 `_build_scheduler_config` 抽到公共模块。
6. **数据质量校验接入调度**：将 `scripts/data_quality_check.py` 拆分为轻量“调度后校验”任务并接入 `task_config.py`，同时扩展覆盖 DWD 各表。
7. **修正文档路径错误**：`docs/warehouse-spec.md` 中的旧 `db/ddl/` 路径、`.env.example` 中的旧 `backend/app/config.py` 路径都需要修正。
8. **调整日志目录默认路径**：避免默认 `logs/` 落在仓库根目录，或至少加入 `.gitignore` 并提示配置。

### P2（建议排期优化）

9. **增加 CI workflow**：GitHub Actions 运行 lint / typecheck / pytest（Mock 数据库）。
10. **增加 Makefile / 任务脚本**：封装常用命令，提升开发体验。
11. **统一异常分类体系**：避免各管道重复写字符串判断，复用 `network_runtime.classify_fetch_error` 的异常分类。
12. **明确财务采集调度策略**：`load_stock_finance` 当前每天运行，考虑改为低频或文档说明原因。
13. **数据质量阈值参数化**：将硬编码阈值抽出为配置或命令行参数，支持不同环境。
14. **增加生产验证 checklist**：区分“代码落地”与“生产稳定运行”两个状态。

---

## 5. 结论

`CarApp` 项目已经具备了一个**可运行的数据工程底座**，在模块化、调度框架、数据管道幂等性、网络采集健壮性方面都有不错的实现。当前**最紧迫的问题不是功能缺失，而是“文档与代码不同步”以及“测试已失效”**。如果能把 P0/P1 项处理掉，项目会从“能跑”进入“可维护、可协作、可验证”的状态，为后续 M4/M5 的 Power BI 报表开发打下稳定基础。

**一句话建议**：先修测试、同步文档，再补工具链和抽象重复代码，最后才是做报表。
