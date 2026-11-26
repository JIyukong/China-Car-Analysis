from scripts.sales_source_probe import inspect_pages


def test_duplicate_source_identity_keeps_both_positions_without_summing():
    row = '<tr><td>{}</td><td><a href="/s/109528/">model</a></td><td>62</td><td><a href="/f/57339/">maker</a></td><td>16</td></tr>'
    pages = {10: '<table class="xl-table-a">' + row.format(500) + '</table>',
             11: '<table class="xl-table-a">' + row.format(501) + '</table>'}
    result = inspect_pages(pages)
    assert result['duplicates'][0]['source_id'] == '109528'
    assert result['duplicates'][0]['positions'] == [{'page': 10, 'rank': '500'}, {'page': 11, 'rank': '501'}]
    assert result['duplicates'][0]['classification'] == 'identical_source_duplicate'
    assert result['publishable'] is False


def test_conflicting_sales_are_not_treated_as_identical():
    def html(sales):
        return f'<table class="xl-table-a"><tr><td>1</td><td><a href="/s/1/">m</a></td><td>{sales}</td><td>f</td><td>p</td></tr></table>'
    assert inspect_pages({1: html(2), 2: html(3)})['duplicates'][0]['classification'] == 'conflicting_source_duplicate'


def test_empty_response_is_not_success():
    assert inspect_pages({1: '<html>blocked</html>'})['errors']
