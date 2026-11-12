"""Consolidate explicitly confirmed OEM aliases and their sales references."""

import json
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import text

from pipelines.dim.oem_aliases import resolve_aliases
from scheduler.monthly_publish import _publication_transaction, _read, _write_groups


def _sales_totals(rows):
    totals = Counter()
    for row in rows:
        totals[row["stat_month"]] += row["sales_volume"]
    return len(rows), totals


def merge_oems(pairs):
    if not pairs or len({old for old, _ in pairs}) != len(pairs):
        raise ValueError("每个别名主机厂只能归并一次")
    retired = {old for old, _ in pairs}
    if retired.intersection(target for _, target in pairs):
        raise ValueError("请直接指定每个别名的正式主机厂")

    with _publication_transaction() as conn:
        oems = {row["id"]: row for row in _read(
            conn, "SELECT id,oem_name,company_id,aliases FROM dim_oem FOR UPDATE"
        )}
        counts = Counter()
        for old, target in pairs:
            legacy, canonical = oems[old], oems[target]
            if legacy["company_id"] != canonical["company_id"]:
                raise ValueError(f"主机厂公司归属不同：{old}, {target}")
            values = []
            for row in (canonical, legacy):
                raw = row["aliases"]
                values.extend(json.loads(raw) if isinstance(raw, str) else raw or [])
            canonical["aliases"] = sorted(set(values + [legacy["oem_name"]]))
            legacy["aliases"] = None
        resolve_aliases(list(oems.values()))

        for old, target in pairs:
            counts.update(_merge_pair(conn, oems[old], oems[target]))
        return dict(counts)


def _merge_pair(conn, old, target):
    params = {"old": old["id"], "target": target["id"], "name": target["oem_name"],
              "aliases": json.dumps(target["aliases"], ensure_ascii=False)}
    by_oem = defaultdict(dict)
    for model in _read(conn, """
        SELECT id,model_name,oem_id,brand_id FROM dim_model
        WHERE oem_id IN (:old,:target) FOR UPDATE
    """, **params):
        key = model["model_name"].strip().casefold()
        if key in by_oem[model["oem_id"]]:
            raise ValueError(f"主机厂存在重复车型：{model['model_name']}")
        by_oem[model["oem_id"]][key] = model

    mapping = {
        source["id"]: by_oem[target["id"]].get(key, source)
        for key, source in by_oem[old["id"]].items()
    }
    changes = Counter()
    brand_months = set()
    for table, extra in (("dwd_sales_monthly_model", ""), ("dwd_sales_monthly_model_city", ",area_id")):
        select = f"""SELECT id,stat_month,oem_id,model_id,brand_id,sales_volume{extra}
                     FROM {table} WHERE oem_id IN (:old,:target)"""
        rows = _read(conn, select + " FOR UPDATE", **params)
        seen = set()
        updates = Counter()
        for row in rows:
            model_id = row["model_id"]
            if row["oem_id"] == old["id"]:
                model = mapping[model_id]
                model_id = model["id"]
                updates[(row["model_id"], model_id, model["brand_id"])] += 1
                if not extra and row["brand_id"] != model["brand_id"]:
                    brand_months.add(str(row["stat_month"])[:7])
            key = (row["stat_month"], model_id, row.get("area_id"))
            if key in seen:
                raise ValueError(f"{table} 归并后销量业务键重复：{key}")
            seen.add(key)
        for (old_model, model_id, brand_id), expected in updates.items():
            result = conn.execute(text(f"""
                UPDATE {table} SET oem_id=:target, model_id=:model_id, brand_id=:brand_id
                WHERE oem_id=:old AND model_id=:old_model
            """), {**params, "old_model": old_model, "model_id": model_id, "brand_id": brand_id})
            if result.rowcount != expected:
                raise ValueError(f"{table} 更新行数与预检不一致")
        after = _read(conn, select, **params)
        if _sales_totals(after) != _sales_totals(rows) or any(row["oem_id"] == old["id"] for row in after):
            raise ValueError(f"{table} 归并后行数或销量核对失败")
        changes[table] += sum(updates.values())

    for source_id, canonical in mapping.items():
        if source_id == canonical["id"]:
            conn.execute(text("UPDATE dim_model SET oem_id=:target,oem_name=:name WHERE id=:id"),
                         {**params, "id": source_id})
            changes["dim_model_moved"] += 1
        else:
            result = conn.execute(text("UPDATE dim_model SET is_active=0 WHERE id=:id AND is_active<>0"),
                                  {"id": source_id})
            changes["dim_model_retired"] += result.rowcount

    conn.execute(text("UPDATE dim_oem SET aliases=:aliases WHERE id=:target"), params)
    conn.execute(text("UPDATE dim_oem SET aliases=NULL,is_active=0 WHERE id=:old"), params)
    changes["oem_aliases"] += 1
    for period in sorted(brand_months):
        rows = _read(conn, """
            SELECT brand_id,sales_volume FROM dwd_sales_monthly_model WHERE stat_month=:period
        """, period=period)
        _write_groups(conn, "brand", date.fromisoformat(period + "-01"), rows)
        changes["dws_brand_months"] += 1
    return changes
