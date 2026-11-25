"""Offline, evidence-qualified city model inventory. Never imports a DB engine."""

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from scheduler.monthly_source import _validated_csv
from scheduler.runtime import SkipTask, business_period


def _profile(path, chain):
    period = path.stem.removeprefix(chain + '_')
    meta = dict(path=str(path.resolve()), chain=chain, period=period,
                snapshot_validated=False, duplicate_key_rows=0, invalid_rows=0,
                local_quality_valid=False, row_count=0, csv_sha256=None,
                distinct_models=0, import_marked_models=0, positive_sales=None)
    try:
        business_period(chain, period)
        raw = path.read_bytes()
    except (ValueError, OSError) as exc:
        meta['validation_error'] = str(exc)
        return meta, []
    try:
        raw = _validated_csv(path, path.with_suffix('.manifest.json'), chain, period)
        meta['snapshot_validated'] = True
    except (ValueError, OSError, UnicodeError, csv.Error, SkipTask) as exc:
        meta['validation_error'] = str(exc)
    meta['csv_sha256'] = hashlib.sha256(raw).hexdigest()
    other = 'city' if chain == 'city' else 'manufacturer'
    records, seen, errors = [], set(), []
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        header = reader.fieldnames or []
        if not {'month', 'model_name', other, 'sales_volume'}.issubset(header) or len(set(header)) != len(header):
            raise ValueError('必要字段缺失或表头重复')
        for row in reader:
            meta['row_count'] += 1
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError('列数异常')
                name, label = row['model_name'].strip(), row[other].strip()
                sales = row['sales_volume'].strip()
                if row['month'].strip() != period or not name or not label:
                    raise ValueError('月份或业务键无效')
                if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', sales):
                    raise ValueError('销量无效')
                sales = int(sales.replace(',', ''))
                if sales > 2**32 - 1 or len(name) > 128 or len(label) > (64 if chain == 'city' else 128):
                    raise ValueError('字段超出ODS范围')
                key = (name.casefold(), label.casefold())
                if key in seen:
                    meta['duplicate_key_rows'] += 1
                seen.add(key)
                records.append(dict(model_name=name, label=label, sales_volume=sales))
            except ValueError as exc:
                meta['invalid_rows'] += 1
                if len(errors) < 3:
                    errors.append(f'第 {reader.line_num} 行：{exc}')
    except (ValueError, UnicodeError, csv.Error) as exc:
        meta['invalid_rows'] += 1
        errors.append(str(exc))
    if errors:
        meta['row_errors'] = errors
    meta['local_quality_valid'] = not (meta['invalid_rows'] or meta['duplicate_key_rows'])
    # A local collation conflict must not count as an acceptable snapshot.
    if not meta['local_quality_valid']:
        meta['snapshot_validated'] = False
    meta['distinct_models'] = len({row['model_name'] for row in records})
    meta['import_marked_models'] = len({row['model_name'] for row in records if '进口' in row['model_name']})
    meta['positive_sales'] = sum(row['sales_volume'] for row in records) if meta['local_quality_valid'] else None
    return meta, records


def preview(city_dir, style_dir, *, period=None):
    """Profile local CSVs; verified snapshot evidence is not an ODS acceptance receipt."""
    if period is not None:
        business_period('city', period)
    city_files = sorted(Path(city_dir).glob(f'city_{period or "*"}.csv'))
    if not city_files:
        raise ValueError('没有找到指定范围的本地城市 CSV')
    files, models, national = [], {}, defaultdict(list)
    for path in city_files:
        meta, records = _profile(path, 'city')
        files.append(meta)
        # Broken local files remain in the evidence list but cannot supply totals or identities.
        if not meta['local_quality_valid']:
            continue
        for row in records:
            name = row['model_name']
            item = models.setdefault(name, dict(source_model_name=name, months=set(),
                validated_city_months=set(), observed_csv_rows=0, observed_sales=0,
                import_marker='进口' in name))
            item['months'].add(meta['period'])
            if meta['snapshot_validated']:
                item['validated_city_months'].add(meta['period'])
            item['observed_csv_rows'] += 1
            item['observed_sales'] += row['sales_volume']
    for path in sorted(Path(style_dir).glob('style_*.csv')):
        meta, records = _profile(path, 'style')
        files.append(meta)
        if meta['local_quality_valid']:
            for row in records:
                national[row['model_name']].append((meta['period'], row['label'], meta['snapshot_validated']))
    result = []
    for name, item in sorted(models.items()):
        last = max(item['months'])
        validated, unverified = set(), set()
        for month, oem, accepted in national[name]:
            if month <= last:
                (validated if accepted else unverified).add(oem)
        unmarked = re.sub(r'[（(]\s*进口\s*[）)]', '', name).strip()
        peer = models.get(unmarked) if unmarked != name else None
        result.append({**item, 'months': sorted(item['months']),
            'validated_city_months': sorted(item['validated_city_months']),
            'validated_national_oems': sorted(validated),
            'unverified_national_oems': sorted(unverified),
            'national_oem_conflict': len(validated | unverified) > 1,
            'has_unmarked_peer': peer is not None,
            'same_month_unmarked_peer_months': sorted(item['months'] & peer['months']) if peer else [],
        })
    city_meta = [meta for meta in files if meta['chain'] == 'city']
    style_meta = [meta for meta in files if meta['chain'] == 'style']
    return dict(generated_at=datetime.now().astimezone().isoformat(),
        purpose='local_preview_only_no_database_writes_or_ownership_confirmation',
        caveats=[
            '本地CSV不等同于当前正式ODS；manifest校验通过也不代表正式库已接收。',
            '无manifest或校验失败的全国候选仅供排查，不可作为已确认归属。',
            '未在本地全国文件发现候选，不等同于该车型不存在于全国ODS。',
            '累计销量跨多个本地月份，只用于审核排序，不能与单月全国销量相加。',
            '存在重复键或非法行的文件不参与车型清单和销量汇总，问题保留在files中。',
        ],
        summary=dict(city_files=len(city_meta), style_files=len(style_meta),
            validated_city_files=sum(meta['snapshot_validated'] for meta in city_meta),
            validated_style_files=sum(meta['snapshot_validated'] for meta in style_meta),
            unverified_style_files=sum(not meta['snapshot_validated'] for meta in style_meta),
            observed_city_models=len(result),
            observed_import_marked_models=sum(item['import_marker'] for item in result),
            observed_models_with_national_conflicts=sum(item['national_oem_conflict'] for item in result),
            observed_models_without_local_national_candidate=sum(not (item['validated_national_oems'] or item['unverified_national_oems']) for item in result),
            observed_positive_city_sales=sum(meta['positive_sales'] for meta in city_meta) if all(meta['positive_sales'] is not None for meta in city_meta) else None,
        ), files=files, models=result)


def write_preview(report, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['source_model_name', 'months', 'validated_city_months', 'observed_csv_rows',
              'observed_sales', 'import_marker', 'validated_national_oems', 'unverified_national_oems',
              'national_oem_conflict', 'has_unmarked_peer', 'same_month_unmarked_peer_months']
    with (output / 'models.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report['models']:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value for key, value in row.items()})
    return output.resolve()
