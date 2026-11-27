import pytest


def row(name='Model', maker='Maker', sales='10', sid='1'):
    return dict(model_name=name, manufacturer=maker, sales_volume=sales, source_id=sid)


def test_blank_identity_is_audited_and_skipped_only_in_national_chain():
    from scheduler.sales_source_rules import select_rows
    good = row()
    dirty = row('', '', '200', '0')
    kept, excluded = select_rows([good, dirty], 'style')
    assert kept == [good] and excluded[0]['reason'] == 'nan_identity'
    assert select_rows([dirty], 'city') == ([dirty], [])


def test_h5_keeps_long_history_id_not_highest_sales():
    from scheduler.sales_source_rules import select_rows
    long = row('红旗H5', '一汽红旗', '2220', '127847')
    short = row('红旗H5', '一汽红旗', '7117', '129643')
    kept, excluded = select_rows([short, long], 'style')
    assert kept == [long] and excluded[0]['reason'] == 'h5_short_history_questionable'
    with pytest.raises(ValueError, match='H5'):
        select_rows([row('红旗H5', '一汽红旗', '1', ''), long], 'style')
    with pytest.raises(ValueError, match='H5'):
        select_rows([row('红旗H5', '一汽红旗', '1', '')], 'style')


def test_qin_only_keeps_largest_sales_and_does_not_hide_pagination_ties():
    from scheduler.sales_source_rules import select_rows
    high = row('秦新能源', '比亚迪', '3,029', 'a')
    low = row('秦新能源', '比亚迪', '1484', 'b')
    assert select_rows([low, high], 'style')[0] == [high]
    assert select_rows([high, high.copy()], 'style')[0] == [high, high]
    # Unrelated duplicates must still reach strict duplicate validation.
    other = row()
    assert select_rows([other, other.copy()], 'style')[0] == [other, other]
