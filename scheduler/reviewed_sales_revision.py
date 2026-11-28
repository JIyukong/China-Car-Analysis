"""Apply the user's explicit 2026-11-28 source identity/price review."""

import csv
import hashlib
import json
import re
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

OLD = "哈弗枭龙MAX"
NEW = "长城H6L 新能源"
CITY_NEW = "22款红旗H5"
EVIDENCE = "用户2026-11-28核准；全国2026-10 /s/129831/ 应为长城H6L 新能源；城市15.98-22.58红旗H5独立为22款红旗H5，燃油含普通HEV，不是PHEV"


def read(conn, sql, **params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def insert(conn, table, row):
    conn.execute(text(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row)


def identity_hash(chain, month, model, key):
    return hashlib.md5(f"16888_{chain}|{month}|{model}|{key}".encode()).hexdigest()


def repair_national_identity(conn):
    params = {"old": OLD, "new": NEW, "oem": "长城汽车"}
    old = read(
        conn,
        "SELECT * FROM ods_sales_monthly_model WHERE source='16888_style' AND stat_month='2026-10-01' AND model_name_raw=:old AND manufacturer_name_raw=:oem",
        **params,
    )
    target = read(
        conn,
        "SELECT * FROM ods_sales_monthly_model WHERE source='16888_style' AND stat_month='2026-10-01' AND model_name_raw=:new AND manufacturer_name_raw=:oem",
        **params,
    )
    if not old:
        if len(target) != 1 or target[0]["sales_volume"] != 60:
            raise ValueError("全国目标来源身份或销量不一致")
        models = read(conn, "SELECT * FROM dim_model WHERE model_name=:new AND oem_id=110", **params)
        facts = read(
            conn,
            "SELECT * FROM dwd_sales_monthly_model WHERE source='16888_style' AND stat_month='2026-10' AND (model_name_raw=:new OR model_name_raw=:old)",
            **params,
        )
        expected_hash = identity_hash("style", "2026-10", NEW, "长城汽车")
        if (
            len(models) != 1
            or len(facts) != 1
            or facts[0]["model_name_raw"] != NEW
            or facts[0]["model_name"] != NEW
            or facts[0]["model_id"] != models[0]["id"]
            or facts[0]["sales_volume"] != 60
            or facts[0]["row_hash"] != expected_hash
            or target[0]["row_hash"] != expected_hash
        ):
            raise ValueError("已修订全国ODS与DWD身份不一致")
        return {"changed": False}
    if len(old) != 1 or old[0]["sales_volume"] != 60 or target:
        raise ValueError("全国改名前后业务键冲突或销量未经核准")
    original = read(conn, "SELECT * FROM dim_model WHERE model_name=:old AND oem_id=110", **params)
    if len(original) != 1:
        raise ValueError("原车型维度必须唯一")
    model = original[0]
    facts = read(
        conn,
        "SELECT * FROM dwd_sales_monthly_model WHERE source='16888_style' AND stat_month='2026-10' AND model_name_raw=:old AND oem_id=110",
        **params,
    )
    if len(facts) != 1 or facts[0]["sales_volume"] != 60 or facts[0]["model_id"] != model["id"]:
        raise ValueError("全国事实与来源修订不匹配")
    if read(
        conn,
        "SELECT id FROM dwd_sales_monthly_model WHERE source='16888_style' AND stat_month='2026-10' AND model_name_raw=:new",
        **params,
    ):
        raise ValueError("全国目标事实已存在，禁止合并")
    energy = read(conn, "SELECT * FROM dim_energy_vehicle WHERE model_name=:new", **params)
    if energy and (len(energy) != 1 or energy[0]["energy_type"] != "新能源" or energy[0]["vehicle_type"] != "SUV"):
        raise ValueError("全国新车型能源分类冲突")
    if not energy:
        insert(
            conn,
            "dim_energy_vehicle",
            {"model_name": NEW, "energy_type": "新能源", "vehicle_type": "SUV", "source_file": EVIDENCE},
        )
    new_models = read(conn, "SELECT * FROM dim_model WHERE model_name=:new AND oem_id=110", **params)
    if not new_models:
        clone = {k: v for k, v in model.items() if k not in {"id", "created_at", "updated_at"}}
        clone.update(
            model_name=NEW, model_code="MODEL_" + hashlib.md5(f"{NEW}|长城汽车".encode()).hexdigest()[:10].upper()
        )
        for key in ("ads_id", "ads_supplier_name", "first_seen_month", "last_seen_month"):
            if key in clone:
                clone[key] = None
        insert(conn, "dim_model", clone)
        new_models = read(conn, "SELECT * FROM dim_model WHERE model_name=:new AND oem_id=110", **params)
    if len(new_models) != 1 or any(
        new_models[0][k] != model[k] for k in ("brand_id", "oem_id", "energy_type", "vehicle_type")
    ):
        raise ValueError(
            f"全国新车型维度属性冲突: {new_models!r}; 原属性={[(k, model[k]) for k in ('brand_id', 'oem_id', 'energy_type', 'vehicle_type')]!r}"
        )
    values = {**params, "hash": identity_hash("style", "2026-10", NEW, "长城汽车"), "model_id": new_models[0]["id"]}
    conn.execute(
        text("UPDATE ods_sales_monthly_model SET model_name_raw=:new,row_hash=:hash WHERE id=:id"),
        {**values, "id": old[0]["id"]},
    )
    conn.execute(
        text(
            "UPDATE dwd_sales_monthly_model SET model_name=:new,model_name_raw=:new,model_id=:model_id,row_hash=:hash WHERE id=:id"
        ),
        {**values, "id": facts[0]["id"]},
    )
    ods_after = read(conn, "SELECT * FROM ods_sales_monthly_model WHERE id=:id", id=old[0]["id"])[0]
    fact_after = read(conn, "SELECT * FROM dwd_sales_monthly_model WHERE id=:id", id=facts[0]["id"])[0]
    if (
        ods_after["model_name_raw"] != NEW
        or fact_after["model_name_raw"] != NEW
        or ods_after["sales_volume"] != 60
        or fact_after["sales_volume"] != 60
        or fact_after["model_id"] != new_models[0]["id"]
        or ods_after["row_hash"] != values["hash"]
        or fact_after["row_hash"] != values["hash"]
    ):
        raise ValueError("全国身份修订事务回读不一致")
    return {"changed": True, "ods_before": old[0], "dwd_before": facts[0], "new_model_id": new_models[0]["id"]}


def recover_city_manifest(csv_path, period):
    csv_path = Path(csv_path)
    log = csv_path.with_name("crawl.log").read_text(encoding="utf-8")
    if "ValueError: 来源重复业务键内容冲突: ('红旗H5'," not in log:
        raise ValueError("仅可恢复已核准的H5身份冲突，不接受其他抓取失败")
    hits = re.findall(r"city (\d{4}-\d{2}) 第 (\d+) 页 (\d+) 行，累计 (\d+)/(\d+)", log)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    if not hits or any(m != period or int(page) != i for i, (m, page, _, _, _) in enumerate(hits, 1)):
        raise ValueError("城市原始分页日志不连续")
    counts = [int(h[2]) for h in hits]
    total = int(hits[0][4])
    if (
        len(rows) != total
        or sum(counts) != total
        or any(int(h[4]) != total or int(h[3]) != sum(counts[:i]) for i, h in enumerate(hits, 1))
    ):
        raise ValueError("城市原始分页与CSV覆盖不足")
    return {
        "chain": "city",
        "period": period,
        "status": "published",
        "pagination_complete": True,
        "source_total": total,
        "page_rows": counts,
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "reviewed_recovery": EVIDENCE,
        "crawl_log_sha256": hashlib.sha256(log.encode()).hexdigest(),
    }


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("style", {}).get("period") != "2026-10" or sorted(r["period"] for r in spec.get("city", [])) != [
        "2023-03",
        "2023-04",
        "2023-05",
    ]:
        raise ValueError("此修订仅授权全国2026-10和城市2023-03至05来源快照")
    return spec


def prepare(spec, root):
    result = []
    for chain, records in [("style", [spec["style"]]), ("city", spec["city"])]:
        for record in records:
            p = (root / record["csv"]).resolve()
            trusted = (root / f"pipelines/ingest/sales/crawl_16888_{chain}/data/snapshots").resolve()
            if (
                not p.is_relative_to(trusted)
                or not p.is_file()
                or hashlib.sha256(p.read_bytes()).hexdigest() != record["sha256"]
            ):
                raise ValueError("审核快照路径或哈希不匹配")
            manifest = p.with_name("manifest.json")
            if chain == "city" and not manifest.exists():
                recovered = recover_city_manifest(p, record["period"])
            else:
                recovered = json.loads(manifest.read_text(encoding="utf-8"))
            result.append(
                {"chain": chain, "period": record["period"], "path": p, "manifest": manifest, "recovered": recovered}
            )
    return result


def repair_city_identities(conn, *, old_name="红旗H5", new_name=CITY_NEW, period=None):
    from scheduler.sales_source_rules import select_rows

    source = read(
        conn,
        "SELECT * FROM ods_sales_monthly_model_city WHERE source='16888_city' AND model_name_raw=:old_name",
        old_name=old_name,
    )
    selected = [
        r
        for r in source
        if select_rows([{"model_name": r["model_name_raw"], "price_range": r["price_range_raw"]}], "city")[0][0][
            "model_name"
        ]
        == new_name
    ]
    if period is not None:
        selected = [r for r in source if str(r["stat_month"])[:7] == period]
    bases = read(
        conn,
        "SELECT * FROM dim_city_model WHERE source='16888_city' AND source_model_name=:old_name",
        old_name=old_name,
    )
    if (
        len(bases) != 1
        or bases[0]["review_status"] != "confirmed"
        or bases[0]["energy_type"] != ("燃油" if period is None else "新能源")
    ):
        raise ValueError("城市H5原维度未核准")
    target = read(
        conn, "SELECT * FROM dim_city_model WHERE source='16888_city' AND source_model_name=:name", name=new_name
    )
    if not target:
        clone = {k: v for k, v in bases[0].items() if k not in {"id", "created_at", "updated_at"}}
        clone.update(
            source_model_name=new_name,
            model_name=new_name,
            model_code="CITY_" + hashlib.sha256(f"16888_city|{new_name}".encode()).hexdigest()[:32].upper(),
            evidence=EVIDENCE,
            national_candidates="[]",
            first_seen_month=None,
            last_seen_month=None,
            is_active=0,
        )
        insert(conn, "dim_city_model", clone)
        target = read(
            conn, "SELECT * FROM dim_city_model WHERE source='16888_city' AND source_model_name=:name", name=new_name
        )
    if len(target) != 1 or any(
        target[0][k] != bases[0][k] for k in ("brand_code", "oem_code", "energy_type", "vehicle_type")
    ):
        raise ValueError("城市H5新维度属性冲突")
    all_facts = read(
        conn,
        "SELECT * FROM dwd_sales_monthly_city WHERE source='16888_city' AND model_name_raw=:old_name",
        old_name=old_name,
    )
    facts_by_key = {}
    for fact in all_facts:
        key = (str(fact["stat_month"])[:7], fact["city_name_raw"])
        if key in facts_by_key:
            raise ValueError("城市H5存量DWD业务键重复")
        facts_by_key[key] = fact
    existing_targets = {
        (str(r["stat_month"])[:7], r["city_name_raw"])
        for r in read(
            conn,
            "SELECT stat_month,city_name_raw FROM ods_sales_monthly_model_city WHERE source='16888_city' AND model_name_raw=:name",
            name=new_name,
        )
    }
    fact_targets = {
        (str(r["stat_month"])[:7], r["city_name_raw"])
        for r in read(
            conn,
            "SELECT stat_month,city_name_raw FROM dwd_sales_monthly_city WHERE source='16888_city' AND model_name_raw=:name",
            name=new_name,
        )
    }
    receipts, ods_updates, dwd_updates = [], [], []
    for row in selected:
        month = str(row["stat_month"])[:7]
        key = (month, row["city_name_raw"])
        if key in existing_targets or key in fact_targets:
            raise ValueError("城市H5目标ODS已存在，禁止合并")
        fact = facts_by_key.get(key)
        if fact is None or fact["sales_volume"] != row["sales_volume"]:
            raise ValueError("城市H5历史ODS与DWD不一致")
        values = {
            "id": row["id"],
            "name": new_name,
            "hash": identity_hash("city", month, new_name, row["city_name_raw"]),
        }
        ods_updates.append(values)
        dwd_updates.append({**values, "id": fact["id"], "model": target[0]["id"]})
        receipts.append({"ods_before": row, "dwd_before": fact})
    if ods_updates:
        conn.execute(
            text("UPDATE ods_sales_monthly_model_city SET model_name_raw=:name,row_hash=:hash WHERE id=:id"),
            ods_updates,
        )
        conn.execute(
            text(
                "UPDATE dwd_sales_monthly_city SET model_name_raw=:name,model_name=:name,city_model_id=:model,row_hash=:hash WHERE id=:id"
            ),
            dwd_updates,
        )
        ods_after = {
            r["id"]: r
            for r in read(
                conn,
                "SELECT * FROM ods_sales_monthly_model_city WHERE source='16888_city' AND model_name_raw=:name",
                name=new_name,
            )
        }
        dwd_after = {
            r["id"]: r
            for r in read(
                conn,
                "SELECT * FROM dwd_sales_monthly_city WHERE source='16888_city' AND model_name_raw=:name",
                name=new_name,
            )
        }
        for receipt in receipts:
            for label, stored in [("ods_before", ods_after), ("dwd_before", dwd_after)]:
                before = receipt[label]
                after = stored.get(before["id"])
                if (
                    after is None
                    or after["sales_volume"] != before["sales_volume"]
                    or after["model_name_raw"] != new_name
                    or after["row_hash"]
                    != identity_hash("city", str(before["stat_month"])[:7], new_name, before["city_name_raw"])
                    or (label == "dwd_before" and after["city_model_id"] != target[0]["id"])
                ):
                    raise ValueError("城市身份修订事务回读不一致")
    return {
        "renamed_rows": receipts,
        "periods": sorted({str(r["stat_month"])[:7] for r in selected}),
        "city_model_id": target[0]["id"],
    }


def run(spec_path, *, plan=False):
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(spec_path)
    if plan:
        return {
            "plan": True,
            "style": spec["style"]["period"],
            "city": [r["period"] for r in spec["city"]],
            "actions": ["exact identity corrections", "six reviewed prices", "transactional readback and publication"],
        }
    prepared = prepare(spec, root)
    from scheduler import monthly_source
    from scheduler.monthly_publish import _publication_transaction
    from scheduler.status import BatchStatus
    from scheduler.workflow import State, _publish

    state = State()
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    receipt_path = root / "logs/etl" / f"sales_source_review_{stamp}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=receipt_path.parent):
        pass
    report = {"committed": False, "receipt": str(receipt_path), "snapshots": []}
    with ExitStack() as locks:
        for lock in ("collect-city", "collect-style", "warehouse"):
            locks.enter_context(state.lock(lock, timeout=120))
        validated = []
        for r in prepared:
            if not r["manifest"].exists():
                r["manifest"].write_text(json.dumps(r["recovered"], ensure_ascii=False, indent=2), encoding="utf-8")
            raw = monthly_source._validated_csv(r["path"], r["manifest"], r["chain"], r["period"])
            loader = monthly_source._loader(r["chain"])
            validated.append((r, loader, list(loader.read_rows(r["path"], raw_csv=raw))))
            report["snapshots"].append(
                {
                    "chain": r["chain"],
                    "period": r["period"],
                    "raw_sha256": r["recovered"]["csv_sha256"],
                    "rows": len(validated[-1][2]),
                }
            )
        revisions = {}
        with _publication_transaction() as conn:
            report["national"] = repair_national_identity(conn)
            report["city"] = repair_city_identities(conn)
            report["city_h6l"] = repair_city_identities(conn, old_name=OLD, new_name=NEW, period="2023-05")
            for r, loader, rows in validated:
                revisions[(r["chain"], r["period"])] = monthly_source._reconcile_ods(
                    loader, r["chain"], r["period"], rows, connection=conn
                )
            for period in report["city"]["periods"]:
                if ("city", period) not in revisions:
                    loader = monthly_source._loader("city")
                    revisions[("city", period)] = monthly_source._revision_digest(
                        "city", period, monthly_source._ods_content(conn, loader, "city", period, lock=True)
                    )
        report["committed"] = True
        report["revisions"] = [{"chain": c, "period": p, "digest": d} for (c, p), d in revisions.items()]
        receipt_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        results = []
        for (chain, period), digest in sorted(revisions.items()):
            state.save(chain, period, BatchStatus.READY, digest=digest)
        for (chain, period), digest in sorted(revisions.items()):
            okay = _publish(state, chain, period, digest)
            results.append(
                {
                    "chain": chain,
                    "period": period,
                    "published": okay and state.batch(chain, period)["status"] == BatchStatus.PUBLISHED,
                }
            )
        state.export()
        report["publication"] = results
        receipt_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "committed": True,
        "receipt": str(receipt_path),
        "national_changed": report["national"]["changed"],
        "city_renamed_rows": len(report["city"]["renamed_rows"]),
        "publication": results,
    }
