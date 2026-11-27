"""Explicit national-source decisions approved by the user on 2026-11-27.

Raw snapshots remain intact. Excluded records are not market sales in this
research dataset; they are retained in the review receipt, never silently lost.
"""
import re
from collections import defaultdict

VERSION = 'reviewed_sales_v1'
NAN = {'', 'nan', 'none', 'null', 'n/a'}


def duplicate_source_ids(rows):
    """Check raw positive IDs before exclusions, including questionable records."""
    seen, duplicates = {}, set()
    for row in rows:
        identity = str(row.get('source_id', '')).strip()
        if not identity.isdigit() or int(identity) == 0:
            continue
        content = {k: v for k, v in row.items() if k not in ('rank', 'month')}
        if identity in seen:
            if seen[identity] != content:
                raise ValueError(f'来源编号重复且内容冲突: {identity}')
            duplicates.add(identity)
        else:
            seen[identity] = content
    return duplicates


def _sales(row):
    value = str(row.get('sales_volume', '')).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', value):
        raise ValueError('秦新能源销量无效，不能执行最大值规则')
    return int(value.replace(',', ''))


def select_rows(rows, chain):
    if chain != 'style':
        return list(rows), []
    groups = defaultdict(list)
    for row in rows:
        key = (str(row.get('model_name', '')).strip(), str(row.get('manufacturer', '')).strip())
        groups[key].append(row)
    excluded = {}
    for key, records in groups.items():
        if any(v.casefold() in NAN for v in key):
            for row in records:
                excluded[id(row)] = 'nan_identity'
        elif key == ('红旗H5', '一汽红旗'):
            ids = {str(row.get('source_id', '')).strip() for row in records}
            if not ids.issubset({'127847', '129643'}):
                raise ValueError('红旗H5缺少已核验来源编号，须重新抓取')
            for row in records:
                if str(row.get('source_id', '')).strip() == '129643':
                    excluded[id(row)] = 'h5_short_history_questionable'
        elif key == ('秦新能源', '比亚迪') and len(records) > 1:
            maximum = max(map(_sales, records))
            for row in records:
                if _sales(row) < maximum:
                    excluded[id(row)] = 'qin_lower_sales_questionable'
    return ([row for row in rows if id(row) not in excluded],
            [{'reason': excluded[id(row)], 'row': row} for row in rows if id(row) in excluded])
