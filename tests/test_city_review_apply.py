import json

import pytest


def test_reviews_reject_duplicate_identity(tmp_path):
    from scheduler.city_review_apply import load_reviews
    path = tmp_path / 'reviews.json'
    path.write_text(json.dumps([{'source_model_name': 'same', 'values': {}},
                                {'source_model_name': 'same', 'values': {}}]))
    with pytest.raises(ValueError):
        load_reviews(path)


def test_apply_cli_plan_never_imports_database(monkeypatch, tmp_path, capsys):
    from scheduler import runner
    from scheduler.cli import admin_commands
    path = tmp_path / 'receipt.json'
    monkeypatch.setattr(admin_commands, 'require_etl_account', lambda p: pytest.fail('plan accessed account'))
    assert runner.main(['--apply-city-model-reviews', str(path), '--plan']) == 0
    assert 'plan' in capsys.readouterr().out
    assert not path.exists()


def test_apply_rolls_back_batch_when_later_identity_collides(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from sqlalchemy import create_engine, text

    from pipelines.dim.city_model import REVIEW_FIELDS
    from scheduler import city_review_apply, monthly_publish, workflow

    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        fields = ','.join(f'{k} TEXT' for k in REVIEW_FIELDS)
        conn.execute(text(f'CREATE TABLE dim_city_model(id INTEGER PRIMARY KEY, source TEXT, source_model_name TEXT, review_status TEXT, model_code TEXT, {fields})'))
        for i, name in enumerate(('first', 'second', 'existing'), 1):
            conn.execute(text("INSERT INTO dim_city_model(id,source,source_model_name,review_status,model_code,brand_code,brand_name) VALUES (:id,'16888_city',:name,:status,:name,:code,:brand)"),
                         {'id': i, 'name': name, 'status': 'confirmed' if i == 3 else 'pending',
                          'code': 'COLLISION' if i == 3 else None, 'brand': 'Original' if i == 3 else None})
        for table in ('dwd_sales_monthly_city', 'dwd_sales_monthly_model'):
            conn.execute(text(f'CREATE TABLE {table}(sales_volume INTEGER)'))
            conn.execute(text(f'INSERT INTO {table} VALUES(17)'))

    @contextmanager
    def transaction():
        with engine.begin() as conn:
            yield conn

    class State:
        locked = False

        @contextmanager
        def lock(self, *args, **kwargs):
            self.locked = True
            try:
                yield
            finally:
                self.locked = False

        def service(self, *args):
            assert self.locked, 'queue publication must remain under warehouse lock'

        def export(self):
            pass

    monkeypatch.setattr(monthly_publish, '_publication_transaction', transaction)
    monkeypatch.setattr(workflow, 'State', State)
    values = dict(brand_code='B', brand_name='Brand', oem_code='O', oem_name='Maker',
                  energy_type='燃油', vehicle_type='SUV', production_type='domestic',
                  import_channel='unknown', evidence='Reviewed source URL')
    reviews = [{'source_model_name': 'first', 'values': values},
               {'source_model_name': 'second', 'values': {**values, 'brand_code': 'COLLISION'}}]
    with pytest.raises(ValueError, match='编码与名称冲突'):
        city_review_apply.run(reviews, tmp_path / 'receipt.json')
    with engine.connect() as conn:
        assert conn.execute(text("SELECT review_status FROM dim_city_model WHERE id=1")).scalar_one() == 'pending'
    report = city_review_apply.run(reviews[:1], tmp_path / 'receipt.json')
    assert report['committed'] and report['updated_count'] == 1
    assert report['facts_before'] == report['facts_after']
