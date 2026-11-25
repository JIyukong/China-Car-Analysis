"""Republish retained city ODS without accepting a revised external source.

Legacy facts are preserved. Source revision validation and batch state are
intentionally outside this warehouse restoration operation.
"""
import json
from datetime import date
from pathlib import Path

from scheduler import monthly_publish as p


def _key(row):
    return p._name(row['model_name_raw']).casefold(), p._name(row['city_name_raw']).casefold()


def restore_month(period):
    from scheduler.city_publish import publish_city, area_ids

    month = date.fromisoformat(period + '-01')
    with p._publication_transaction() as conn:
        source = p._source(conn, 'city', month, positive_only=False)
        expected = p._index(source, _key, 'retained city ODS')
        areas = area_ids(conn)
        subtypes = {r['subtype_name'] for r in p._read(conn, '''
            SELECT st.subtype_name FROM dim_vehicle_subtype st
            JOIN dim_vehicle_type vt ON vt.id=st.vehicle_type_id''')}
        quarantine, eligible = [], []
        for row in source:
            reasons = []
            if row['sales_volume'] > 0:
                if p._name(row['city_name_raw']) not in areas:
                    reasons.append('area_not_uniquely_mapped')
                if p._name(row['level_raw']) not in subtypes:
                    reasons.append('subtype_not_mapped')
            if reasons:
                quarantine.append({'model_name': row['model_name_raw'], 'city_name': row['city_name_raw'],
                                   'sales_volume': row['sales_volume'], 'reasons': reasons})
            else:
                eligible.append(row)
        positive = {_key(r): r for r in eligible if r['sales_volume'] > 0}
        legacy = p._dwd(conn, 'city', period)
        existing = p._dwd(conn, 'city', period, wide_city=True)
        for label, rows in (('legacy', legacy), ('independent', existing)):
            stored = p._index(rows, _key, label)
            for key, row in stored.items():
                if (row['source'] != '16888_city' or key not in expected
                        or row['sales_volume'] != expected[key]['sales_volume']):
                    raise p.ReconciliationError(f'{period}: {label} fact differs from retained ODS: {key}')
        orphans = p._read(conn, '''SELECT COUNT(*) n FROM dwd_sales_monthly_model_city d
            LEFT JOIN dim_model m ON m.id=d.model_id
            WHERE d.stat_month=:period AND m.id IS NULL''', period=period)[0]['n']
        if not positive:
            raise p.ReconciliationError(f'{period}: no positive facts have valid dimensional mapping')
        facts, gaps = publish_city(conn, period, eligible)
        if facts is None:
            # Roll back discovered dimensions too if area/type mapping is incomplete.
            raise p.ManualRequired(gaps)
        actual = p._index(p._dwd(conn, 'city', period, wide_city=True), _key, 'restored city DWD')
        if (actual.keys() != positive.keys() or any(
                row['sales_volume'] != positive[key]['sales_volume'] for key, row in actual.items())):
            raise p.ReconciliationError(f'{period}: retained ODS/city DWD key or sales readback differs')
        return {'period': period, 'warehouse_rows': len(actual),
                'complete': not quarantine, 'quarantined_rows': quarantine,
                'quarantined_sales': sum(r['sales_volume'] for r in quarantine),
                'inserted_rows': len(actual) - len(existing),
                'sales': sum(r['sales_volume'] for r in actual.values()),
                'legacy_rows_retained': len(legacy), 'legacy_orphan_rows_retained': int(orphans),
                'pending_dimension_reviews': len(gaps)}


def run(report_path, *, period=None):
    from scheduler.workflow import State

    with p.engine.connect() as conn:
        periods = sorted({str(r['stat_month'])[:7] for r in p._read(conn,
            "SELECT DISTINCT stat_month FROM ods_sales_monthly_model_city WHERE source='16888_city'")})
    if period is not None:
        date.fromisoformat(period + '-01')
        if period not in periods:
            raise ValueError(f'No retained city ODS for {period}')
        periods = [period]
    report = {'provenance': 'retained_ods_warehouse_restore', 'new_source_revision_accepted': False,
              'batch_status_modified': False, 'months': [], 'failures': [], 'complete': not periods}
    output = Path(report_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    state = State()
    for period in periods:
        try:
            with state.lock('warehouse', timeout=120):
                result = restore_month(period)
            report['months'].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            report['failures'].append({'period': period, 'error': str(exc),
                                       'gaps': getattr(exc, 'gaps', [])})
        report['complete'] = (len(report['months']) == len(periods)
                              and all(m['complete'] for m in report['months']))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
