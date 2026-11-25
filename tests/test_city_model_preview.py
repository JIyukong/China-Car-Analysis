import csv
import hashlib
import json

import pytest


def snapshot(directory, chain, period, rows, *, manifest=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{chain}_{period}.csv'
    other = 'manufacturer' if chain == 'style' else 'city'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['month', 'model_name', other, 'sales_volume'])
        writer.writerows((period, *row) for row in rows)
    if manifest:
        path.with_suffix('.manifest.json').write_text(json.dumps(dict(
            chain=chain, period=period, status='published', pagination_complete=True,
            source_total=len(rows), page_rows=[len(rows)],
            csv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )), encoding='utf-8')
    return path


def test_preview_preserves_import_identity_and_separates_unverified_sources(tmp_path):
    from pipelines.dim.city_model_preview import preview
    city, style = tmp_path / 'city', tmp_path / 'style'
    snapshot(city, 'city', '2026-01', [('Model', 'City', 8), ('Model(进口)', 'City', 2), ('Zero', 'City', 0)])
    snapshot(style, 'style', '2026-01', [('Model', 'Domestic OEM', 10)])
    snapshot(style, 'style', '2025-12', [('Model(进口)', 'Unverified OEM', 1)], manifest=False)
    report = preview(city, style)
    models = {r['source_model_name']: r for r in report['models']}
    assert set(models) == {'Model', 'Model(进口)', 'Zero'}
    assert models['Model(进口)']['has_unmarked_peer']
    assert models['Model(进口)']['validated_national_oems'] == []
    assert models['Model(进口)']['unverified_national_oems'] == ['Unverified OEM']
    assert report['summary']['validated_city_files'] == 1
    assert report['summary']['unverified_style_files'] == 1
    assert report['summary']['observed_positive_city_sales'] == 10


def test_preview_rejects_duplicate_keys_without_hiding_them(tmp_path):
    from pipelines.dim.city_model_preview import preview
    city, style = tmp_path / 'city', tmp_path / 'style'
    style.mkdir()
    snapshot(city, 'city', '2026-01', [('Model', 'City', 8), ('Model', 'City', 2)])
    report = preview(city, style)
    assert report['summary']['validated_city_files'] == 0
    assert report['files'][0]['duplicate_key_rows'] == 1
    assert not report['files'][0]['snapshot_validated']
    assert report['summary']['observed_positive_city_sales'] is None


def test_preview_excludes_future_national_evidence_and_checks_requested_period(tmp_path):
    from pipelines.dim.city_model_preview import preview
    city, style = tmp_path / 'city', tmp_path / 'style'
    snapshot(city, 'city', '2026-01', [('Model', 'City', 8)])
    snapshot(style, 'style', '2026-02', [('Model', 'Future OEM', 10)])
    assert preview(city, style, period='2026-01')['models'][0]['validated_national_oems'] == []
    with pytest.raises(ValueError, match='没有'):
        preview(city, style, period='2026-03')


def test_preview_cli_stays_offline_and_writes_review_artifacts(tmp_path, monkeypatch):
    from scheduler import runner
    city = tmp_path / 'pipelines/ingest/sales/crawl_16888_city/data'
    style = tmp_path / 'pipelines/ingest/sales/crawl_16888_style/data'
    snapshot(city, 'city', '2026-01', [('Model', 'City', 8)])
    snapshot(style, 'style', '2026-01', [('Model', 'OEM', 10)])
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    output = tmp_path / 'preview'
    assert runner.main(['--chain', 'city', '--period', '2026-01', '--preview-city-models', str(output)]) == 0
    report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert report['summary']['observed_city_models'] == 1
    assert (output / 'models.csv').is_file()
    with pytest.raises(SystemExit):
        runner.main(['--chain', 'city', '--preview-city-models', str(output), '--resume'])
