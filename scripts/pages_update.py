"""Maintain public GitHub Pages snapshots, without private-repository access."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from live_rates import collect
import sync_prices


def stamp():
    return datetime.now(timezone.utc).isoformat()


OBSERVATION_TIMES = frozenset({'scanStartedAt', 'generatedAt', 'checkedAt',
    'priceObservedAt', 'fetchedAt', 'quotedAt', 'savedAt'})


def content_signature(value):
    """Compare business data, not collection timestamps or incidental ordering."""
    if isinstance(value, dict):
        return {key: content_signature(item) for key, item in sorted(value.items())
                if key not in OBSERVATION_TIMES}
    if isinstance(value, list):
        items = [content_signature(item) for item in value]
        if items and all(isinstance(item, dict) for item in items):
            for key in ('code', 'key'):
                if all(key in item for item in items):
                    return sorted(items, key=lambda item: item[key])
        if all(isinstance(item, str) for item in items):
            return sorted(items)
        return items
    return value


def write_if_changed(target, payload):
    target = Path(target)
    if target.exists():
        previous = json.loads(target.read_text(encoding='utf-8'))
        if content_signature(previous) == content_signature(payload):
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    return True


def update_prices(data_dir):
    target = data_dir / 'prices.json'
    data_dir.mkdir(parents=True, exist_ok=True)
    original_output = sync_prices.OUTPUT
    try:
        # Scan into a candidate file so unchanged observations cannot alter the live file.
        with tempfile.TemporaryDirectory(prefix='.price-scan-', dir=data_dir) as temporary:
            candidate = Path(temporary) / 'prices.json'
            if target.exists():
                shutil.copyfile(target, candidate)
            sync_prices.OUTPUT = candidate
            sync_prices.sync(5)
            result = json.loads(candidate.read_text(encoding='utf-8'))
            return write_if_changed(target, result)
    finally:
        sync_prices.OUTPUT = original_output


def valid_quote(value):
    try:
        datetime.fromisoformat(value['quotedAt'].replace('Z', '+00:00'))
        return math.isfinite(value['rate']) and value['rate'] > 0
    except (KeyError, TypeError, ValueError):
        return False


def merge_rates(previous, fresh, saved_at):
    quotes = {code: value for code, value in fresh.get('quotes', {}).items()
              if valid_quote(value) and fresh.get('rates', {}).get(code) == value['rate']}
    usdt = fresh.get('usdtQuote')
    has_usdt = valid_quote(usdt) and fresh.get('usdtUsd') == usdt['rate']
    if not quotes and not has_usdt:
        raise ValueError('No valid upstream quotes; existing snapshot retained')
    retained = []
    for code, value in previous.get('quotes', {}).items():
        if code not in quotes and valid_quote(value):
            quotes[code] = {**value, 'retained': True, 'delayed': True}
            retained.append(code)
    for code in fresh.get('quotes', {}):
        if code in quotes and code not in retained:
            quotes[code] = {**quotes[code], 'retained': False}
    if not has_usdt:
        old = previous.get('usdtQuote')
        if valid_quote(old):
            usdt = {**old, 'retained': True, 'delayed': True}
            retained.append('USDT')
        else:
            usdt = None
    else:
        usdt = {**usdt, 'retained': False}
    return {
        'base': 'USD', 'rates': {'USD': 1, **{code: value['rate'] for code, value in quotes.items()}},
        'quotes': quotes, 'errors': fresh.get('errors', {}),
        'provider': 'Yahoo Finance', 'providerUrl': 'https://finance.yahoo.com/currencies/',
        'usdtUsd': usdt['rate'] if usdt else None, 'usdtQuote': usdt,
        'fetchedAt': fresh['fetchedAt'],
        'cache': {'status': 'cached', 'savedAt': saved_at, 'retained': retained},
    }


def update_rates(data_dir):
    prices = json.loads((data_dir / 'prices.json').read_text(encoding='utf-8'))
    target = data_dir / 'exchange-rates.json'
    previous = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    codes = {country['currency'] for country in prices['countries'] if country.get('currency')}
    result = merge_rates(previous, collect(codes), stamp())
    changed = write_if_changed(target, result)
    print('Quotes changed:', changed, '; currencies:', len(result['rates']), '; retained:', ', '.join(result['cache']['retained']) or 'none')
    return changed


def prepare_site(root):
    destination = root / '_site'
    destination.mkdir(exist_ok=True)
    # Never publish .git, checkout credentials, workflow files or update scripts.
    for name in ('index.html', 'favicon.svg', 'THIRD_PARTY_NOTICES.txt'):
        shutil.copyfile(root / name, destination / name)
    for name in ('assets', 'data'):
        shutil.copytree(root / name, destination / name, dirs_exist_ok=True)
    (destination / '.nojekyll').write_text('', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--scope', choices=['rates', 'all'], default='rates')
    parser.add_argument('--prepare-site', action='store_true')
    args = parser.parse_args()
    if args.prepare_site:
        prepare_site(Path.cwd())
    else:
        prices_changed = update_prices(args.data_dir) if args.scope == 'all' else False
        rates_changed = update_rates(args.data_dir)
        changed = prices_changed or rates_changed
        print('Data changed:', str(changed).lower())
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write('changed=' + str(changed).lower() + '\n')
