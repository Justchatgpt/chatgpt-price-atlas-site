"""Maintain public GitHub Pages snapshots, without private-repository access."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.error import URLError
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
    except (KeyError, TypeError, ValueError, AttributeError):
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


def usable_cache(path, source):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if source == 'apple':
            return value.get('schemaVersion') == 1 and any(
                isinstance(country.get('currency'), str) and any(
                    isinstance(item.get('amount'), (int, float)) and item['amount'] > 0
                    for item in country.get('purchases', []))
                for country in value.get('countries', []))
        return value.get('base') == 'USD' and (
            any(valid_quote(quote) for quote in value.get('quotes', {}).values())
            or valid_quote(value.get('usdtQuote')))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def run_updates(data_dir, scope):
    """Keep independent source failures from discarding successful updates."""
    sources = [('apple', update_prices, 'prices.json')] if scope == 'all' else []
    sources.append(('rates', update_rates, 'exchange-rates.json'))
    result = {'changed': False, 'degraded': False, 'failed': False, 'sources': {}}
    for source, updater, filename in sources:
        try:
            changed = updater(data_dir)
            result['changed'] |= changed
            result['sources'][source] = {'status': 'changed' if changed else 'unchanged'}
        except (URLError, TimeoutError, ConnectionError, RuntimeError, ValueError) as error:
            retained = usable_cache(data_dir / filename, source)
            detail = str(error).splitlines()
            message = (type(error).__name__ + (': ' + detail[0] if detail else ''))[:250]
            result['sources'][source] = {'status': 'retained' if retained else 'failed', 'reason': message}
            result['degraded'] = True
            result['failed'] |= not retained
            escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print('::warning title=' + source + ' update::' + escaped + ('; previous snapshot retained' if retained else '; no usable cache'))
    # No upstream source succeeded: keep monitoring red, rather than pretending an update succeeded.
    result['failed'] |= all(item['status'] in ('retained', 'failed') for item in result['sources'].values())
    return result


def report_update(result):
    outputs = {'changed': str(result['changed']).lower(), 'degraded': str(result['degraded']).lower()}
    outputs.update({source + '_status': item['status'] for source, item in result['sources'].items()})
    print('Data check:', json.dumps(outputs))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key, value in outputs.items():
                output.write(key + '=' + value + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write('## Price snapshot check\n\n| Source | Status |\n| --- | --- |\n')
            for source, item in result['sources'].items():
                summary.write('| ' + source + ' | ' + item['status'] + ' |\n')
            summary.write('\nChanged files: ' + outputs['changed'] + '. Degraded: ' + outputs['degraded'] + '.\n')
            summary.write('retained means collection failed and the previous file was kept unchanged.\n')


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
        outcome = run_updates(args.data_dir, args.scope)
        report_update(outcome)
        if outcome['failed']:
            raise SystemExit('Requested data sources failed; usable previous snapshots were preserved')
