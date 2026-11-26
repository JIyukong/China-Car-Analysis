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

城市专用宽维表的字段、审核口径和管理员切换步骤见 [城市车型宽维表](city-model-wide.md)。
新增链路由 `ETL_CITY_WIDE_ENABLED` 控制，默认关闭；开发不会自动建表或回填历史。

先用 `--plan` 核对步骤。手动重放时，从已核对的同一个抓取快照复制 CSV 与 `manifest.json` 到该链爬虫的 `data/`。文件名分别为 `<chain>_YYYY-MM.csv` 和 `<chain>_YYYY-MM.manifest.json`。已通过来源验收的批次用 `--resume` 按原版本对账并重跑发布。

```powershell
python -m scheduler.runner --chain city --period 2026-07 --from-csv --plan
python -m scheduler.runner --chain city --period 2026-07 --from-csv
python -m scheduler.runner --chain style --period 2026-07 --resume
python -m scheduler.runner --chain daily --period 2026-07-03 --step load_index_daily
```

日线 `--period` 指定校验和写入的交易日。财务的 `--period` 是探测截止日，按实际验收的报告期记录结果。没有已验收的来源清单时，先进行一次完整采集。

## 飞书补录

### 已保留历史的对账恢复

`python -m scheduler.runner --refresh-city-tiers REPORT_JSON` 按第一财经 2025 年完整
337 城榜单增量更新 `city_tier`，未上榜地区标为“其他”。2026 年起第一财经不再发布分级，
因此仍使用最后公布的 2025 年分级；不把这一口径冒充 2026 年榜单。`city_level` 继续表示
行政区类型，不写入一线、二线等商业分级。正式库须已由管理员部署 `city_tier VARCHAR(50)`。

`python -m scheduler.runner --load-reviewed-county-areas REPORT_JSON` 从本地已审核的
`logs/etl/area-enrichment-20261125/county-areas-prepared.csv` 补录石河子、琼海。该凭据与
其他核验材料需要随运行目录备份，不提交业务产物到仓库。执行前要求管理员已将
`city_level` 枚举扩展为支持“县级市”；命令仅新增维度，不运行结构升级。完成后用
`--restore-city-history REPORT_JSON --period YYYY-MM` 恢复受影响月份。

`python -m scheduler.runner --seed-reviewed-ads-models REPORT_JSON` 仅按代码中已核验的
工信部生产身份清单补充 ADS 车型、品牌及其能源/车型分类。执行前验证保存的官方证据，
已有身份或分类冲突即回滚；保持默认主机厂品牌桥接及销量事实不变，不伪造首次销量月份。
随后运行 `python -m scheduler.runner --tick ads` 由权威 ADS 表更新匹配和拉链，最终以独立
回读结果确认匹配，不能把因锁超时跳过的 tick 退出码当作已完成。

`python -m scheduler.runner --restore-national-history REPORT_JSON` 按已有全国 ODS
逐键核对保留的 DWD 后，恢复全部月份的公司、品牌 DWS 当前分组。缺失车型只有在
已有主机厂及唯一品牌桥接可确定时才新增；历史未知公司保留在 OTHER 分组，并在报告
单列数量和销量。任一业务键或销量不一致会回滚，不将历史未知归属推断成当前归属。

`python -m scheduler.runner --repair-futures-history EVIDENCE_JSON` 使用已保存的上期所
原始日行情及日历证据修复已核实异常。写入前核对原 OHLC、成交量、持仓量，写入后在
事务中回读；原值、修正值及来源保存在本地修正档案。该命令不推断没有官方证据的日历。

`python -m scheduler.runner --restore-city-history REPORT_JSON` 将保留的城市 ODS 历史
按月对账后发布到独立城市明细表，检查已有明细及回读结果的业务键、销量。旧城市表保留。
报告明确标记来源为已保留 ODS；该操作不接受新版网站榜单，也不修改来源验收状态。
网站改名、撤回或重复业务键仍需单独核实，不能把数仓恢复报告当作外部来源完整性证明。

`python -m scheduler.runner --seed-reviewed-ads-models REPORT_JSON` 仅按已核验的工信部
生产企业、商标及车型证据增量建立指定 ADS 车型。明确的车型品牌可以与生产企业的默认
销量品牌不同；不修改主机厂默认桥接，不创建销量记录，不填写没有销量依据的销售月份。
已有身份冲突时回滚；完成后再用 `--tick ads` 同步权威 ADS 记录。车型品牌一致性检查仅对
已核验的精确车型、品牌、主机厂组合允许例外，其他错配仍会报错。

月链定时检查优先处理最新应到月份，再处理历史积压。城市来源 HTTP 429 或服务器
错误最多重试三次；所有分页与业务键验收仍然必须通过。无变化的 DWD 明细不重复更新。

在飞书建一张限定维护人员可编辑的多维表格，使用普通文本字段 `任务键`、`维度类型`、`业务键`、`月份`、`缺失信息`、`补录数据`、`处理结果`，以及包含「待补录」「已提交」「校验退回」「已应用」的单选字段 `状态`。给自建应用开通并发布多维表格读写权限，把应用加入该表格。在服务的 `.env` 设置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_TOKEN`、`FEISHU_TABLE_ID`、`FEISHU_TABLE_URL` 和 `ALERT_WEBHOOK_URL`，并保持 `ALERT_WEBHOOK_TYPE=feishu`。群机器人可使用关键词 `CarApp调度器`。

使用飞书 CLI 部署时，设置 `FEISHU_CLI_PATH` 为可执行文件的绝对路径，补录通过 CLI 的应用身份调用 Base v3。常驻任务需以持有该 CLI 配置及系统密钥的 Windows 用户运行。应用机器人加入通知群后，用 `FEISHU_ALERT_CHAT_ID` 指定群 ID。启用群 ID 后，群消息发送失败会记录失败并按原策略重试。

收到通知后，打开链接并填写「补录数据」中预填的 JSON 字段，再把状态改为「已提交」。系统回填结果，受影响的已验收批次自动续跑。主机厂归属使用已存在的 `company_code` 和 `brand_code`。存在归属冲突、城市车型无法唯一定位或原始级别为空时，按回执核对来源和主数据后续跑。能源缺项继续提醒，全国销量按自身依赖发布。

能源和地区 CSV 可分别用 `pipelines.dim.refresh_dim_energy_vehicle` 和 `pipelines.dim.refresh_dim_area` 专人维护，完成后按月份 `--resume`。

ADS 单独维护。在同一个飞书 Base 中配置独立数据表 `dim_model_ads_manual`，设置 `FEISHU_ADS_TABLE_ID` 并给调度应用读取权限。五列均为文本：`model_name`（唯一车型名）、`ads_proportion`（如 `95%`）、`ads_supplier`、`ads_supplier_mode`、`listed_at`（如 `2026-07-01` 或 `2026年7月`）。调度每五分钟增量同步到 `dim_model_ads_manual`；已匹配车型同步生成 `dim_model_ads_zipper`，供应商或模式变化时按上市日期开启新版本。尚未建档的车型先进入权威表。源表缺行不会删除数据库记录。

手动同步 ADS：`python -m scheduler.runner --tick ads`。切换到飞书后停止使用 ADS CSV 导入及数据库旧 ADS 事件，避免重复写入。DWD 不再读取或写入 ADS 字段；正式库旧列及历史值保留，结构由管理员另行维护。查询历史方案时，按 `model_id` 和所需时点关联拉链表。

主机厂工作簿 `汽车品牌主机厂信息表.xlsx` 放在本地仓库根目录。使用 `python -m pipelines.dim.seed_dim_from_excel` 更新已有品牌关系时会保留其生效日期。工作簿没有品牌变更的生效日期，遇到品牌变更会停止导入，待确认日期后单独维护关系。

## 切换顺序

1. 用独立测试库和测试飞书表格验证一次完整入库、一次失败回执和一次补录续跑。部署主机安装运行依赖及 Playwright Chromium。
2. 由管理员在维护会话部署 [006 当前发布视图](../db/migrations/006_monthly_publication_current_groups.sql) 和 [008 多市场日历](../db/migrations/008_create_market_session_tables.sql)，确认表的存储引擎和现有存储过程权限，并补齐交易日历。先在 ODS 核对行情，确认后用 `ETL_STOCK_DWD_ENABLED=true` 启用日线及财报 DWD。
3. 核对同库旧的写入调度和 ADS 事件，停用重复写入，再启动常驻任务。Windows 使用飞书 CLI 时，可在任务计划程序以完成 CLI 授权的用户运行 `python -m scheduler.runner`，设为用户登录时启动、失败重试和禁止重复实例，并将工作目录设为项目根目录。当前任务名为 `CarApp ETL Scheduler`，锁屏后仍运行；重启后需要该 Windows 用户登录。常驻服务与手工补跑使用同一个项目目录。部署在其他主机时，把生成的 `logs/etl/status.json` 同步给 Codex 所在项目。
4. 按月验收 BI 所需存量后，将 Power BI 指向 `vw_dws_sales_monthly_company_current` 和 `vw_dws_sales_monthly_brand_current`。已有历史分组保留在基表，通过验收的分组才进入当前视图。Power BI 需要这两个视图及其基表的只读权限。

## MySQL 账号

## 股票日线历史对账与后复权

股票日线仅包含 A/H 股。每日 23:30 的任务独立读取 2017-01-01 至业务日的全历史业务键、上市/退市日期、交易日历及停牌登记；本地任务状态不决定完整性。行情采用 AKShare 免费新浪单源，取消双源门槛。每日检查最近 5 个实际交易日、缺口、证据不符记录及因子变化影响的日期；周日刷新原始行情并复核历史数值。

```powershell
python -m scheduler.runner --audit-stock-history
python -m scheduler.runner --rebase-stock-history --adjust hfq --start-date 2017-01-01
```

初始化或审计可追加 `--end-date YYYY-MM-DD`，明确截至已完成的业务日；拒绝未来日期。来源尚未发布当天行情时保留待采，不能用上一日行情补齐当天。完成到某个业务日不代表当天及以后已经完整。

审计只读正式库，将明细写入 `logs/etl/stock_history/audit.json`；非零退出表示尚未通过验收，并不表示无数据。上市日缺失时不能计算完整应采集合。首次切换命令保存原价、显式因子及逐日证据；全部验收通过后，在行情锁及数仓锁内以同一 MySQL 事务更新 ODS/DWD 并回读。任一股票缺少原价、有效因子、交易日历或上市日期时不会部分切换口径。业务起点固定为 2017-01-01，但因子基准保持实际上市基准，不能在 2017 年重新归一化。

A/H 均固定新浪后复权口径 `sina_hfq_v1`。复用 AKShare 的原始行情解码和因子接口，避开价格前填、按行情值去重和因子失败后返回原价。A 股 OHLC=原价×该日有效因子；港股再加有效现金调整项，使用 Decimal 并保留 4 位小数。成交量为真实股数，成交额为真实币种金额，不随价格复权；可选字段缺失时新行留空、已有值保留。涨跌幅按上一实际交易日的同口径后复权收盘价计算。

`logs/etl/stock_history/cache/<股票>/raw.json` 保存带校验和的原价缓存，`factors/<摘要>.json` 保存因子版本，`runs/` 保存 HTTP 原文及逐日验收证据。`prepared.json` 保存事务准备证据；`active.json` 保存已提交的逐行摘要、原价摘要、因子版本和因子快照。只有数据库提交成功后才接受新因子版本；提交后进程中断时，通过 ODS/DWD 回读恢复。缓存损坏重新抓取，不能直接信任；缺响应不删除已有原价。上述本地文件需要备份。

新增分红/送转时比较有效日期上的因子及现金项，仅重算该股票发生变化的区间；历史因子修改或删除也按区间重算。重算使用本地原价，只有缺原价、新业务日或周日历史复核才刷新原始行情。部分新浪接口会整段返回，但不会因此重写全库。正式库仅新增或更新价格、量额或映射确实变化的业务键。输出包含 `factor_affected`、`raw_changed`、`raw_fetches`、新增和修正数。固定 AKShare 已验证版本，升级前先核对解码、字段和复权行为。

已审核参考数据仍由同一入口增量导入：

```powershell
python -m scheduler.runner --stock-repair-evidence reviewed.json
```

JSON 可包含 `listings`（`code, listed_date, delisted_date?, source_ref`）、`calendar`（`market: A/HK, date, is_open: 0/1, source_ref`）、`suspensions`（`code, date, source_ref`）。每条参考证据必须有已人工核实的 HTTPS 来源链接。上市日期与既有值冲突时停止；缺日历行不能据此填为休市，停牌必须有明确证据。仅对当前已有的 A/H 股票补充参考数据，不创建正式表。`restore_amount` 仅暂存恢复证据，不再直接更新正式 ODS 或绕过双源验收。

原有 `--repair-stock-dates CSV` 也进入同一全历史审计和原价/因子验收流程；已确认停牌/休市日期排除，未完成后复权切换时提示 `waiting_rebase`，不能绕过切换门槛。不要直接调用旧采集器写正式库。

既有 DWD 停牌快照默认阻止验收。核实官方停牌证据后，可在对应 `suspensions` 项明确设置 `quarantine_existing_dwd: true`：仅允许将零成交且 OHLC 平价的既有记录标为 `is_valid_ohlc=0`，不删除、不改变原价格；非快照或回读失败时整笔回滚。审计将这些行列入 `retained_invalid`，有效行情发布与回读不计入这些保留的异常行。有效行情查询须筛选 `is_valid_ohlc=1`。

需要使用其他已有网络出口时，可在 `.env` 配置 `STOCK_MARKET_PROXY_URL=http://127.0.0.1:端口`。该配置仅作用于股票历史采集器的东财请求，不修改系统代理、Clash 规则或其他应用；程序也不会启动或改写代理服务。配置的本机 HTTP 代理不可用时停止并报告原因。注意现有代理中的东财直连规则可能使“通过代理”和“直连”使用同一出口，必须以真实行情 GET 响应持续可用为准，不能只凭节点延迟检查判定可用。

## MySQL 账号配置

给 ETL 使用新建的专用 MySQL 账号，通过 `.env` 的 `DB_USER` 和 `DB_PASSWORD` 配置。由管理员在独立会话中创建和授予权限，管理员凭据放在项目及智能体可访问的环境之外。

以下以已经部署存储过程的 `dw_auto_stock` 为例。账号的主机范围用实际 ETL 主机或网段替换，密码使用独立生成的值。示例用于配置权限，普通 ETL 启动不执行它。

```sql
CREATE USER 'carapp_etl'@'<etl-host>' IDENTIFIED BY '<generated-password>';
GRANT SELECT, INSERT, UPDATE, CREATE TEMPORARY TABLES
    ON `dw_auto_stock`.* TO 'carapp_etl'@'<etl-host>';
-- 仅授予应用代码实际 CALL 的 01~04 号 ETL 存储过程
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_build_sales_dimensions TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_clean_sales_dwd TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_update_lifecycle TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_agg_sales_monthly TO 'carapp_etl'@'<etl-host>';
SHOW GRANTS FOR 'carapp_etl'@'<etl-host>';
```

> 注意：`sp_refresh_dim_model_ads_*`（`db/procedures/05~07`）及配套事件
> （`db/events/01`）是早期纯 SQL ADS 刷新管线的遗留对象，应用代码已改用
> `pipelines/dim/refresh_dim_model_ads.py`，全仓不再 CALL，因此**不要**给 ETL
> 账号授予这些过程的 EXECUTE 权限；待 DBA 确认线上无依赖后再下线。

同时核对账号没有通过角色、全局授权或其他授权路径取得 `DROP`、`ALTER`、`DELETE`、永久建表或管理权限。MySQL 的 `TRUNCATE TABLE` 需要 `DROP` 权限。上线前核对已部署过程的定义和安全上下文，特别是以定义者权限执行的例程，以及触发器。保留 Power BI 只读账号。涉及数据库维护时，由管理员先确认可恢复备份。

运行账号可做正常更新，更新范围仍需由业务 SQL 控制。验证代码使用离线预览、模拟连接或独立测试库。正式库的结构升级由管理员单独处理。

### 直连独立来源与字段证据

腾讯、新浪和同花顺行情请求直连；东财不可用时仍须满足双源验收，不降低阈值。A 股增加同花顺公开年度 `02` 后复权接口，并验证响应分支；港股腾讯使用专用 `hkfqkline` 后复权接口。统一价格口径仍为东财/腾讯口径。接口仅返回原价分支时，必须有覆盖全部请求日期的明确因子证据证明乘数为 1、现金调整为 0，才可认定价格口径等价。

价格与成交字段分别记录独立供应商证据。新浪复投价格与统一口径不一致时，不参与价格验收，其真实成交量、成交额仍可独立核对。腾讯 A 股接口部分日期将成交量舍入到整手，不能证明精确股数，因此不用于 A 股成交量验收。成交量仍要求两个提供精确股数的来源完全一致；成交额仍遵循原容差。任何字段冲突继续阻止该记录写入。原始响应、价格供应商及逐字段供应商均保留在本地验收证据中。同花顺 v6/v4 仅是同一供应商的重试接口，不计作两个独立来源。
