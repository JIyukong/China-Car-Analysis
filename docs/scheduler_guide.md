# 调度和 ETL

## 启动与定时任务

从仓库根目录执行 `python -m scheduler.runner --plan` 离线查看计划。完成下文部署后，用 `python -m scheduler.runner` 在常开主机上运行一个 APScheduler 服务。下表使用北京时间。

| 链 | 触发时间 | 步骤 |
| --- | --- | --- |
| 车型销量 `style` | 每日 03:00 | 16888 来源验收 → ODS → 车型维度、全国 DWD 和 DWS 发布 |
| 城市销量 `city` | 每日 03:30 | 16888 来源验收 → ODS → 城市 DWD 发布 |
| 行情 `daily` | 每日 17:30 股票和期货、17:45 指数、18:30 财务 | 按市场和交易日对账。财务按报告期验收 |
| 飞书与续跑 | 每 5 分钟 | 读取已提交记录，校验并更新维度，恢复已验收批次 |
| Codex 巡检 | 每日 09:00 | 运行 `python -X utf8 -m scheduler.runner --status`，汇报到货、缺项和发布状态 |

月链每日检查上月和未完成的月份。16888 没有可靠的版本号，因此每日重新核对上月完整榜单，以发现总行数相同的修订。来源延迟、漏页、重复业务键和已入 ODS 的业务键在新版榜单消失都会记录具体状态。原始抓取及验收清单位于对应爬虫的 `data/snapshots/`。来源内容不变且已发布时跳过写入。

服务启动时检查错过的任务。可恢复的批次、缺维和飞书处理记录存放在 `logs/etl/state.sqlite3`，切换主机或升级时随项目持久化保存。日线首次启动从最近七个自然日检查起，之后会补查停机期间和已经记录的缺口。SSE 沿用 `dim_trade_calendar`。港股和期货按交易所使用 `etl_market_calendar`，开市和休市均需有记录。已确认停牌写入 `etl_stock_suspension` 并附公告来源。空响应只说明没有拿到数据。A 股财报从已观察到的报告中核对业务键和标的，已知报告期的遗漏会保留为错误。

## 手动补跑

先用 `--plan` 核对步骤。手动重放时，从已核对的同一个抓取快照复制 CSV 与 `manifest.json` 到该链爬虫的 `data/`。文件名分别为 `<chain>_YYYY-MM.csv` 和 `<chain>_YYYY-MM.manifest.json`。已通过来源验收的批次用 `--resume` 按原版本对账并重跑发布。

```powershell
python -m scheduler.runner --chain city --period 2026-07 --from-csv --plan
python -m scheduler.runner --chain city --period 2026-07 --from-csv
python -m scheduler.runner --chain style --period 2026-07 --resume
python -m scheduler.runner --chain daily --period 2026-07-03 --step load_index_daily
```

日线 `--period` 指定校验和写入的交易日。财务的 `--period` 是探测截止日，按实际验收的报告期记录结果。没有已验收的来源清单时，先进行一次完整采集。

## 飞书补录

在飞书建一张限定维护人员可编辑的多维表格，使用普通文本字段 `任务键`、`维度类型`、`业务键`、`月份`、`缺失信息`、`补录数据`、`处理结果`，以及包含「待补录」「已提交」「校验退回」「已应用」的单选字段 `状态`。给自建应用开通并发布多维表格读写权限，把应用加入该表格。在服务的 `.env` 设置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_TOKEN`、`FEISHU_TABLE_ID`、`FEISHU_TABLE_URL` 和 `ALERT_WEBHOOK_URL`，并保持 `ALERT_WEBHOOK_TYPE=feishu`。群机器人可使用关键词 `CarApp调度器`。

使用飞书 CLI 部署时，设置 `FEISHU_CLI_PATH` 为可执行文件的绝对路径，补录通过 CLI 的应用身份调用 Base v3。常驻任务需以持有该 CLI 配置及系统密钥的 Windows 用户运行。应用机器人加入通知群后，用 `FEISHU_ALERT_CHAT_ID` 指定群 ID。启用群 ID 后，群消息发送失败会记录失败并按原策略重试。

收到通知后，打开链接并填写「补录数据」中预填的 JSON 字段，再把状态改为「已提交」。系统回填结果，受影响的已验收批次自动续跑。ADS 确认不适用时，填写 `{"not_applicable":true}`。主机厂归属使用已存在的 `company_code` 和 `brand_code`。存在归属冲突、城市车型无法唯一定位或原始级别为空时，按回执核对来源和主数据后续跑。能源和智驾缺项继续提醒，全国销量按自身依赖发布。

原有手工维度 CSV 可用于专人维护。使用现有 `pipelines.dim.refresh_dim_energy_vehicle`、`refresh_dim_model_ads`、`refresh_dim_area` 命令，完成后按月份 `--resume`。

主机厂工作簿 `汽车品牌主机厂信息表.xlsx` 放在本地仓库根目录。使用 `python -m pipelines.dim.seed_dim_from_excel` 更新已有品牌关系时会保留其生效日期。工作簿没有品牌变更的生效日期，遇到品牌变更会停止导入，待确认日期后单独维护关系。

## 切换顺序

1. 用独立测试库和测试飞书表格验证一次完整入库、一次失败回执和一次补录续跑。部署主机安装运行依赖及 Playwright Chromium。
2. 由管理员在维护会话部署 [006 当前发布视图](../db/migrations/006_monthly_publication_current_groups.sql) 和 [008 多市场日历](../db/migrations/008_create_market_session_tables.sql)，确认表的存储引擎和现有存储过程权限，并补齐交易日历。先在 ODS 核对行情，确认后用 `ETL_STOCK_DWD_ENABLED=true` 启用日线及财报 DWD。
3. 核对同库旧的写入调度和 ADS 事件，停用重复写入，再启动常驻任务。Windows 使用飞书 CLI 时，可在任务计划程序以完成 CLI 授权的用户运行 `python -m scheduler.runner`，设为用户登录时启动、失败重试和禁止重复实例，并将工作目录设为项目根目录。当前任务名为 `CarApp ETL Scheduler`，锁屏后仍运行；重启后需要该 Windows 用户登录。常驻服务与手工补跑使用同一个项目目录。部署在其他主机时，把生成的 `logs/etl/status.json` 同步给 Codex 所在项目。
4. 按月验收 BI 所需存量后，将 Power BI 指向 `vw_dws_sales_monthly_company_current` 和 `vw_dws_sales_monthly_brand_current`。已有历史分组保留在基表，通过验收的分组才进入当前视图。Power BI 需要这两个视图及其基表的只读权限。

## MySQL 账号

给 ETL 使用新建的专用 MySQL 账号，通过 `.env` 的 `DB_USER` 和 `DB_PASSWORD` 配置。由管理员在独立会话中创建和授予权限，管理员凭据放在项目及智能体可访问的环境之外。

以下以已经部署存储过程的 `dw_auto_stock` 为例。账号的主机范围用实际 ETL 主机或网段替换，密码使用独立生成的值。示例用于配置权限，普通 ETL 启动不执行它。

```sql
CREATE USER 'carapp_etl'@'<etl-host>' IDENTIFIED BY '<generated-password>';
GRANT SELECT, INSERT, UPDATE, CREATE TEMPORARY TABLES
    ON `dw_auto_stock`.* TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_refresh_dim_model_ads_all TO 'carapp_etl'@'<etl-host>';
SHOW GRANTS FOR 'carapp_etl'@'<etl-host>';
```

同时核对账号没有通过角色、全局授权或其他授权路径取得 `DROP`、`ALTER`、`DELETE`、永久建表或管理权限。MySQL 的 `TRUNCATE TABLE` 需要 `DROP` 权限。上线前核对已部署过程的定义和安全上下文，特别是以定义者权限执行的例程，以及触发器。保留 Power BI 只读账号。涉及数据库维护时，由管理员先确认可恢复备份。

运行账号可做正常更新，更新范围仍需由业务 SQL 控制。验证代码使用离线预览、模拟连接或独立测试库。正式库的结构升级由管理员单独处理。
