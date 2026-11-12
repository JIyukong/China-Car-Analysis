# 数仓分层

CarApp 将贴源数据保存在 MySQL 的 ODS 层，结合维度加工成 DWD 明细，再汇总为面向 BI 的 DWS 数据。各主题进入的层级以仓库实现为准。

| 主题 | 来源 | 当前数据流 |
| --- | --- | --- |
| 车型月销量 | 16888 style CSV | `ods_sales_monthly_model` → 车型与归属核验 → `dwd_sales_monthly_model` → 公司和品牌月销量 DWS 同事务发布 |
| 城市车型月销量 | 16888 city CSV | `ods_sales_monthly_model_city` → 关联车型和地区 → `dwd_sales_monthly_model_city` |
| 股票日线与财务 | AKShare，A 股及港股日线、A 股财务 | 日链按交易日或报告期验收 ODS，启用 `ETL_STOCK_DWD_ENABLED` 后按周期增量发布对应 DWD |
| 行业指数 | AKShare 同花顺汽车整车指数 881125 | `dim_index` → `ods_index_daily` |
| 商品期货 | AKShare 新浪期货主力连续，碳酸锂 LC0、铜 CU0、铝 AL0 | `dim_commodity` → `ods_futures_daily` |

## 分层与代码

| 层 | 职责 | 实现 |
| --- | --- | --- |
| ODS | 按来源保留原始字段与采集时间，销量保留文件和行哈希以便溯源 | [采集脚本](../pipelines/ingest/)、[ODS DDL](../db/ods/) |
| DIM 和 Bridge | 维护车型、品牌、主机厂、上市公司、证券、日期等维度和映射。上市公司与主机厂分开建模，品牌与主机厂、公司与股票的映射由桥接表维护 | [维度脚本](../pipelines/dim/)、[维度 DDL](../db/dim/) |
| DWD | 标准化名称、价格和日期，关联维度 ID。销量月份为 `CHAR(7)`（`YYYY-MM`） | [清洗脚本](../pipelines/staging/)、[DWD DDL](../db/dwd/) |
| DWS | 汇总公司、品牌月度销量。月份为 `DATE`，取每月第一天，BI 读取当前发布视图 | [发布逻辑](../scheduler/monthly_publish.py)、[当前视图](../db/migrations/006_monthly_publication_current_groups.sql) |
| ADS 和 BI | 仓库有 [缺少智驾映射的车型视图](../db/ads/01_create_vw_dim_model_ads_manual_missing_models.sql) 和 [Power BI 只读账号脚本](../db/system/01_create_powerbi_user.sql)。报表计划见 [路线图](roadmap.md) | [ADS SQL](../db/ads/) |

飞书补录提供能源与车型大类、智驾方案、地区、级别和归属映射。智驾 ADS 拉链保留供应商及模式历史。城市明细依赖车型维度，待补期间全国销量按自身依赖独立发布。刷新命令和缺维处理见 [调度指南](scheduler_guide.md#飞书补录)。

## 数据库结构维护

表、触发器、存储过程和迁移由管理员在独立会话维护。以 [db/](../db/) 下的 DDL、[迁移文件](../db/migrations/) 和目标库已部署版本核对依赖。迁移按目标库实际结构选择。日常 ETL 的账号和操作边界见 [MySQL 账号](scheduler_guide.md#mysql-账号)。

分析用字段和业务键见 [数据字典](data-dictionary.md)，计算定义见 [指标口径](metric-definition.md)。
