"""Incrementally reconcile existing city identities without republishing sales."""

import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pipelines.dim.city_model import REVIEW_FIELDS, discover_city_models, apply_city_model_review, validate_city_model_review
from scheduler.monthly_publish import _publication_transaction
from scheduler.ticket_store import TicketRepository


def run(state):
    with state.lock('warehouse', timeout=10):
        with _publication_transaction() as conn:
            rows = [dict(row) for row in conn.execute(text(
                "SELECT source_model_name AS model_name FROM dim_city_model WHERE source='16888_city'"
            )).mappings()]
            period = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
            models = discover_city_models(conn, period, rows)
            review_file = Path(__file__).resolve().parents[1] / 'data/manual_dimensions/city_model_reviews.json'
            reviews = json.loads(review_file.read_text(encoding='utf-8')) if review_file.exists() else []
            for review in reviews:
                name, values = review['source_model_name'], review['values']
                validate_city_model_review(name, values)
                if name in models and models[name]['review_status'] != 'confirmed':
                    apply_city_model_review(conn, name, values)
                    models[name].update(values, review_status='confirmed')
        # Persist a separate dimension queue: do not change source acceptance receipts.
        research_file = Path(__file__).resolve().parents[1] / 'data/manual_dimensions/city_model_research.json'
        research = json.loads(research_file.read_text(encoding='utf-8')) if research_file.exists() else {}
        gaps = [dict(kind='city_model', key=name, fields=list(REVIEW_FIELDS),
                     periods=[period], contexts={period: json.dumps(model, ensure_ascii=False, default=str) + '\n' + research.get(name, '')})
                for name, model in models.items() if model['review_status'] != 'confirmed']
        with state.transaction() as conn:
            conn.execute("INSERT INTO service_status(task,status,updated_at,message) VALUES ('city_model_gaps','ok',?,?) "
                         "ON CONFLICT(task) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,message=excluded.message",
                         (datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), json.dumps(gaps, ensure_ascii=False)))
        for name, model in models.items():
            if model['review_status'] == 'confirmed':
                TicketRepository(state).mark_auto_confirmed(name, model.get('evidence', ''))
        print(json.dumps({'city_models': len(models), 'confirmed': len(models)-len(gaps), 'unresolved': len(gaps)}))
    return True
