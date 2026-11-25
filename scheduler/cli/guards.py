"""管理子命令的通用校验小工具。

历史上这些校验以内联样板散落在 runner.main 的长 if 链里，这里只做最小收口，
每个子命令允许的参数白名单仍由各处理器自行声明，避免泛化后改变互斥语义。
"""

from __future__ import annotations


def reject_other_flags(parser, args, *, allowed: tuple[str, ...], message: str) -> None:
    """除 allowed 白名单（argparse 下划线属性名）外，任何参数有值即报错。"""
    values = {name: value for name, value in vars(args).items() if name not in allowed}
    if any(values.values()):
        parser.error(message)


def require_etl_account(parser) -> None:
    """写库类管理命令必须使用独立 ETL 账号，禁止 root/admin 等高权账号。"""
    from base_infra.config import get_settings

    if get_settings().DB_USER.strip().casefold() in {"", "root", "admin", "administrator"}:
        parser.error("请配置独立 ETL 账号")
