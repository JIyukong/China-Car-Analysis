import pytest

from pipelines.dim.manual_refresh import apply_ads


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return None

    def scalar_one(self):
        return 1


class Connection:
    def __init__(self, models):
        self.models = models
        self.calls = []

    def execute(self, statement, values):
        sql = str(statement)
        self.calls.append((sql, dict(values)))
        return Result(self.models if 'SELECT m.id, m.model_name FROM dim_model' in sql else ())


def test_ads_verified_typo_resolves_canonical_model_preserving_source_key():
    conn = Connection([{'id': 3271, 'model_name': '奕派M8'}])
    values = dict.fromkeys(('ads_proportion_raw', 'ads_proportion_pct', 'ads_supplier',
                           'ads_supplier_mode', 'listed_at_raw', 'listed_at_date',
                           'listed_at_granularity'))
    apply_ads(conn, '亦派M8', values)
    assert conn.calls[0][1]['key'] == '奕派M8'
    inserted = next(v for sql, v in conn.calls if 'INSERT INTO dim_model_ads_manual' in sql)
    assert inserted['model_name'] == '亦派M8'
    assert inserted['model_id'] == 3271


def test_ads_missing_canonical_model_stays_unmatched():
    conn = Connection([])
    values = dict.fromkeys(('ads_proportion_raw', 'ads_proportion_pct', 'ads_supplier',
                           'ads_supplier_mode', 'listed_at_raw', 'listed_at_date',
                           'listed_at_granularity'))
    apply_ads(conn, '亦境X9', values)
    assert conn.calls[0][1]['key'] == '奕境X9'
    inserted = next(v for sql, v in conn.calls if 'INSERT INTO dim_model_ads_manual' in sql)
    assert inserted['model_id'] is None
    assert inserted['missing_reason'] == 'DIM_MODEL_NOT_FOUND'


def test_ads_verified_alias_does_not_choose_between_duplicate_models():
    conn = Connection([{'id': 1, 'model_name': '奕派M8'},
                       {'id': 2, 'model_name': '奕派M8'}])
    with pytest.raises(ValueError, match='唯一车型'):
        apply_ads(conn, '亦派M8', {})
    assert len(conn.calls) == 1


def test_ads_unverified_spelling_is_not_fuzzy_matched():
    conn = Connection([])
    values = dict.fromkeys(('ads_proportion_raw', 'ads_proportion_pct', 'ads_supplier',
                           'ads_supplier_mode', 'listed_at_raw', 'listed_at_date',
                           'listed_at_granularity'))
    apply_ads(conn, '奕派M9', values)
    assert conn.calls[0][1]['key'] == '奕派M9'
