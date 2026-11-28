"""Apply explicit city dimension reviews only; never republish or message."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def load_reviews(path):
    from pipelines.dim.city_model import validate_city_model_review

    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    names = [r['source_model_name'] for r in rows]
    if len(names) != len(set(names)):
        raise ValueError('城市车型审核清单包含重复身份')
    for row in rows:
        validate_city_model_review(row['source_model_name'], row['values'])
    return rows


def run(reviews, output):
    from sqlalchemy import text

    from pipelines.dim.city_model import REVIEW_FIELDS, apply_city_model_review
    from scheduler.monthly_publish import _publication_transaction
    from scheduler.workflow import State

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Check receipt destination before committing any warehouse change.
    with target.open('a', encoding='utf-8'):
        pass
    report = {'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'committed': False, 'updated': [], 'already_confirmed': [], 'absent': []}
    state = State()
    with state.lock('warehouse', timeout=120):
        with _publication_transaction() as conn:
            before = {r['source_model_name']: dict(r) for r in conn.execute(text(
                "SELECT * FROM dim_city_model WHERE source='16888_city'")).mappings()}
            def totals():
                return {table: dict(conn.execute(text(
                    f'SELECT COUNT(*) AS rows_count, SUM(sales_volume) AS sales FROM {table}'
                )).mappings().one()) for table in ('dwd_sales_monthly_city', 'dwd_sales_monthly_model')}
            report['facts_before'] = totals()
            for review in reviews:
                name, values = review['source_model_name'], review['values']
                old = before.get(name)
                if old is None:
                    report['absent'].append(name)
                    continue
                if old['review_status'] == 'confirmed':
                    if any(old[k] != values[k] for k in REVIEW_FIELDS):
                        raise ValueError('拒绝覆盖已确认且与清单不同的城市车型: ' + name)
                    report['already_confirmed'].append(name)
                    continue
                apply_city_model_review(conn, name, values)
                after = dict(conn.execute(text('SELECT * FROM dim_city_model WHERE id=:id'),
                                          {'id': old['id']}).mappings().one())
                if any(after[k] != values[k] for k in REVIEW_FIELDS) or after['review_status'] != 'confirmed':
                    raise ValueError('城市车型回读不一致: ' + name)
                protected = set(old) - set(REVIEW_FIELDS) - {'review_status', 'updated_at'}
                if any(old[k] != after[k] for k in protected):
                    raise ValueError('城市车型身份或生命周期发生变化: ' + name)
                report['updated'].append({'name': name, 'before': old, 'after': after})
            report['facts_after'] = totals()
            if report['facts_after'] != report['facts_before']:
                raise ValueError('销量事实总量发生变化，拒绝提交')
            remaining = [dict(r) for r in conn.execute(text(
                "SELECT * FROM dim_city_model WHERE source='16888_city' AND review_status<>'confirmed'"
            )).mappings()]
            period = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
            gaps = [dict(kind='city_model', key=r['source_model_name'], fields=list(REVIEW_FIELDS),
                         periods=[period], contexts={period: json.dumps(r, ensure_ascii=False, default=str)})
                    for r in remaining]
            report['remaining_count'] = len(gaps)
        report['committed'] = True
        try:
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
        except OSError as exc:
            raise RuntimeError('城市维表已提交，但回执写入失败；勿误认为事务回滚') from exc
        state.service('city_model_gaps', 'ok', json.dumps(gaps, ensure_ascii=False))
        state.export()
    return {**{k: v for k, v in report.items() if k != 'updated'}, 'updated_count': len(report['updated'])}
