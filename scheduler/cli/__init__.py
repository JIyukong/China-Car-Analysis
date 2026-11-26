"""runner 命令行的组装与管理子命令分发。

- parser.py：argparse 参数定义（保持历史 flag 与报错文案不变）
- guards.py：管理子命令通用的互斥与 ETL 账号校验
- admin_commands.py：11 个独立管理/修复类子命令的处理器
"""
