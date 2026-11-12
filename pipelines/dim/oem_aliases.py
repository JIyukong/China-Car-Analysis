"""Resolve source manufacturer names to canonical OEM records."""

import json

from sqlalchemy import bindparam, text


def resolve_aliases(rows):
    names = {row["oem_name"].strip().lower(): row for row in rows}
    aliases = {}
    retired = set()
    for row in rows:
        values = row["aliases"]
        if isinstance(values, str):
            values = json.loads(values)
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"无效的主机厂别名：{row['oem_name']}")
        for value in values:
            key = value.strip().lower()
            exact = names.get(key)
            if key in aliases and aliases[key] != row["id"]:
                raise ValueError(f"主机厂别名重复：{value}")
            if exact and exact["id"] != row["id"]:
                if exact["company_id"] != row["company_id"]:
                    raise ValueError(f"主机厂别名跨公司：{value}")
                retired.add(exact["id"])
            aliases[key] = row["id"]
    if retired.intersection(aliases.values()):
        raise ValueError("主机厂别名存在多级归并")
    return aliases, retired


def read_aliases(conn):
    rows = list(conn.execute(
        text("SELECT id, oem_name, company_id, aliases FROM dim_oem")
    ).mappings())
    aliases, retired = resolve_aliases(rows)
    if not retired:
        return aliases, retired
    # Keep exact-name records in use until their published facts have been rekeyed.
    live = set(conn.execute(text("""
        SELECT DISTINCT oem_id FROM dwd_sales_monthly_model WHERE oem_id IN :ids
        UNION SELECT DISTINCT oem_id FROM dwd_sales_monthly_model_city WHERE oem_id IN :ids
    """).bindparams(bindparam("ids", expanding=True)), {"ids": sorted(retired)}).scalars())
    for row in rows:
        if row["id"] in live:
            aliases.pop(row["oem_name"].strip().lower(), None)
    return aliases, retired - live


def canonical_model_clause(retired, column):
    params = {f"retired_{i}": value for i, value in enumerate(sorted(retired))}
    if not params:
        return "", params
    return f"AND {column} NOT IN ({', '.join(':' + name for name in params)})", params
