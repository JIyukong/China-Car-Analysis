import pytest

from scheduler.sales_source_renames import canonical_key, rename_plan


def test_reviewed_model_and_manufacturer_resolve_together():
    assert canonical_key(('欧拉5 EV', '长城汽车')) == ('欧拉5', '长城新能源')
    assert canonical_key(('unreviewed', '长城汽车')) == ('unreviewed', '长城汽车')
    assert canonical_key(('e爱丽舍', '神龙汽车')) == ('e爱丽舍', '东风新能源')
    assert canonical_key(('红旗金葵花国耀', '一汽红旗')) == ('红旗国耀', '一汽红旗')
    assert canonical_key(('魏牌 蓝山新能源', '长城汽车')) == ('魏牌 蓝山', '长城汽车')
    assert canonical_key(('瑞虎9新能源', '奇瑞汽车')) == ('瑞虎9 C-DM', '奇瑞汽车')


def test_backlog_catalog_has_identity_and_multimonth_evidence():
    import json
    from pathlib import Path
    from scheduler.sales_source_renames import RENAMES
    path = Path(__file__).resolve().parents[1] / 'docs/evidence/sales-backlog-renames-2026-11-26.json'
    records = json.loads(path.read_text(encoding='utf-8'))['records']
    for row in records:
        assert RENAMES[tuple(row['old'])] == tuple(row['new'])
        assert row['model_url'].startswith('/s/')
        assert len({c['month'] for c in row['history_checks']}) >= 2
        assert all(c['old_sales'] == c['source_sales'] for c in row['history_checks'])


def test_plan_is_exact_and_preserves_unexplained_missing_keys():
    old = ('哈弗大狗', '长城汽车')
    new = ('长城大狗', '长城汽车')
    assert rename_plan({old: (1,)}, {new: (1,)}) == [(old, new)]
    assert rename_plan({('unknown', 'maker'): (1,)}, {new: (1,)}) == []


def test_old_and_new_existing_together_must_not_be_merged():
    old, new = ('哈弗大狗', '长城汽车'), ('长城大狗', '长城汽车')
    with pytest.raises(ValueError, match='并存'):
        rename_plan({old: (1,), new: (1,)}, {new: (1,)})


def test_both_labels_in_source_are_not_a_rename():
    old, new = ('哈弗大狗', '长城汽车'), ('长城大狗', '长城汽车')
    assert rename_plan({old: (1,)}, {old: (1,), new: (1,)}) == []


@pytest.mark.parametrize('unexplained', [False, True])
def test_ods_rekey_is_transactional_and_keeps_row_identity(monkeypatch, unexplained):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, text
    from scheduler import monthly_source as source

    db = create_engine('sqlite://')
    with db.begin() as conn:
        conn.execute(text('CREATE TABLE ods_sales_monthly_model (id INTEGER PRIMARY KEY, source TEXT, stat_month TEXT, model_name_raw TEXT, manufacturer_name_raw TEXT, sales_volume INTEGER, rank_no INTEGER, price_range_raw TEXT)'))
        conn.execute(text("INSERT INTO ods_sales_monthly_model VALUES (5,'16888_style','2026-07','哈弗大狗','长城汽车',10,1,NULL)"))
        if unexplained:
            conn.execute(text("INSERT INTO ods_sales_monthly_model VALUES (6,'16888_style','2026-07','Unknown','Maker',2,2,NULL)"))
    def read(conn, loader, chain, period, lock=False):
        return source._content(conn.execute(text('SELECT * FROM ods_sales_monthly_model')).mappings(), source._content_fields(chain))
    monkeypatch.setattr(source, '_ods_content', read)
    def upsert(rows, connection):
        for row in rows:
            connection.execute(text('UPDATE ods_sales_monthly_model SET sales_volume=:sales_volume WHERE model_name_raw=:model_name_raw'), row)
    loader = SimpleNamespace(engine=db, SOURCE_TAG='16888_style', parse_month=lambda x: x, upsert_rows=upsert)
    rows = [{'model_name_raw': '长城大狗', 'manufacturer_name_raw': '长城汽车', 'sales_volume': 12, 'rank_no': 1, 'price_range_raw': None}]
    if unexplained:
        with pytest.raises(ValueError, match='历史业务键'):
            source._reconcile_ods(loader, 'style', '2026-07', rows)
    else:
        source._reconcile_ods(loader, 'style', '2026-07', rows)
    with db.connect() as conn:
        result = conn.execute(text('SELECT id,model_name_raw,sales_volume FROM ods_sales_monthly_model WHERE id=5')).one()
        assert result == ((5, '哈弗大狗', 10) if unexplained else (5, '长城大狗', 12))
    db.dispose()
