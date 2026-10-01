"""Fetch every storefront listed by Apple. Uses only the Python standard library."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'public' / 'data' / 'prices.json'
APP_URL = 'https://apps.apple.com/{code}/app/chatgpt/id6448311069?l=en-GB'


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; PriceAtlas/1.0)', 'Accept-Language': 'en-GB,en;q=0.9'})
            with urlopen(request, timeout=25) as response:
                return response.read().decode('utf-8')
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(1.5 * (attempt + 1))


def storefronts(page):
    # Apple's own locale switcher is the scope of the scan, not an ISO country guess.
    pattern = r'<h2[^>]*>([^<]+)</h2>|<a\b[^>]*href="/([a-z]{2})/iphone/today"[^>]*data-testid="region-list-link"[^>]*>\s*<span[^>]*>(.*?)</span>'
    region = None
    countries = {}
    for heading, code, name in re.findall(pattern, page, re.S):
        if heading in ('Africa, Middle East, and India', 'Asia Pacific', 'Europe', 'Latin America and the Caribbean', 'The United States and Canada', 'United States and Canada'):
            region = heading
        if code:
            if not region:
                raise ValueError('Apple storefront region structure changed')
            countries[code] = {'code': code, 'name': html.unescape(name), 'region': region}
    if len(countries) < 150:
        raise ValueError(f'Incomplete Apple storefront list: {len(countries)}')
    return list(countries.values())


def price_number(label, currency):
    digits = re.sub(r'[^\d,.]', '', label)
    if not digits:
        raise ValueError(f'Unrecognized price: {label!r}')
    # Indonesian storefronts abbreviate prices: 349ribu = 349,000;
    # 9,399juta = 9.399 million. The comma is decimal in compact notation.
    compact = re.search(r'(ribu|juta|miliar)\s*$', label, re.I)
    if compact:
        if currency != 'IDR' or not re.fullmatch(r'\d+(?:,\d+)?', digits):
            raise ValueError(f'Unrecognized compact price: {label!r}')
        multiplier = {'ribu': 1_000, 'juta': 1_000_000, 'miliar': 1_000_000_000}[compact.group(1).lower()]
        amount = float(digits.replace(',', '.')) * multiplier
        if amount <= 0:
            raise ValueError('IAP amount must be positive')
        return amount
    # Apple formats amounts in the storefront's numeric locale, even with l=en-GB.
    places = 3 if currency in {'BHD', 'KWD', 'OMR', 'JOD', 'TND'} else 2
    if currency in {'JPY', 'KRW', 'CLP', 'VND', 'XAF', 'XOF', 'XPF', 'UGX', 'RWF', 'PYG'}:
        places = 0
    last = max(digits.rfind(','), digits.rfind('.'))
    fraction = len(digits) - last - 1
    if last >= 0 and places > 0 and 0 < fraction <= places and (fraction != 3 or places == 3):
        normalized = re.sub('[,.]', '', digits[:last]) + '.' + digits[last + 1:]
    else:
        normalized = re.sub('[,.]', '', digits)
    amount = float(normalized)
    if amount <= 0:
        raise ValueError('IAP amount must be positive')
    return amount


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def parse_purchases(page, code):
    match = re.search(r'<script\b[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if not match:
        raise ValueError('Apple page data missing')
    payload = json.loads(match.group(1))
    intent = payload['data'][0]['intent']
    if intent.get('storefront') != code or intent.get('id') != '6448311069':
        raise ValueError('Unexpected Apple storefront redirect')
    currency_match = re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', page)
    if not currency_match:
        raise ValueError('Apple currency missing')
    currency = currency_match.group(1)
    pairs = []
    for node in walk(payload):
        for pair in node.get('textPairs', []):
            if isinstance(pair, list) and len(pair) == 2 and re.search(r'ChatGPT|Credits', pair[0], re.I):
                if pair not in pairs:
                    pairs.append(pair)
    items = [{'name': name, 'amount': price_number(label, currency), 'displayPrice': label, 'period': None} for name, label in pairs]
    for item in items:
        same_name = sorted((p for p in items if p['name'] == item['name']), key=lambda p: p['amount'])
        ordinal = same_name.index(item) + 1
        item['key'] = re.sub(r'[^a-z0-9]+', '-', item['name'].lower()).strip('-') + f'-{ordinal}'
        item['ordinal'] = ordinal
        item['sameNameCount'] = len(same_name)
        item['family'] = 'credits' if re.search('credits', item['name'], re.I) else 'subscription'
    return currency, sorted(items, key=lambda p: p['amount'], reverse=True)


def scrape(country, previous):
    url = APP_URL.format(code=country['code'])
    result = {**country, 'sourceUrl': url, 'checkedAt': now(), 'purchases': []}
    try:
        page = fetch(url)
        currency, purchases = parse_purchases(page, country['code'])
        result.update(currency=currency, purchases=purchases, status='available' if purchases else 'no_prices', priceObservedAt=now())
    except HTTPError as error:
        result.update(status='unavailable' if error.code in (404, 410, 451) else 'error', httpStatus=error.code, error=f'HTTP {error.code}')
    except Exception as error:
        result.update(status='error', error=f'{type(error).__name__}: {error}')
    if result['status'] == 'error' and previous and previous.get('purchases'):
        # Preserve usable historical observations, visibly marked as stale.
        for key in ('currency', 'purchases', 'priceObservedAt'):
            result[key] = previous.get(key)
        result['status'] = 'stale'
    return result


def sync(workers):
    started = now()
    previous = json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {}
    previous_countries = {c['code']: c for c in previous.get('countries', [])}
    page = fetch(APP_URL.format(code='us'))
    countries = storefronts(page)
    results = []
    print(f'Scanning {len(countries)} Apple storefronts, {workers} workers...', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(scrape, c, previous_countries.get(c['code'])): c for c in countries}
        for job in as_completed(jobs):
            result = job.result()
            results.append(result)
            print(f"[{len(results):3}/{len(countries)}] {result['code'].upper()} {result['status']}: {len(result['purchases'])} items", flush=True)
    successful = sum(c['status'] == 'available' for c in results)
    if successful < len(countries) * 0.6:
        raise RuntimeError('Too few verified storefronts; previous snapshot preserved')
    snapshot = {
        'schemaVersion': 1, 'appId': '6448311069', 'appName': 'ChatGPT',
        'scanStartedAt': started, 'generatedAt': now(),
        'scope': {'sourceUrl': APP_URL.format(code='us'), 'storefrontCount': len(countries), 'description': 'All storefronts in Apple App Store locale selector'},
        'countries': sorted(results, key=lambda c: c['code']),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(OUTPUT)
    print(f'Saved {OUTPUT}: {successful}/{len(countries)} priced storefronts', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=5)
    args = parser.parse_args()
    sync(max(1, min(args.workers, 8)))
