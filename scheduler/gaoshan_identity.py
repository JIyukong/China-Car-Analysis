"""Keep Gaoshan HEV/PHEV identities separate; repair only evidenced old aliases."""
import json
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import text

HEV = '魏牌 高山'
PHEV = '魏牌 高山新能源'
PROFILES = {
    HEV: {'powertrain': 'HEV', 'energy_type': '燃油', 'source_id': '131182'},
    PHEV: {'powertrain': 'PHEV', 'energy_type': '新能源', 'source_id': '129881'},
}
EVIDENCE = 'https://www.wey.com/gaoshan-intelligence.html'
FACT_FIELDS = 'id,stat_month,source,model_name_raw,oem_id,brand_id,company_id,sales_volume,price_min,price_max,rank_no,row_hash'


class ReceiptWriteError(OSError):
    """Warehouse committed successfully; only the local receipt failed."""
    def __init__(self, report):
        super().__init__('数据库已提交，但本地回执写入失败；请保留输出，不要按回滚处理')
        self.report = report


def _read(conn, sql, **params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def repair(conn):
    from scheduler.monthly_publish import _month_shift
    names = {'hev': HEV, 'phev': PHEV, 'oem': '长城汽车'}
    models = _read(conn, '''SELECT m.id,m.model_name,m.energy_type,m.oem_id,m.brand_id
        FROM dim_model m JOIN dim_oem o ON o.id=m.oem_id
        WHERE m.model_name IN (:hev,:phev) AND o.oem_name=:oem''', **names)
    if len(models) != 2 or {r['model_name'] for r in models} != set(PROFILES):
        raise ValueError('高山两种车型必须独立且唯一建档')
    ids = {r['model_name']: r['id'] for r in models}
    params = {'hev_id': ids[HEV], 'phev_id': ids[PHEV], **names}
    facts = _read(conn, f'''SELECT {FACT_FIELDS},model_id,model_name FROM dwd_sales_monthly_model
        WHERE model_id IN (:hev_id,:phev_id) ORDER BY id''', **params)
    fixes = []
    for row in facts:
        if row['source'] != '16888_style' or row['model_name_raw'] not in PROFILES:
            raise ValueError('高山事实来源或原始身份未经核实')
        if row['model_name_raw'] == HEV and str(row['stat_month']) < '2026-10':
            raise ValueError('历史高山原始名称含义待核实，拒绝套用当前HEV分类')
        target = ids[row['model_name_raw']]
        if row['model_id'] == target and row['model_name'] == row['model_name_raw']:
            continue
        if row['model_id'] != ids[HEV] or row['model_name_raw'] != PHEV:
            raise ValueError('高山事实存在未核验的反向归属')
        source = _read(conn, '''SELECT sales_volume FROM ods_sales_monthly_model
            WHERE stat_month=:month AND source='16888_style' AND model_name_raw=:phev
            AND manufacturer_name_raw=:oem''', month=str(row['stat_month'])[:7]+'-01', **names)
        if len(source) != 1 or source[0]['sales_volume'] != row['sales_volume']:
            raise ValueError('高山历史事实与ODS不一致')
        if any(r['stat_month'] == row['stat_month'] and r['model_id'] == target for r in facts):
            raise ValueError('同月PHEV事实已存在，拒绝合并或覆盖')
        fixes.append({'id': row['id'], 'model_id': target, 'model_name': PHEV})
    # Preflight all authoritative/city dimensions before the first write.
    energy = _read(conn, 'SELECT model_name,energy_type,vehicle_type,source_file FROM dim_energy_vehicle WHERE model_name IN (:hev,:phev)', **names)
    cities = _read(conn, '''SELECT id,source_model_name,energy_type,vehicle_type,evidence FROM dim_city_model
        WHERE source='16888_city' AND source_model_name IN (:hev,:phev)''', **names)
    if len(energy) != 2 or len(cities) != 2:
        raise ValueError('能源或独立城市车型档案不唯一')
    for name, profile in PROFILES.items():
        values = {'name': name, 'energy': profile['energy_type'], 'id': ids[name],
                  'evidence': f"reviewed-gaoshan-v1 {profile['powertrain']}; {EVIDENCE}"}
        conn.execute(text('''UPDATE dim_energy_vehicle SET energy_type=:energy,vehicle_type='MPV',source_file=:evidence
            WHERE model_name=:name'''), values)
        conn.execute(text("UPDATE dim_model SET energy_type=:energy,vehicle_type='MPV' WHERE id=:id"), values)
        conn.execute(text('''UPDATE dim_city_model SET energy_type=:energy,vehicle_type='MPV',evidence=:evidence
            WHERE source='16888_city' AND source_model_name=:name'''), values)
    for fix in fixes:
        conn.execute(text('UPDATE dwd_sales_monthly_model SET model_id=:model_id,model_name=:model_name WHERE id=:id'), fix)
    after = _read(conn, f'''SELECT {FACT_FIELDS},model_id,model_name FROM dwd_sales_monthly_model
        WHERE model_id IN (:hev_id,:phev_id) ORDER BY id''', **params)
    immutable = [field for field in FACT_FIELDS.split(',')]
    if [{k:r[k] for k in immutable} for r in facts] != [{k:r[k] for k in immutable} for r in after]:
        raise ValueError('高山身份修复改变了原始事实内容')
    if any(r['model_id'] != ids[r['model_name_raw']] or r['model_name'] != r['model_name_raw'] for r in after):
        raise ValueError('高山事实身份回读不一致')
    latest = conn.execute(text('SELECT MAX(stat_month) FROM dwd_sales_monthly_model')).scalar_one()
    threshold = _month_shift(date.fromisoformat(str(latest)[:7]+'-01'), -3).isoformat() if latest else ''
    for identifier in ids.values():
        first, last = conn.execute(text('''SELECT MIN(stat_month),MAX(stat_month) FROM dwd_sales_monthly_model
            WHERE model_id=:id'''), {'id': identifier}).one()
        first, last = (str(first)[:7]+'-01' if first else None), (str(last)[:7]+'-01' if last else None)
        conn.execute(text('''UPDATE dim_model SET first_seen_month=:first,last_seen_month=:last,is_active=:active WHERE id=:id'''),
                     {'id': identifier,'first':first,'last':last,'active':int(last is not None and last >= threshold)})
    actual = _read(conn, 'SELECT model_name,energy_type FROM dim_model WHERE id IN (:hev_id,:phev_id)', **params)
    if {r['model_name']:r['energy_type'] for r in actual} != {n:p['energy_type'] for n,p in PROFILES.items()}:
        raise ValueError('高山能源分类回读不一致')
    for table, name_field, clause in [('dim_energy_vehicle','model_name',''),
                                     ('dim_city_model','source_model_name'," AND source='16888_city'")]:
        checked = _read(conn, f'''SELECT {name_field} AS name,energy_type,vehicle_type FROM {table}
            WHERE {name_field} IN (:hev,:phev){clause}''', **names)
        if len(checked) != 2 or any(r['energy_type'] != PROFILES[r['name']]['energy_type'] or r['vehicle_type'] != 'MPV' for r in checked):
            raise ValueError('高山能源或城市分类回读不一致')
    return {'profiles':PROFILES, 'model_ids':ids, 'reassigned_phev_facts':len(fixes), 'fact_rows':len(after),
            'fact_sales':sum(r['sales_volume'] for r in after), 'models_before':models,
            'energy_before':energy,'city_models_before':cities,'facts_before':facts,
            'reassigned_fact_ids':[r['id'] for r in fixes]}


def run(output):
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Verify destination permissions before any business mutation.
    with tempfile.TemporaryFile(dir=target.parent):
        pass
    from scheduler.monthly_publish import _publication_transaction
    from scheduler.workflow import State
    with State().lock('warehouse', timeout=120), _publication_transaction() as conn:
        report = repair(conn)
    try:
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    except OSError as exc:
        raise ReceiptWriteError(report) from exc
    return report
