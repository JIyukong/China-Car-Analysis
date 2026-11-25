import pytest

from test_monthly_publish import warehouse, add_source


def test_history_restores_groups_and_preserves_unknown_company(warehouse):
    from scheduler.national_history import restore
    publish, execute = warehouse
    add_source(execute, '2017-01', 9, 'Model')
    publish.publish_month('style', '2017-01')
    execute("UPDATE dim_company SET company_code='OTHER' WHERE id=1")
    execute('UPDATE dws_sales_monthly_company SET is_current=0')
    execute('UPDATE dws_sales_monthly_brand SET is_current=0')
    report = restore()
    assert report['months'] == ['2017-01']
    assert report['unresolved_company_rows'] == 1
    assert execute('SELECT total_sales,is_current FROM dws_sales_monthly_company') == [(9, 1)]
    assert execute('SELECT total_sales,is_current FROM dws_sales_monthly_brand') == [(9, 1)]
    assert restore()['inserted_dwd_rows'] == 0


def test_history_rejects_sales_drift_and_rolls_back(warehouse):
    from scheduler.national_history import restore
    publish, execute = warehouse
    add_source(execute, '2017-01', 9, 'Model')
    publish.publish_month('style', '2017-01')
    execute('UPDATE dws_sales_monthly_company SET is_current=0')
    execute('UPDATE ods_sales_monthly_model SET sales_volume=10')
    with pytest.raises(publish.ReconciliationError):
        restore()
    assert execute('SELECT is_current FROM dws_sales_monthly_company') == [(0,)]


def test_history_derives_only_missing_model_from_existing_bridge(warehouse):
    from scheduler.national_history import restore
    publish, execute = warehouse
    add_source(execute, '2017-01', 9, 'Model')
    publish.publish_month('style', '2017-01')
    original = execute('SELECT id,model_id,company_id FROM dwd_sales_monthly_model')
    add_source(execute, '2017-01', 3, 'Missing')
    report = restore()
    assert report['inserted_dwd_rows'] == 1
    assert execute("SELECT id,model_id,company_id FROM dwd_sales_monthly_model WHERE model_name='Model'") == original
    assert execute("SELECT brand_id,oem_id FROM dim_model WHERE model_name='Missing'") == [(1, 1)]
    assert execute('SELECT total_sales FROM dws_sales_monthly_company') == [(12,)]
