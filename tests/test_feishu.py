import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from pipelines.dim import manual_refresh
from scheduler import feishu


def _record(kind, key, values):
    fields = feishu.ticket_fields(kind, key, tuple(values), ['2026-08', '2026-07'])
    fields[feishu.STATUS] = {'text': feishu.SUBMITTED}
    fields[feishu.INPUT] = [{'text': json.dumps(values, ensure_ascii=False), 'type': 'text'}]
    return {'record_id': 'rec1', 'fields': fields}


def test_list_feishu_records_uses_token_and_follows_pages(monkeypatch):
    calls = []
    responses = iter([
        {'code': 0, 'tenant_access_token': 'tenant-token', 'expire': 7200},
        {'code': 0, 'data': {'items': [{'record_id': 'rec1', 'fields': {}}], 'has_more': True, 'page_token': 'a+b'}},
        {'code': 0, 'data': {'items': [{'record_id': 'rec2', 'fields': {}}], 'has_more': False}},
    ])

    def open_response(request, timeout):
        calls.append((request, timeout))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(next(responses)).encode()
        return response

    monkeypatch.setattr(feishu, 'urlopen', open_response)
    client = feishu.FeishuClient('app-id', 'secret', 'base', 'table')
    assert [record['record_id'] for record in client.records()] == ['rec1', 'rec2']
    assert [request.get_method() for request, _ in calls] == ['POST', 'GET', 'GET']
    assert all(timeout > 0 for _, timeout in calls)
    assert json.loads(calls[0][0].data) == {'app_id': 'app-id', 'app_secret': 'secret'}
    assert calls[2][0].get_header('Authorization') == 'Bearer tenant-token'
    assert 'page_token=a%2Bb' in calls[2][0].full_url


def test_feishu_rejects_business_error_without_echoing_response(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"code": 123, "msg":"sensitive-content"}'
    monkeypatch.setattr(feishu, 'urlopen', lambda request, timeout: response)
    client = feishu.FeishuClient('app-id', 'secret', 'base', 'table')
    with pytest.raises(feishu.FeishuError, match='123') as exc:
        client.records()
    assert 'sensitive-content' not in str(exc.value)


def test_parse_energy_rejects_wrong_record_and_placeholder():
    record = _record('energy', '甲', {'energy_type': '新能源', 'vehicle_type': 'SUV'})
    submission = feishu.submission(record, kind='energy', key='甲')
    assert submission.values == {'energy_type': '新能源', 'vehicle_type': 'SUV'}
    assert record['fields'][feishu.PERIODS] == '2026-07, 2026-08'
    with pytest.raises(ValueError, match='任务'):
        feishu.submission(record, kind='energy', key='乙')
    record['fields'][feishu.INPUT] = '{"energy_type":"占位","vehicle_type":"SUV"}'
    with pytest.raises(ValueError, match='energy_type'):
        feishu.submission(record, kind='energy', key='甲')


@pytest.mark.parametrize('percentage', ['NaN', '101%', '-1', '10%%', True, '10.001%'])
def test_ads_rejects_invalid_percentage(percentage):
    record = _record('ads', '甲', {
        'ads_proportion': percentage, 'ads_supplier': '供应商', 'ads_supplier_mode': '模式', 'listed_at': '2026-07-01',
    })
    with pytest.raises(ValueError, match='ads_proportion'):
        feishu.submission(record, kind='ads', key='甲')


def test_ads_not_applicable_must_be_explicit_and_skips_database(monkeypatch):
    engine = MagicMock()
    monkeypatch.setattr(feishu, 'engine', engine)
    record = _record('ads', '甲', {'not_applicable': True})
    value = feishu.submission(record, kind='ads', key='甲')
    assert value.not_applicable
    feishu.apply_submission(value)
    engine.begin.assert_not_called()
    record['fields'][feishu.INPUT] = '{"not_applicable":true,"ads_supplier":"供应商"}'
    with pytest.raises(ValueError, match='字段'):
        feishu.submission(record, kind='ads', key='甲')


def test_subtype_and_area_cannot_override_tracked_key():
    subtype = _record('subtype', '紧凑型SUV', {'subtype_code': '11', 'type_name': 'SUV'})
    assert feishu.submission(subtype, kind='subtype', key='紧凑型SUV').values == {
        'subtype_code': '11', 'type_name': 'SUV',
    }
    area = _record('area', '宁波', {
        'city_code': '330200', 'city_name': '宁波市', 'city_short_name': '杭州', 'city_level': '地级市',
        'province_code': '330000', 'province_name': '浙江省', 'province_short_name': '浙江',
        'province_type': '省', 'region_code': 'EC', 'region_name': '华东',
    })
    with pytest.raises(ValueError, match='city_short_name'):
        feishu.submission(area, kind='area', key='宁波')


def test_only_actionable_ownership_can_be_applied():
    record = _record('ownership', '某车厂', {'brand_code': 'SOME_BRAND'})
    assert feishu.submission(record, kind='ownership', key='某车厂', expected_fields=['brand_code']).values == {
        'brand_code': 'SOME_BRAND',
    }
    with pytest.raises(ValueError, match='管理员'):
        feishu.submission(record, kind='ownership', key='某车厂', expected_fields=['model_id', 'oem_id'])


def test_company_only_submission_resolves_oem_and_current_bridge_placeholders(monkeypatch):
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(feishu, 'engine', engine)
    record = _record('ownership', '某车厂', {'company_code': 'COMPANY'})
    change = feishu.submission(record, kind='ownership', key='某车厂', expected_fields=['company_code'])
    oem = {'id': 2, 'oem_name': '某车厂', 'company_id': 3, 'company_code': 'OTHER'}
    company = {'id': 4, 'company_name': '某公司'}
    bridge = {'brand_id': 5, 'company_id': 3, 'is_current': 1, 'open_ended': 1}
    result = conn.execute.return_value
    result.mappings.return_value.one_or_none.side_effect = [oem, company]
    result.mappings.return_value.all.return_value = [bridge]
    result.rowcount = 1
    result.scalar_one.return_value = 1

    feishu.apply_submission(change)

    writes = [(str(call.args[0]), call.args[1]) for call in conn.execute.call_args_list
              if str(call.args[0]).lstrip().startswith(('UPDATE', 'INSERT'))]
    assert len(writes) == 2
    assert 'UPDATE dim_oem' in writes[0][0]
    assert 'UPDATE bridge_brand_oem' in writes[1][0]
    assert 'brand_id' not in writes[1][0]
    assert all(params['company_id'] == 4 and params['oem_id'] == 2 for _, params in writes)

    conn.execute.reset_mock()
    result.mappings.return_value.one_or_none.side_effect = [{**oem, 'company_code': 'CONFIRMED'}, company]
    with pytest.raises(ValueError, match='冲突'):
        feishu.apply_submission(change)
    assert all('UPDATE dim_oem' not in str(call.args[0]) for call in conn.execute.call_args_list)

    with pytest.raises(ValueError, match='已确认的公司'):
        feishu.submission(_record('ownership', '某车厂', {'company_code': 'OTHER'}),
                          kind='ownership', key='某车厂', expected_fields=['company_code'])


def test_energy_checks_exact_model_and_postcheck_in_transaction(monkeypatch):
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    model = {'id': 4, 'model_name': '甲', 'energy_type': '占位', 'vehicle_type': 'SUV'}
    conn.execute.return_value.mappings.return_value.all.return_value = [model]
    conn.execute.return_value.scalar_one.return_value = 1
    upsert = MagicMock()
    monkeypatch.setattr(manual_refresh, 'upsert_dimension', upsert)
    monkeypatch.setattr(feishu, 'engine', engine)
    value = feishu.submission(_record('energy', '甲', {'energy_type': '新能源', 'vehicle_type': 'SUV'}), kind='energy', key='甲')
    feishu.apply_submission(value)
    assert upsert.call_args.args == (conn, 'dim_energy_vehicle', [
        {'model_name': '甲', 'energy_type': '新能源', 'vehicle_type': 'SUV', 'source_file': 'feishu-bitable'},
    ])
    conn.execute.return_value.mappings.return_value.all.return_value = [model, {**model, 'id': 5}]
    upsert.reset_mock()
    with pytest.raises(ValueError, match='唯一'):
        feishu.apply_submission(value)
    upsert.assert_not_called()


def test_area_refuses_reusing_another_city_code(monkeypatch):
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = [
        {'city_code': '330200', 'city_short_name': '杭州'},
    ]
    upsert = MagicMock()
    monkeypatch.setattr(manual_refresh, 'upsert_dimension', upsert)
    with pytest.raises(ValueError, match='冲突'):
        manual_refresh.apply_feishu_dimension(conn, 'area', '宁波', {'city_code': '330200', 'city_short_name': '宁波'})
    upsert.assert_not_called()


def test_subtype_refuses_reusing_existing_code():
    conn = MagicMock()
    conn.execute.return_value.scalar_one_or_none.return_value = 4
    conn.execute.return_value.mappings.return_value.all.return_value = [
        {'subtype_code': '2', 'subtype_name': 'SUV', 'vehicle_type_id': 4},
    ]
    with pytest.raises(ValueError, match='冲突'):
        manual_refresh.apply_feishu_dimension(conn, 'subtype', '紧凑型SUV', {'subtype_code': '2', 'type_name': 'SUV'})
    assert all('INSERT' not in str(call.args[0]) for call in conn.execute.call_args_list)


def test_energy_ticket_prefills_only_missing_fields_and_keeps_existing_type(monkeypatch):
    template = feishu.ticket_fields('energy', '甲', ['energy_type'], ['2026-07'])
    assert json.loads(template[feishu.INPUT]) == {'energy_type': ''}
    with pytest.raises(ValueError, match='不支持'):
        feishu.submission(
            _record('energy', '甲', {'energy_type': '新能源', 'vehicle_type': 'SUV'}),
            kind='energy', key='甲', expected_fields=['energy_type'],
        )

    value = feishu.submission(
        _record('energy', '甲', {'energy_type': '新能源'}), kind='energy', key='甲', expected_fields=['energy_type'],
    )
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = [
        {'id': 4, 'model_name': '甲', 'energy_type': '占位', 'vehicle_type': 'SUV',
         'stored_energy_type': '占位', 'stored_vehicle_type': 'SUV'},
    ]
    conn.execute.return_value.scalar_one.return_value = 1
    upsert = MagicMock()
    monkeypatch.setattr(manual_refresh, 'upsert_dimension', upsert)
    manual_refresh.apply_feishu_dimension(conn, 'energy', '甲', value.values)
    assert upsert.call_args.args[2][0]['vehicle_type'] == 'SUV'


def test_ads_partial_submission_keeps_authoritative_values_and_requires_fresh_rows_to_be_complete():
    value = feishu.submission(
        _record('ads', '甲', {'ads_proportion': '20%'}), kind='ads', key='甲', expected_fields=['ads_proportion'],
    )
    assert value.values == {'ads_proportion_raw': '20%', 'ads_proportion_pct': Decimal(20)}
    conn = MagicMock()
    mapping = conn.execute.return_value.mappings.return_value
    mapping.all.side_effect = [
        [{'id': 4, 'model_name': '甲', 'energy_type': '新能源', 'vehicle_type': 'SUV'}],
        [{'id': 8, 'ads_supplier': '供应商', 'ads_supplier_mode': '模式', 'effective_start_date': date(2026, 7, 1)}],
    ]
    mapping.one_or_none.return_value = {
        'ads_supplier': '供应商', 'ads_supplier_mode': '模式', 'listed_at_raw': '2026年7月',
        'listed_at_date': date(2026, 7, 1), 'listed_at_granularity': 'month', 'model_name': '甲',
    }
    conn.execute.return_value.scalar_one.return_value = 1
    manual_refresh.apply_feishu_dimension(conn, 'ads', '甲', value.values)
    write = next(call.args[1] for call in conn.execute.call_args_list if 'INSERT INTO dim_model_ads_manual' in str(call.args[0]))
    assert (write['ads_supplier'], write['listed_at_raw'], write['ads_proportion_pct']) == ('供应商', '2026年7月', Decimal(20))

    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = [{'id': 4, 'model_name': '甲'}]
    conn.execute.return_value.mappings.return_value.one_or_none.return_value = None
    with pytest.raises(ValueError, match='现有 ADS'):
        manual_refresh.apply_feishu_dimension(conn, 'ads', '甲', value.values)
    assert all('INSERT' not in str(call.args[0]) for call in conn.execute.call_args_list)
