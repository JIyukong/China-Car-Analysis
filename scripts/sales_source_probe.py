"""Read-only, bounded diagnosis of specific national sales pages. Never imports ETL."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

from bs4 import BeautifulSoup


def inspect_pages(pages: dict[int, str]) -> dict:
    identities: dict[str, list] = {}
    errors = []
    for number, html in pages.items():
        soup = BeautifulSoup(html, 'html.parser')
        count = 0
        for row in soup.select('table.xl-table-a tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) < 5:
                continue
            link = cells[1].find('a', href=True)
            match = re.fullmatch(r'/s/(\d+)/', link['href']) if link else None
            if not match:
                errors.append({'page': number, 'reason': 'missing_source_identity'})
                continue
            count += 1
            identities.setdefault(match[1], []).append({
                'page': number, 'rank': cells[0].get_text(strip=True),
                'content': [cell.get_text(' ', strip=True) for cell in cells[1:5]],
            })
        if not count:
            errors.append({'page': number, 'reason': 'no_model_rows'})
    duplicates = []
    for identity, rows in identities.items():
        if len(rows) > 1:
            duplicates.append({
                'source_id': identity,
                'positions': [{'page': r['page'], 'rank': r['rank']} for r in rows],
                'classification': ('identical_source_duplicate' if all(r['content'] == rows[0]['content'] for r in rows)
                                   else 'conflicting_source_duplicate'),
                'observations': rows,
            })
    return {'scope': 'selected_pages_only', 'publishable': False,
            'unique_source_ids': len(identities), 'duplicates': duplicates, 'errors': errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('period', help='YYYY-MM')
    parser.add_argument('pages', type=int, nargs='+', help='At most six explicit page numbers')
    args = parser.parse_args()
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args.period):
        parser.error('period must be YYYY-MM')
    numbers = sorted(set(args.pages))
    if len(numbers) > 6 or any(n < 1 or n > 10000 for n in numbers):
        parser.error('select one to six pages, each between 1 and 10000')
    pages, evidence = {}, []
    for number in numbers:
        if pages:
            time.sleep(2)
        url = f'https://xl.16888.com/style-{args.period.replace("-", "")}-{args.period.replace("-", "")}-{number}.html'
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            if response.url != url:
                raise RuntimeError(f'Unexpected redirect: {response.url}')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError('Response exceeds 2 MB diagnostic limit')
        html = raw.decode('utf-8-sig')
        soup = BeautifulSoup(html, 'html.parser')
        months = [el.get('value') for el in soup.select('input.xl-date-input')[:2]]
        current = [el.get_text(strip=True) for el in soup.select('.xl-data-pageing .curr')]
        if months != [args.period, args.period] or current != [str(number)]:
            raise RuntimeError(f'Unexpected month/page at {url}: {months}, {current}')
        pages[number] = html
        evidence.append({'page': number, 'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
                         'fetched_at': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'period': args.period, 'evidence': evidence, **inspect_pages(pages)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
