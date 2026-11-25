"""Restore historical rollups from source-reconciled, preserved national facts.

OTHER is an explicit unresolved bucket, never an inferred company assignment.
"""
from datetime import date

from scheduler import monthly_publish as p


def restore():
    report = {'months': [], 'inserted_dwd_rows': 0, 'unresolved_company_rows': 0,
              'unresolved_company_sales': 0, 'unresolved_oems': {}, 'dws_rows': 0}
    with p._publication_transaction() as conn:
        months = sorted({str(r['stat_month'])[:7] for r in p._read(conn,
            "SELECT DISTINCT stat_month FROM ods_sales_monthly_model WHERE source='16888_style'")})
        companies = {r['id']: r['company_code'] for r in p._read(conn,
            'SELECT id,company_code FROM dim_company')}
        for period in months:
            month = date.fromisoformat(period + '-01')
            source = p._source(conn, 'style', month)
            dimensions = p._dimensions(conn, 'style', month)
            dwd = p._dwd(conn, 'style', period)
            expected = {}
            for row in source:
                matches = dimensions[row['id']]
                if len(matches) != 1 or matches[0]['oem_id'] is None:
                    raise p.ReconciliationError(f'{period}: ambiguous source OEM: {row["model_name"]}')
                key = (p._name(row['model_name']).casefold(), matches[0]['oem_id'])
                if key in expected:
                    raise p.ReconciliationError(f'{period}: duplicate canonical source key {key}')
                expected[key] = row
            actual = p._index(dwd, lambda r: (p._name(r['model_name']).casefold(), r['oem_id']), 'historical DWD')
            if actual.keys() - expected.keys():
                raise p.ReconciliationError(f'{period}: DWD keys absent from ODS')
            missing = [r for key, r in expected.items() if key not in actual]
            if missing:
                p._derive_models(conn, missing, dimensions)
                dimensions = p._dimensions(conn, 'style', month)
                additions, required, _ = p._candidates('style', period, missing, dimensions)
                if required:
                    raise p.ManualRequired(required)
                # Existing facts keep their reviewed historical ownership.
                p._write_dwd(conn, 'style', period, source,
                    [{**r, 'id': expected[key]['id']} for key, r in actual.items()] + additions)
                report['inserted_dwd_rows'] += len(additions)
                dwd = p._dwd(conn, 'style', period)
                actual = p._index(dwd, lambda r: (p._name(r['model_name']).casefold(), r['oem_id']), 'repaired DWD')
            if actual.keys() != expected.keys():
                raise p.ReconciliationError(f'{period}: source/DWD key set mismatch')
            for key, row in actual.items():
                if row['sales_volume'] != expected[key]['sales_volume'] or row['source'] != '16888_style':
                    raise p.ReconciliationError(f'{period}: source/DWD sales mismatch {key}')
                if any(row[field] is None for field in ('model_id', 'oem_id', 'brand_id', 'company_id')):
                    raise p.ReconciliationError(f'{period}: null historical dimensional key {key}')
                if companies.get(row['company_id']) == 'OTHER':
                    report['unresolved_company_rows'] += 1
                    report['unresolved_company_sales'] += row['sales_volume']
                    name = row['manufacturer_name']
                    report['unresolved_oems'][name] = report['unresolved_oems'].get(name, 0) + row['sales_volume']
            report['dws_rows'] += sum(p._write_groups(conn, group, month, dwd) for group in ('company', 'brand'))
            report['months'].append(period)
        p._refresh_lifecycle(conn)
    return report
