# 中国乘用车投资分析平台

> 当前口径：以仓库已落地代码为准，不把未来规划写成当前能力。
> 当前主线：16888 自抓销量文件 -> 云 MySQL 数仓 -> Power BI 消费。
> 当前真实状态：M0/M1/M2 已完成，M3 基本完成（调度链路已全量注册，待生产环境验证），M4-M6 未开始。

## 项目概览

这是一个面向汽车投资分析的数据项目，当前重点不是自研前端，而是先把数据主链跑通：

- 销量数据：来自 `16888` 已落地的 `style` / `city` CSV、JSON 文件
- 数仓链路：`ODS -> DIM -> DWD -> DWS`
- 手工维度支线：
  - `dim_energy_vehicle`
  - `dim_model_ads_manual -> dim_model_ads_zipper`
- 服务层：FastAPI 健康检查 + APScheduler 定时调度
- 可视化目标：后续由 Power BI 读取云 MySQL

当前调度链路已全量注册（销量月链、城市月链、股价/指数/期货日链、财务日链），但仍需在生产环境连续运行验证稳定性。尚未落地的部分集中在 Power BI 报表资产（M4-M6）。

## 建议阅读顺序

1. 本文 `README.md`：先看怎么初始化、怎么增量跑、哪里是断点
2. `执行大纲.md`：再看当前做到哪一里程碑、后面缺什么
3. `需求文档.md`：最后看产品定位、技术边界和后续路线
4. `docs/warehouse-spec.md`：补充数仓分层与数据流转口径
5. `docs/data-dictionary.md`：查字段定义

## 目录结构

```text
CarApp/
├── base_infra/                    # 配置、数据库连接、日志
├── backend/                       # FastAPI 健康检查入口
├── pipelines/
│   ├── ingest/                    # 数据采集 / ODS 入库
│   ├── dim/                       # 维度种子、维度派生、手工维度刷新
│   ├── staging/                   # DWD 清洗
│   └── serve/                     # DWS 聚合
├── scheduler/                     # APScheduler 定时任务
├── scripts/                       # 初始化脚本
├── db/
│   ├── system/                    # 建库 / Power BI 只读账号
│   ├── ods/                       # ODS 建表
│   ├── dim/                       # 维度 / 桥接 / 拉链表建表
│   ├── dwd/                       # DWD 建表
│   ├── dws/                       # DWS 建表
│   ├── ads/                       # ADS 视图
│   ├── migrations/                # 结构补丁
│   ├── triggers/                  # 触发器
│   ├── procedures/                # 存储过程
│   ├── events/                    # 事件
│   ├── scheduler/                 # 调度器元数据表
│   └── manual/                    # 手工维度刷新 SQL
├── data/manual_dimensions/        # 手工维护的 CSV 维度源
├── docs/                          # 辅助文档
├── tests/                         # 测试
├── requirements.txt               # Python 依赖
├── pyproject.toml                 # 项目元数据
├── .env.example                   # 环境变量模板
├── 执行大纲.md
├── 需求文档.md
└── README.md
```

## 数仓分层与命名

| 前缀 | 类型 | 说明 |
|------|------|------|
| `dim_` | 维度层 | 上市公司、主机厂、品牌、车型、日期、股票等主数据 |
| `bridge_` | 桥接层 | 多对多关系，如品牌-主机厂、公司-股票 |
| `ods_` | 贴源层 | 保留原始采集字段与溯源信息 |
| `dwd_` | 明细层 | 标准化、去重、补充维度 ID |
| `dws_` | 汇总层 | 按月份按主体聚合 |
| `ads_` | 应用层 | 面向下游消费的应用表或视图 |
| `tmp_` | 临时层 | 中间计算结果 |

当前真实落地情况：

- 已落地主表：`dim_*`、`bridge_*`、`ods_*`（含 `ods_stock_daily`、`ods_stock_finance`、`ods_index_daily`、`ods_futures_daily`）
- 已落地 DWD 表：`dwd_sales_monthly_model`、`dwd_sales_monthly_model_city`、`dwd_stock_daily`、`dwd_stock_finance`
- 已落地 DWS 表：`dws_sales_monthly_company`、`dws_sales_monthly_brand`
- 已落地手工 ADS 支线表：`dim_model_ads_manual`、`dim_model_ads_zipper`
- 已落地采集脚本：AKShare A 股/H 股日线、A 股财务、同花顺行业指数、商品期货主力连续
- 已落地调度链路：销量 style 月链、销量 city 月链、股价/指数/期货日链、财务日链（均已在 `task_config.py` 注册）

## 系统主链

```text
16888 style/city 文件
    -> ODS 入库
    -> DIM 种子与 dim_model 派生
    -> DWD 清洗
    -> DWS 聚合
    -> Power BI 消费
```

对应代码入口：

- ODS 入库：
  - `pipelines/ingest/sales/load_16888_style_to_ods.py`
  - `pipelines/ingest/sales/load_16888_city_to_ods.py`
- 维度派生：`pipelines/dim/build_sales_dimensions.py`
- DWD 清洗：`pipelines/staging/clean_sales_dwd.py`
- DWS 聚合：`pipelines/serve/agg_sales_monthly.py`

## DDL 使用顺序

### 1. 新库初始化推荐顺序

SQL 已按数仓分层拆分到 `db/<layer>/` 目录，推荐直接用初始化脚本自动按序执行：

```bash
python scripts/init_database.py
```

脚本按以下目录顺序自动发现并执行（`system/01_create_powerbi_user.sql` 权限脚本需 DBA 手工执行）：

```text
system → ods → dim → dwd → dws → ads → scheduler
       → migrations（结构补丁）→ triggers → procedures → events
```

如需手工执行，按上述目录顺序、目录内按文件名编号依次执行即可。关键对象：
- 维度核心表：`db/dim/01_create_dim_core.sql`
- ADS 拉链表：`db/dim/03_create_dim_zipper.sql`
- DWD/DWS：`db/dwd/`、`db/dws/`
- ETL 存储过程：`db/procedures/`
- Power BI 只读账号（按需手工执行）：`db/system/01_create_powerbi_user.sql`

### 2. 推荐执行命令

```bash
# 一键初始化（DDL + 维度种子 + ODS + 车型维度构建）
python scripts/init_database.py

# 表已存在时只跑种子/ODS
python scripts/init_database.py --skip-ddl

# 单个 SQL 文件执行
python scripts/run_sql_file.py db/migrations/003_alter_dwd_sales_monthly_model.sql
```

## 首次初始化使用顺序

### 1. 准备环境

- Python `3.10+`
- 云 MySQL `8.0+`
- 已将当前本机 IP 加入数据库白名单
- 已准备写账号和 root 或高权限账号

### 2. 安装依赖

```bash
pip install -r requirements.txt
pip install -e .
```

### 3. 配置环境变量

```bash
copy .env.example .env
```

填写以下核心配置：

- `DB_HOST`
- `DB_PORT`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `POWERBI_READONLY_USER`
- `POWERBI_READONLY_PASSWORD`

说明：数据库连接串由 `base_infra/config.py` 内部拼装，无需手写 `DATABASE_URL`。

### 4. 执行 Python 初始化主链

对于已完成建库建表的环境，推荐执行：

```bash
python scripts/init_database.py --skip-ddl --with-dwd
```

该脚本当前真实顺序如下：

```text
scripts/init_database.py
    -> seed_dim_date
    -> seed_dim_company_stock
    -> seed_dim_oem_brand
    -> seed_dim_from_excel
    -> load_16888_style_to_ods
    -> load_16888_city_to_ods
    -> build_sales_dimensions
    -> clean_sales_dwd              # 仅 --with-dwd
    -> update_lifecycle             # 仅 --with-dwd
```

### 5. 启动后端健康检查

```bash
uvicorn backend.app.main:app --reload
```

验证地址：

- `http://localhost:8000/`
- `http://localhost:8000/health`

期望健康检查返回：

```json
{"status": "ok", "database": "connected"}
```

## 调度运行顺序

当前自动调度链路由 `scheduler/runner.py` 遍历 `scheduler/task_config.py` 注册，包含三条独立链路：

### style 月链（车型月销量，每月 12~15 号探测）

```text
每月 12-15 号 03:00
    -> crawl_16888_style_monthly        # 窗口内每日探测，未发布则 SkipTask
       -> load_style_ods                # ODS 入库
          -> build_sales_dimensions     # dim_model 派生
             -> refresh_ads_zipper      # ADS 拉链刷新
                -> run_dwd_clean        # DWD 清洗
                   -> update_lifecycle  # 生命周期更新（并行）
                   -> run_dws_agg       # DWS 聚合（并行）
```

### city 月链（城市车型月销量，每月 20~25 号探测）

```text
每月 20-25 号 03:30
    -> crawl_16888_city_monthly         # 窗口内每日探测
       -> load_city_ods                 # ODS 入库
          -> run_dwd_city_clean         # DWD 城市清洗
```

### 日链（交易日 17:30 起）

```text
mon-fri 17:30  -> load_stock_daily      # A 股/H 股日线采集（SSE 交易日守卫）
mon-fri 17:30  -> load_futures_daily    # 商品期货主力连续采集（SSE 交易日守卫）
mon-fri 17:45  -> load_index_daily      # 同花顺行业指数采集（SSE 交易日守卫）
mon-fri 18:30  -> load_stock_finance    # A 股财务采集（SSE 交易日守卫）
```

## 手工维度刷新顺序

### 1. 能源类型 / 车型类型支线

数据源：

- `data/manual_dimensions/dim_energy_vehicle.csv`

执行入口：

```bash
python -m pipelines.dim.refresh_dim_energy_vehicle
```

流程：

```text
dim_energy_vehicle.csv
    -> stg_dim_energy_vehicle
    -> db/manual/refresh_dim_energy_vehicle.sql
    -> dim_energy_vehicle
    -> 触发器同步 dim_model.energy_type / vehicle_type
```

### 2. ADS 手工权威存储支线

数据源：

- `data/manual_dimensions/dim_model_ads_matched.csv`

执行入口：

```bash
python -m pipelines.dim.refresh_dim_model_ads
```

流程：

```text
dim_model_ads_matched.csv
    -> stg_dim_model_ads_manual
    -> db/manual/refresh_dim_model_ads_manual.sql
    -> dim_model_ads_manual
    -> sp_refresh_dim_model_ads_all()
    -> dim_model_ads_zipper
    -> 回填 dim_model.ads_id / ads_supplier_name
```

注意：

- `dim_model_ads_manual` 是权威存储表，可包含尚未在 `dim_model` 中出现的车型
- `dim_model_ads_zipper` 只接收当前已存在于 `dim_model` 的车型

## 当前进度与断点

| 模块 | 当前状态 | 说明 |
|------|----------|------|
| M0 脚手架与数据库 | 已完成 | 基础包、主 DDL、健康检查已落地 |
| M1 销量数据主链 | 已完成 | 16888 ODS、DIM 派生、DWD、DWS 已实现 |
| M2 股价采集 | 已完成 | A 股/H 股日线、A 股财务、同花顺指数、商品期货采集脚本均已落地 |
| M3 调度服务 | 基本完成 | 调度链路已全量注册（销量月链、城市月链、日链、财务），待生产环境连续运行验证 |
| M4 Power BI 看板 | 未开始 | 仅只读账号 DDL 已有 |
| M5 Power BI 关联分析 | 未开始 | 未见报表文件 |
| M6 Publish to Web | 未开始 | 未见发布资产 |

## 常用命令

```bash
# 首次初始化（DDL 已手工执行完成）
python scripts/init_database.py --skip-ddl --with-dwd

# 仅启动健康检查服务
uvicorn backend.app.main:app --reload

# 启动调度器
python -m scheduler.runner

# 手工刷新能源 / 车型类型字典
python -m pipelines.dim.refresh_dim_energy_vehicle

# 手工刷新 ADS 支线
python -m pipelines.dim.refresh_dim_model_ads

# 手工执行 DWS 聚合
python -m pipelines.serve.agg_sales_monthly
```

## 只读账号验证

使用 Power BI 只读账号连接数据库后，以下行为应符合预期：

- `SELECT * FROM dw_auto_stock.dim_company LIMIT 1;` 成功
- `INSERT INTO dw_auto_stock.dim_company(...) VALUES(...);` 被拒绝

## 参考文档

- `执行大纲.md`
- `需求文档.md`
- `docs/warehouse-spec.md`
- `docs/data-dictionary.md`
