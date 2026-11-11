# 调度和 ETL

## 启动与定时任务

从仓库根目录运行。先执行 `python -m scheduler.runner --plan` 离线查看计划，配置好专用 ETL 账号后用 `python -m scheduler.runner` 启动定时调度。下表使用北京时间，任务声明见 [task_config.py](../scheduler/task_config.py)。

| 链 | 触发时间 | 步骤 |
| --- | --- | --- |
| 车型销量 `style` | 每月 12–15 日 03:00 | 目标月抓取 → ODS → 车型维度 → ADS → DWD → 生命周期 → DWS |
| 城市销量 `city` | 每月 20–25 日 03:30 | 目标月抓取 → ODS → 城市 DWD |
| 行情 `daily` | 工作日 17:30 股票和期货、17:45 指数、18:30 财务 | 四项独立任务，运行前检查交易日历 |

月链默认处理上一个自然月，前置步骤失败会停止后续步骤。目标月 ODS 达到就绪阈值时，跳过抓取和 ODS 入库，从后续步骤继续。源数据未就绪时，在探测窗口内等下一次触发，窗口结束后可手工补跑。城市 DWD 需要已有车型维度，因此城市链跳过时也应检查该月的车型链。

定时任务由 APScheduler 在内存注册。任务结果记录在进程日志中，配置 Webhook 时定时任务失败会告警。停机期间的错过任务用手动命令补跑。已有的调度日志、心跳和任务存储表仍可作为历史记录。升级时停用旧的数据库心跳探活，改看 NSSM 或 systemd 服务状态以及应用日志。建议同一环境只运行一个调度器，手工补跑避开同一批数据的定时处理。

## 手动补跑

先用 `--plan` 核对月份和步骤。指定 `--from-csv` 会跳过爬取，从入库开始。加载器分别从 [车型爬虫目录](../pipelines/ingest/sales/crawl_16888_style/) 和 [城市爬虫目录](../pipelines/ingest/sales/crawl_16888_city/) 下的 `data/` 读取 `style_YYYY-MM.csv` 和 `city_YYYY-MM.csv`。需要重新探测源数据时可省略 `--from-csv`，此时仍会检查 ODS 是否已就绪。

```powershell
python -m scheduler.runner --chain city --period 2026-07 --from-csv --plan
python -m scheduler.runner --chain city --period 2026-07 --from-csv
python -m scheduler.runner --chain daily --step load_index_daily
```

行情 `--period` 用于交易日判断和日志标签。各行情加载器按 ODS 水位增量采集，单独指定日期不会把源数据截断到该日。原来的一次性脚本仍可调用同一个入口，运行结果请看日志。

## 手工维度刷新

根据 DWD 后置检查或 DWS 前置检查日志的缺维清单补充对应 CSV。CSV 由数据维护者通过内部渠道提供，放在本地 `data/manual_dimensions/`。确认修改后，从仓库根目录运行对应模块，再补跑目标月链。

| CSV | 刷新命令 |
| --- | --- |
| `dim_energy_vehicle.csv`（能源类型与车型大类） | `python -m pipelines.dim.refresh_dim_energy_vehicle` |
| `dim_model_ads_matched.csv`（智驾方案映射） | `python -m pipelines.dim.refresh_dim_model_ads` |
| `dim_area.csv`（地区映射） | `python -m pipelines.dim.refresh_dim_area` |

按业务键新增或更新，保留未出现在 CSV 中的历史记录。刷新过程只清理当前连接创建的临时表。缺维阈值以 [dimension_readiness.py](../pipelines/dim/dimension_readiness.py) 为准。

## MySQL 账号

给 ETL 使用新建的专用 MySQL 账号，通过 `.env` 的 `DB_USER` 和 `DB_PASSWORD` 配置。由管理员在独立会话中创建和授予权限，管理员凭据放在项目及智能体可访问的环境之外。

以下以已经部署存储过程的 `dw_auto_stock` 为例。账号的主机范围用实际 ETL 主机或网段替换，密码使用独立生成的值。示例用于配置权限，普通 ETL 启动不执行它。

```sql
CREATE USER 'carapp_etl'@'<etl-host>' IDENTIFIED BY '<generated-password>';
GRANT SELECT, INSERT, UPDATE, CREATE TEMPORARY TABLES
    ON `dw_auto_stock`.* TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_build_sales_dimensions TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_clean_sales_dwd TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_update_lifecycle TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_etl_agg_sales_monthly TO 'carapp_etl'@'<etl-host>';
GRANT EXECUTE ON PROCEDURE `dw_auto_stock`.sp_refresh_dim_model_ads_all TO 'carapp_etl'@'<etl-host>';
SHOW GRANTS FOR 'carapp_etl'@'<etl-host>';
```

同时核对账号没有通过角色、全局授权或其他授权路径取得 `DROP`、`ALTER`、`DELETE`、永久建表或管理权限。MySQL 的 `TRUNCATE TABLE` 需要 `DROP` 权限。上线前核对已部署过程的定义和安全上下文，特别是以定义者权限执行的例程，以及触发器。保留 Power BI 只读账号。涉及数据库维护时，由管理员先确认可恢复备份。

运行账号可做正常更新，更新范围仍需由业务 SQL 控制。验证代码使用离线预览、模拟连接或独立测试库。正式库的结构升级由管理员单独处理。
