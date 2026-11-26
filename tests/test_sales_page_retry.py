import pytest

from scheduler.jobs.crawl_16888_job import _duplicate_pages, _replace_duplicate_pages


def row(name, sales='62'):
    return {'model_name': name, 'manufacturer': 'maker', 'sales_volume': sales}


def test_only_duplicate_pages_are_refetched():
    pages = [[row('a')], [row('a')], [row('c')]]
    calls = []
    def fetch(number):
        calls.append(number)
        return [row('a' if number == 1 else 'b')]
    repaired = _replace_duplicate_pages(pages, 'style', fetch, 3)
    assert calls == [1, 2]
    assert [r['model_name'] for p in repaired for r in p] == ['a', 'b', 'c']


def test_persistent_duplicate_has_bounded_retries():
    calls = []
    def fetch(number):
        calls.append(number)
        return [row('a')]
    with pytest.raises(ValueError, match='重复'):
        _replace_duplicate_pages([[row('a')], [row('a')]], 'style', fetch, 2)
    assert len(calls) == 4


def test_conflicting_duplicates_are_never_retried():
    with pytest.raises(ValueError, match='冲突'):
        _replace_duplicate_pages([[row('a')], [row('a', '63')]], 'style', lambda n: pytest.fail('must not fetch'), 2)


def test_city_key_includes_city():
    assert _duplicate_pages([[{'model_name': 'a', 'city': 'x'}], [{'model_name': 'a', 'city': 'y'}]], 'city') == set()


def test_changed_page_size_is_rejected():
    with pytest.raises(ValueError, match='行数变化'):
        _replace_duplicate_pages([[row('a')], [row('a')]], 'style', lambda n: [row('a'), row('b')], 2)


def test_retry_does_not_accumulate_rows_from_previous_attempts():
    originals = [[row('a')], [row('a')]]
    with pytest.raises(ValueError, match='重复'):
        _replace_duplicate_pages(originals, 'style', lambda n: [row('b')], 2)
    assert originals == [[row('a')], [row('a')]]


def test_widespread_duplicates_do_not_trigger_large_crawl():
    with pytest.raises(ValueError, match='六页'):
        _replace_duplicate_pages([[row('a')] for _ in range(7)], 'style', lambda n: pytest.fail('must not fetch'), 7)


def test_capture_rewrites_csv_and_hash_only_after_complete_repair(tmp_path, monkeypatch):
    import csv
    import hashlib
    import json
    from types import SimpleNamespace
    from scheduler.jobs import crawl_16888_job as job

    visits = []
    state = {'number': 0}
    def navigate(page, url):
        state['number'] = int(url.rsplit('-', 1)[1].split('.')[0])
        visits.append(state['number'])
    class Locator:
        first = property(lambda self: self)
        def locator(self, selector):
            return SimpleNamespace(all_text_contents=lambda: [str(state['number'])],
                                   count=lambda: int(state['number'] == 1))
    page = SimpleNamespace(locator=lambda _: Locator())
    crawler = SimpleNamespace(
        CSV_COLUMNS=['month', 'model_name', 'manufacturer', 'sales_volume'],
        PAGE_URL_TEMPLATE='https://example.test/style-{ym}-{ym}-{page}.html',
        PAGER_CONTAINER='pager', PAGE_SLEEP_MIN=0, PAGE_SLEEP_MAX=0,
        get_total_count=lambda _: 2,
        extract_table_rows=lambda _: [row('b' if len(visits) == 4 else 'a')],
    )
    monkeypatch.setattr(job, '_navigate_capture', navigate)
    monkeypatch.setattr(job, '_reported_months', lambda *a: ['2026-07', '2026-07'])
    job._capture_pages(page, crawler, 'style', '2026-07', tmp_path)
    csv_path = tmp_path / 'style_2026-07.csv'
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        assert [r['model_name'] for r in csv.DictReader(handle)] == ['a', 'b']
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['csv_sha256'] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert visits == [1, 2, 1, 2]
    assert not list(tmp_path.glob('*.html'))
