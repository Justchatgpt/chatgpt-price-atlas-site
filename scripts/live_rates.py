"""Latest available FX quotes from Yahoo Finance; USDT/USD from Binance.US."""
import concurrent.futures
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import urllib.request
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
BINANCE_URL = 'https://api.binance.us/api/v3/trades?symbol=USDTUSD&limit=1'
DELAY_SECONDS = 15 * 60


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def fetch_json(url):
    request = urllib.request.Request(
        url if url.startswith('https://api.binance.us/') else url + ('&' if '?' in url else '?') + '_t=' + str(time.time_ns()),
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Cache-Control': 'no-cache'},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def parse_forex(payload, currency, now):
    chart = payload.get('chart', {})
    if chart.get('error') or not chart.get('result'):
        raise ValueError('行情源未返回该币种')
    meta = chart['result'][0]['meta']
    rate = float(meta['regularMarketPrice'])
    quoted = float(meta['regularMarketTime'])
    if meta.get('symbol') != currency + '=X' or meta.get('currency') != currency:
        raise ValueError('行情币种不匹配')
    if not math.isfinite(rate) or rate <= 0 or not math.isfinite(quoted) or quoted <= 0 or quoted > now + 300:
        raise ValueError('行情价格或时间异常')
    return {'rate': rate, 'quotedAt': iso(quoted), 'delayed': now - quoted > DELAY_SECONDS,
            'provider': 'Yahoo Finance', 'sourceUrl': f'https://finance.yahoo.com/quote/{currency}%3DX/'}


def parse_usdt(payload, now):
    if not isinstance(payload, list) or not payload:
        raise ValueError('币安未返回 USDT/USD 成交报价')
    latest = payload[-1]
    rate = float(latest['price'])
    quoted = float(latest['time']) / 1000
    if not math.isfinite(rate) or rate <= 0 or not math.isfinite(quoted) or quoted <= 0 or quoted > now + 300:
        raise ValueError('USDT 报价或时间异常')
    return {'rate': rate, 'quotedAt': iso(quoted), 'delayed': now - quoted > DELAY_SECONDS,
            'provider': 'Binance.US', 'sourceUrl': BINANCE_URL, 'symbol': 'USDTUSD'}


def collect(currencies, fetcher=fetch_json, clock=time.time):
    result = {'base': 'USD', 'rates': {'USD': 1}, 'quotes': {}, 'errors': {},
              'provider': 'Yahoo Finance', 'providerUrl': 'https://finance.yahoo.com/currencies/',
              'usdtUsd': None, 'usdtQuote': None}
    codes = ['CNY'] + sorted(set(currencies) - {'USD', 'CNY'})

    def load(code):
        if code == 'USDT':
            return parse_usdt(fetcher(BINANCE_URL), clock())
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(code + "=X")}?interval=1m&range=1d'
        return parse_forex(fetcher(url), code, clock())

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        jobs = {executor.submit(load, code): code for code in codes + ['USDT']}
        for job in concurrent.futures.as_completed(jobs):
            code = jobs[job]
            try:
                observation = job.result()
                if code == 'USDT':
                    result['usdtUsd'] = observation['rate']
                    result['usdtQuote'] = observation
                else:
                    result['rates'][code] = observation['rate']
                    result['quotes'][code] = observation
            except Exception as error:
                result['errors'][code] = type(error).__name__ + ': 行情暂不可用'
    result['fetchedAt'] = iso(clock())
    return result


if __name__ == '__main__':
    snapshot = json.loads((ROOT / 'public/data/prices.json').read_text(encoding='utf-8'))
    required = {c['currency'] for c in snapshot['countries'] if c.get('currency')}
    print(json.dumps(collect(required), ensure_ascii=False))
