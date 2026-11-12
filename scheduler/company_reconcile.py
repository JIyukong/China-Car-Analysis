"""Apply the reviewed company ownership corrections to published sales."""

from datetime import date, timedelta

from sqlalchemy import text

from scheduler.monthly_publish import _publication_transaction, _read, _write_groups


# OEM IDs are checked against the reviewed company codes before any updates.
_REVIEWED = {
    8: "BAIC", 20: "WEIQIAO_AUTO", 31: "JIMAI", 33: "JMC", 34: "JMEV",
    35: "JAC", 51: "TESLA", 70: "SHINERAY", 72: "BAIC", 79: "SPOTLIGHT_AUTO",
    113: "WEIQIAO_AUTO", 119: "ROX_MOTOR", 162: "SAIC", 169: "YUEDA_KIA",
}
# The last day of the sales month determines the reporting company.
_HISTORY = {
    8: ("2019-01", "BAIC", "BAIC_GROUP", "https://static.cninfo.com.cn/finalpage/2025-01-01/1222189974.PDF"),
    113: ("2023-02", "BAW", "WEIQIAO_AUTO", "https://qd.ifeng.com/c/8a9fQ1xsJ6C"),
}
_COMPANIES = (
    ("BAIC_GROUP", "北汽集团", "北京汽车集团有限公司"),
    ("BAW", "北京汽车制造厂", "北京汽车制造厂有限公司"),
)
_SALES_TABLES = ("dwd_sales_monthly_model", "dwd_sales_monthly_model_city")


def _run(conn, sql, **params):
    return conn.execute(text(sql), params).rowcount


def _register_companies(conn):
    for code, name, full_name in _COMPANIES:
        values = {"code": code, "name": name, "full": full_name}
        rows = _read(conn, """
            SELECT company_code,full_name,is_listed FROM dim_company
            WHERE company_code=:code OR company_name=:name OR full_name=:full FOR UPDATE
        """, **values)
        if rows:
            if rows != [{"company_code": code, "full_name": full_name, "is_listed": 0}]:
                raise ValueError(f"公司档案存在冲突：{code}")
            continue
        _run(conn, """
            INSERT INTO dim_company (company_code,company_name,full_name,company_type,country,is_listed)
            VALUES (:code,:name,:full,'其他','CN',0)
        """, **values)


def _record_history(conn, oem, period, previous, current, source):
    starts = date.fromisoformat(period + "-01")
    ends = starts - timedelta(days=1)
    rows = _read(conn, """
        SELECT * FROM bridge_brand_oem WHERE oem_id=:oem FOR UPDATE
    """, oem=oem)
    active = [row for row in rows if row["is_current"]]
    historical = [row for row in rows if not row["is_current"]]
    if len(active) != 1 or active[0]["effective_end_date"] != date.max:
        raise ValueError(f"主机厂当前品牌桥接不唯一：{oem}")
    if historical:
        if (
            len(historical) == 1
            and historical[0]["company_id"] == previous["id"]
            and historical[0]["effective_end_date"] == ends
            and active[0]["company_id"] == current["id"]
            and active[0]["effective_start_date"] == starts
        ):
            return
        raise ValueError(f"主机厂历史桥接与核验结果冲突：{oem}")
    bridge = active[0]
    if bridge["effective_start_date"] >= starts:
        raise ValueError(f"主机厂原始生效日与核验结果冲突：{oem}")
    _run(conn, """
        UPDATE bridge_brand_oem SET company_id=:company,company_name_snapshot=:name,
            effective_start_date=:starts,source_note=:source WHERE id=:id
    """, company=current["id"], name=current["company_name"], starts=starts, source=source, id=bridge["id"])
    _run(conn, """
        INSERT INTO bridge_brand_oem
          (brand_id,brand_name_snapshot,oem_id,oem_name_snapshot,company_id,company_name_snapshot,
           relation_type,effective_start_date,effective_end_date,is_current,source_note)
        VALUES (:brand_id,:brand_name_snapshot,:oem_id,:oem_name_snapshot,:previous,:previous_name,
                :relation_type,:effective_start_date,:ends,0,:source)
    """, **bridge, previous=previous["id"], previous_name=previous["company_name"], ends=ends, source=source)
    _run(conn, """
        UPDATE dim_oem SET company_id=:company,company_name_snapshot=:name WHERE id=:oem
    """, company=current["id"], name=current["company_name"], oem=oem)


def reconcile_reviewed_companies():
    with _publication_transaction() as conn:
        _register_companies(conn)
        companies = {row["company_code"]: row for row in _read(conn, """
            SELECT id,company_code,company_name FROM dim_company
        """)}
        oems = {row["id"]: row for row in _read(conn, "SELECT id,company_id,oem_name FROM dim_oem FOR UPDATE")}
        report = {}
        affected = set()
        for oem, reviewed in _REVIEWED.items():
            period, prev, code, source = _HISTORY.get(oem, ("1900-01", reviewed, reviewed, None))
            previous, current = companies[prev], companies[code]
            if oems[oem]["company_id"] not in (companies[reviewed]["id"], current["id"]):
                raise ValueError(f"主机厂归属在复核后发生变化：{oem}")
            if source:
                _record_history(conn, oem, period, previous, current, source)
            changes = {}
            for table in _SALES_TABLES:
                changes[table] = 0
                # Repair OTHER and explicitly reviewed stale values. Reject other assignments.
                for comparator, target in (("<", previous), (">=", current)):
                    params = {"oem": oem, "period": period, "company": target["id"],
                              "other": companies["OTHER"]["id"], "reviewed": companies[reviewed]["id"]}
                    scope = f"WHERE oem_id=:oem AND stat_month {comparator} :period"
                    before = _read(conn, f"""
                        SELECT stat_month,COUNT(*) n,SUM(sales_volume) sales,
                            SUM(company_id=:company) correct
                        FROM {table} {scope} GROUP BY stat_month
                    """, **params)
                    expected = sum(row["n"] - (row["correct"] or 0) for row in before)
                    if not expected:
                        continue
                    updated = _run(conn, f"""
                        UPDATE {table} SET company_id=:company {scope}
                          AND company_id<>:company AND company_id IN (:other,:reviewed)
                    """, **params)
                    after = _read(conn, f"""
                        SELECT stat_month,COUNT(*) n,SUM(sales_volume) sales,
                            SUM(company_id=:company) correct
                        FROM {table} {scope} GROUP BY stat_month
                    """, **params)
                    if updated != expected or after != [{**row, "correct": row["n"]} for row in before]:
                        raise ValueError(f"{table} 公司归属或销量核对失败：{oem}")
                    changes[table] += updated
                    if table == _SALES_TABLES[0]:
                        affected.update(str(row["stat_month"])[:7] for row in before if row["correct"] != row["n"])
            report[oem] = {"oem_name": oems[oem]["oem_name"], "current_company": code, **changes}

        published = {str(row["stat_month"])[:7] for row in _read(conn, """
            SELECT DISTINCT stat_month FROM dws_sales_monthly_company WHERE is_current=1
        """)}
        rebuilt = sorted(published & affected)
        for month in rebuilt:
            rows = _read(conn, """
                SELECT company_id,sales_volume FROM dwd_sales_monthly_model WHERE stat_month=:month
            """, month=month)
            _write_groups(conn, "company", date.fromisoformat(month + "-01"), rows)
        return {"oems": report, "affected_style_months": len(affected), "dws_months_rebuilt": rebuilt}
