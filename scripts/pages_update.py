"""Maintain public GitHub Pages snapshots, without private-repository access."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
from live_rates import collect
import sync_prices


def stamp():
    return datetime.now(timezone.utc).isoformat()


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
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
    print('Published quote currencies:', len(result['rates']), '; retained:', ', '.join(result['cache']['retained']) or 'none')


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
        if args.scope == 'all':
            sync_prices.OUTPUT = args.data_dir / 'prices.json'
            sync_prices.sync(5)
        update_rates(args.data_dir)
