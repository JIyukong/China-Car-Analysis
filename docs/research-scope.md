# 非研究范围车企

2026-11-27 用户确认当次34个 ownership 对象全部排除，固定名单见 scheduler/research_scope.py。
OUT_OF_RESEARCH 是汇总分类，不是法人，也不表示这些企业已倒闭。保留厂商、品牌、月份和原销量。

全市场总量使用全部销量。车企排名、集中度及经营表现必须先按公司代码排除
OUT_OF_RESEARCH 和未确认的 OTHER；Python 查询结果使用 research_companies 过滤。
市场份额的分母仍为全部市场销量，不能因排除研究对象而缩小分母。
当前后端仅提供健康检查，尚无上述分析接口，新增接口必须遵循此约束。

离线预览：`python -m scheduler.runner --apply-research-exclusions logs/etl/research_exclusions_receipt.json --plan`。
应用同一命令去掉 --plan，仅使用独立 ETL 账号。遇到已有真实归属或桥接冲突整批回滚，
不自动扩大名单、不覆盖真实公司、不发送飞书消息、不更改城市独立链。
归属更新不等于历史销量重新验收或所有待办消失；历史发布需走原有逐月验收。
