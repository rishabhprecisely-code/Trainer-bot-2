import json
import logging
import os
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import argparse

import pandas as pd
import requests
import yfinance as yf
from typing import Any, Mapping, Optional, Tuple, cast
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')
DEFAULT_PORT = 8080
DEFAULT_CHECK_INTERVAL = 300
DEFAULT_DATA_SOURCE = 'coingecko'
SYMBOL = 'BTC-USD'
TIMEFRAME = '1h'
FETCH_RETRY_COUNT = 3
FETCH_RETRY_DELAY = 2

LAST_STATUS = {}


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=[
            RotatingFileHandler(os.path.join(LOG_DIR, 'bot.log'), maxBytes=5_000_000, backupCount=3),
            logging.StreamHandler(sys.stdout),
        ],
    )


setup_logging()


def load_local_env():
    if not os.path.exists(ENV_FILE):
        return

    try:
        with open(ENV_FILE, 'r', encoding='utf-8') as env_file:
            for line in env_file:
                stripped = line.strip()
                if not stripped or stripped.startswith('#') or '=' not in stripped:
                    continue
                key, value = stripped.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        logging.info('Loaded local .env file for missing environment variables.')
    except Exception as exc:
        logging.warning('Failed to load local .env file: %s', exc)


@dataclass
class Config:
    discord_webhook_url: str | None
    port: int
    status_token: str | None
    data_source: str
    check_interval: int
    symbol: str
    timeframe: str
    fetch_retry_count: int
    fetch_retry_delay: int


def get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        logging.warning('Invalid integer for %s: %s. Using default %s.', name, os.getenv(name), default)
        return default


def get_config() -> Config:
    load_local_env()
    webhook = os.getenv('DISCORD_WEBHOOK_URL')
    if webhook:
        webhook = webhook.strip()

    if webhook and not webhook.startswith('https://discord.com/api/webhooks/'):
        logging.warning(
            'DISCORD_WEBHOOK_URL does not appear to be a Discord webhook endpoint. '
            'Expected format: https://discord.com/api/webhooks/<id>/<token>. '
            'Current value: %s', webhook)

    return Config(
        discord_webhook_url=webhook if webhook else None,
        port=get_int_env('PORT', DEFAULT_PORT),
        status_token=os.getenv('STATUS_TOKEN'),
        data_source=os.getenv('DATA_SOURCE', DEFAULT_DATA_SOURCE).strip().lower(),
        check_interval=get_int_env('CHECK_INTERVAL', DEFAULT_CHECK_INTERVAL),
        symbol=os.getenv('SYMBOL', SYMBOL).strip(),
        timeframe=os.getenv('TIMEFRAME', TIMEFRAME).strip(),
        fetch_retry_count=get_int_env('FETCH_RETRY_COUNT', FETCH_RETRY_COUNT),
        fetch_retry_delay=get_int_env('FETCH_RETRY_DELAY', FETCH_RETRY_DELAY),
    )


class DiscordWebhookClient:
    def __init__(self, webhook_url: str | None):
        self.webhook_url = webhook_url
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send_embed(self, title: str, description: Optional[str] = None, color: int = 3447003) -> None:
        # check webhook_url directly to avoid optional-member static warnings
        if not self.webhook_url:
            logging.warning('Discord webhook is disabled. Alerts will be skipped.')
            return

        if not self.webhook_url.startswith('https://discord.com/api/webhooks/'):
            logging.warning('Invalid Discord webhook URL. Alerts will be skipped: %s', self.webhook_url)
            return

        payload = {
            'embeds': [{
                'title': title,
                'description': description or '',
                'color': color,
                'footer': {'text': 'Yahoo Finance Secure Scraper Engine'},
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }]
        }

        try:
            resp = self.session.post(self.webhook_url, json=payload, timeout=10)
        except requests.RequestException as exc:
            logging.exception('Failed to send Discord alert: %s', exc)
            return

        if resp.status_code == 204:
            logging.info('Discord alert delivered successfully.')
            return

        if resp.status_code == 401:
            logging.warning('Discord webhook responded 401: invalid webhook token.')
        elif resp.status_code == 404:
            logging.warning('Discord webhook responded 404: webhook URL not found.')
        elif resp.status_code == 405:
            logging.warning('Discord webhook responded 405: method not allowed for webhook endpoint.')
        else:
            logging.warning('Discord webhook request failed with status %s: %s', resp.status_code, resp.text)


def build_request_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (compatible; Bot/1.0; +https://example.com/bot)',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Origin': 'https://finance.yahoo.com',
        'Referer': 'https://finance.yahoo.com/',
    })
    return session


def send_alert(message: str, embed_color: int = 3447003) -> None:
    # Backwards-compatible wrapper for tests and legacy call sites.
    config = get_config()
    webhook_client = DiscordWebhookClient(config.discord_webhook_url)
    webhook_client.send_embed('🧠 SYSTEM PREDICTION ENGINE 🧠', message, color=embed_color)


def calculate_rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=window).mean()
    rs = gain / loss
    result = 100 - (100 / (1 + rs))
    # ensure a pandas Series return with the same index as input
    return pd.Series(result, index=prices.index)


def fetch_coingecko_price_data(symbol: str, days: int = 7, interval: str = 'hourly') -> pd.DataFrame | None:
    base = symbol.split('-')[0].upper()
    mapping = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'DOGE': 'dogecoin', 'LTC': 'litecoin'}
    coin = mapping.get(base, base.lower())
    url = f'https://api.coingecko.com/api/v3/coins/{coin}/market_chart'

    session = build_request_session()
    for attempt in range(1, FETCH_RETRY_COUNT + 1):
        try:
            resp = session.get(url, params={'vs_currency': 'usd', 'days': days, 'interval': interval}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                prices = data.get('prices', [])
                volumes = data.get('total_volumes', [])
                if not prices:
                    logging.warning('CoinGecko returned empty price data for %s', symbol)
                    break

                df: pd.DataFrame = pd.DataFrame(prices, columns=['timestamp', 'close'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                df['close'] = df['close'].astype(float)

                if volumes:
                    volume_df: pd.DataFrame = pd.DataFrame(volumes, columns=['timestamp', 'volume'])
                    volume_df['timestamp'] = pd.to_datetime(volume_df['timestamp'], unit='ms', utc=True)
                    df = df.merge(volume_df, on='timestamp', how='left')
                else:
                    df['volume'] = 0.0

                df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
                df['open'] = df['close']
                df['high'] = df['close']
                df['low'] = df['close']
                return cast(pd.DataFrame, df[['timestamp', 'open', 'high', 'low', 'close', 'volume']])

            if resp.status_code == 429:
                logging.warning('CoinGecko rate limited attempt %s/%s for %s', attempt, FETCH_RETRY_COUNT, symbol)
            else:
                logging.warning('CoinGecko API returned status %s for %s', resp.status_code, symbol)
        except requests.RequestException as exc:
            logging.warning('CoinGecko request failed for %s: %s', symbol, exc)

        if attempt < FETCH_RETRY_COUNT:
            time.sleep(FETCH_RETRY_DELAY)

    logging.warning('CoinGecko price fetch failed after %s attempts for %s', FETCH_RETRY_COUNT, symbol)
    return None


def fetch_yfinance_price_data(symbol: str, timeframe: str, days: int = 7) -> pd.DataFrame | None:
    session = build_request_session()
    try:
        df = yf.download(
            tickers=symbol,
            interval=timeframe,
            period=f'{days}d',
            auto_adjust=True,
            progress=False,
            session=session,
        )
        return df
    except Exception as exc:
        logging.warning('yfinance fetch failed for %s: %s', symbol, exc)
        return None


def fetch_coingecko_spot_price(symbol: str) -> float | None:
    base = symbol.split('-')[0].upper()
    mapping = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'DOGE': 'dogecoin', 'LTC': 'litecoin'}
    coin = mapping.get(base, base.lower())
    url = 'https://api.coingecko.com/api/v3/simple/price'

    session = build_request_session()
    try:
        resp = session.get(url, params={'ids': coin, 'vs_currencies': 'usd'}, timeout=15)
        if resp.status_code != 200:
            logging.warning('CoinGecko spot price request failed for %s with status %s', symbol, resp.status_code)
            return None

        data = resp.json()
        price = data.get(coin, {}).get('usd')
        return float(price) if price is not None else None
    except requests.RequestException as exc:
        logging.warning('CoinGecko spot price request failed for %s: %s', symbol, exc)
        return None
    except (TypeError, ValueError) as exc:
        logging.warning('CoinGecko spot price parse failed for %s: %s', symbol, exc)
        return None


def fetch_yfinance_spot_price(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        fast_info = getattr(ticker, 'fast_info', None)
        if fast_info:
            price = fast_info.get('last_price') or fast_info.get('lastPrice') or fast_info.get('lastPrice')
            if price is not None:
                return float(price)

        info = getattr(ticker, 'info', None)
        if isinstance(info, dict):
            for key in ('regularMarketPrice', 'currentPrice', 'lastPrice', 'previousClose'):
                price = info.get(key)
                if price is not None:
                    return float(price)
    except Exception as exc:
        logging.warning('yfinance spot price fetch failed for %s: %s', symbol, exc)
    return None


def run_predictive_learning(df: pd.DataFrame) -> tuple[float, float, int, float, float]:
    current_rsi = float(df['rsi'].iloc[-1])
    upper_bound = current_rsi + 4
    lower_bound = current_rsi - 4

    total_matches = 0
    up_moves = 0
    down_moves = 0
    sum_pct = 0.0
    changes = []

    for i in range(15, len(df) - 1):
        hist_rsi = float(df['rsi'].iloc[i])
        if lower_bound <= hist_rsi <= upper_bound:
            price_then = float(df['close'].iloc[i])
            price_later = float(df['close'].iloc[i + 1])
            total_matches += 1
            change = (price_later - price_then) / price_then
            sum_pct += change
            changes.append(change)
            if price_later > price_then:
                up_moves += 1
            else:
                down_moves += 1

    if total_matches == 0:
        return 50.0, 50.0, 0, 0.0, 0.0

    bullish_probability = (up_moves / total_matches) * 100
    bearish_probability = (down_moves / total_matches) * 100
    avg_pct_change = (sum_pct / total_matches) * 100
    try:
        vol_pct = statistics.pstdev(changes) * 100
    except Exception:
        vol_pct = 0.0

    return bullish_probability, bearish_probability, total_matches, avg_pct_change, vol_pct


def _ensure_data_dir() -> Path:
    data_dir = Path(os.path.dirname(__file__)) / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def persist_price_data(df: pd.DataFrame, symbol: str) -> Path:
    """Append new price rows to a rolling CSV for the symbol and keep last 48 hours."""
    data_dir = _ensure_data_dir()
    path = data_dir / f'{symbol.replace("/", "-")}_prices.csv'
    # standardize columns
    df_to_save = df.copy()
    if 'timestamp' in df_to_save.columns:
        df_to_save['timestamp'] = pd.to_datetime(df_to_save['timestamp'])
        df_to_save = df_to_save.set_index('timestamp')
    df_to_save = df_to_save[['open', 'high', 'low', 'close', 'volume']]

    if path.exists():
        try:
            existing = pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
            combined = pd.concat([existing, df_to_save])
            combined = combined[~combined.index.duplicated(keep='last')]
        except Exception:
            combined = df_to_save
    else:
        combined = df_to_save

    # keep last 48 hours (approximate by 48*3600 seconds)
    try:
        cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=48)
        combined = combined[combined.index >= cutoff]
    except Exception:
        pass

    combined.reset_index().to_csv(path, index=False)
    return path


def generate_market_signals(config: Config, webhook_client: DiscordWebhookClient | None = None) -> None:
    """Fetch ~2 days of data, persist it, compute RSI and produce three signals with expected ranges, send to Discord."""
    if webhook_client is None:
        webhook_client = DiscordWebhookClient(config.discord_webhook_url)

    # Try primary source then fallback
    df = None
    try:
        if config.data_source == 'coingecko':
            df = fetch_coingecko_price_data(config.symbol, days=2, interval='hourly')
            if df is None or df.empty:
                df = fetch_yfinance_price_data(config.symbol, config.timeframe)
        else:
            df = fetch_yfinance_price_data(config.symbol, config.timeframe)
            if df is None or df.empty:
                df = fetch_coingecko_price_data(config.symbol, days=2, interval='hourly')
    except Exception as exc:
        logging.warning('Failed to fetch market data for signals: %s', exc)
        return

    if df is None or df.empty:
        logging.warning('No data available to generate signals.')
        return

    # Normalize dataframe
    if 'timestamp' not in df.columns and 'Date' in df.columns:
        df = df.rename(columns={'Date': 'timestamp'})
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    else:
        # try to use index as datetime
        try:
            df = df.reset_index()
            df['timestamp'] = pd.to_datetime(df['index'])
        except Exception:
            df['timestamp'] = pd.Timestamp.utcnow()

    # standardize lowercase column names
    df.columns = [str(c).lower() for c in df.columns]
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col not in df.columns:
            df[col] = df.get(col.capitalize(), 0.0)

    path = persist_price_data(df[['timestamp', 'open', 'high', 'low', 'close', 'volume']], config.symbol)
    logging.info('Persisted price data to %s', path)

    # compute RSI and predictive metrics
    close_series = pd.Series(df['close'].astype(float))
    df['rsi'] = calculate_rsi(close_series)
    df = df.dropna(subset=['rsi'])
    if df.empty:
        logging.warning('Not enough data after RSI calculation to generate signals.')
        return

    bull_prob, bear_prob, matches, avg_pct_change, vol_pct = run_predictive_learning(df)

    current_price = float(df['close'].iloc[-1])
    current_rsi = float(df['rsi'].iloc[-1])

    # Prepare three horizons: 1h, 3h, 6h (scale expected change and volatility)
    horizons = [(1, 1.0), (3, 3.0), (6, 6.0)]
    signals = []
    for label, factor in horizons:
        expected_target = current_price * (1 + (avg_pct_change * factor) / 100.0)
        expected_low = current_price * (1 + ((avg_pct_change - vol_pct) * factor) / 100.0)
        expected_high = current_price * (1 + ((avg_pct_change + vol_pct) * factor) / 100.0)
        if bull_prob > bear_prob and bull_prob > 50:
            direction = 'bullish'
        elif bear_prob > bull_prob and bear_prob > 50:
            direction = 'bearish'
        else:
            direction = 'neutral'

        signals.append({
            'horizon_hours': label,
            'direction': direction,
            'expected_target': expected_target,
            'expected_low': expected_low,
            'expected_high': expected_high,
            'probabilities': {'bull': round(bull_prob, 2), 'bear': round(bear_prob, 2)},
        })

    # Build message
    lines = [f'Market Signals for {config.symbol} (price ${current_price:,.2f}, RSI {current_rsi:.2f})\n']
    for s in signals:
        lines.append(
            f"{s['horizon_hours']}h — {s['direction'].upper()}: target ${s['expected_target']:,.2f}, "
            f"range ${s['expected_low']:,.2f} — ${s['expected_high']:,.2f} (bull {s['probabilities']['bull']}% / bear {s['probabilities']['bear']}%)"
        )

    message = '\n'.join(lines)
    logging.info('Generated signals: %s', message)

    try:
        webhook_client.send_embed('📡 MARKET SIGNALS', message, color=3447003)
    except Exception as exc:
        logging.warning('Failed to send webhook for signals: %s', exc)

    # update LAST_STATUS with summary
    LAST_STATUS.update({
        'symbol': config.symbol,
        'price': current_price,
        'rsi': current_rsi,
        'signals': signals,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


def build_alert_message(
    symbol: str,
    direction: str,
    current_price: float,
    current_rsi: float,
    matches: int,
    bull_prob: float,
    bear_prob: float,
    avg_pct_change: float,
    vol_pct: float,
    expected_target: float | None,
    expected_range_low: float | None,
    expected_range_high: float | None,
) -> tuple[str, int]:
    if direction == 'bullish':
        title = '📈 RECOMMENDED ACTION: BUY / LONG'
        color = 3066993
        summary = f'The market historically moved upward within the next hour {bull_prob:.1f}% of the time.'
    elif direction == 'bearish':
        title = '📉 RECOMMENDED ACTION: SELL / SHORT'
        color = 15158332
        summary = f'The market historically moved downward within the next hour {bear_prob:.1f}% of the time.'
    else:
        title = '⚠️ MARKET ALERT'
        color = 3447003
        summary = 'No strong trend signal detected for the current RSI profile.'

    expected_section = ''
    if expected_target is not None and expected_range_low is not None and expected_range_high is not None:
        expected_section = (
            f'**Expected Target (1h):** ${expected_target:,.2f} ({avg_pct_change:+.2f}% avg)\n'
            f'**Expected Range (1h):** ${expected_range_low:,.2f} — ${expected_range_high:,.2f}\n\n'
        )

    # Build a readable directional note instead of embedding long replacements inline
    if direction == 'bullish':
        directional_note = 'Strong bullish reversal pressure expected next hour.'
    elif direction == 'bearish':
        directional_note = 'Strong bearish distribution pressure expected next hour.'
    else:
        directional_note = 'Consolidation mode expected.'

    message = (
        f'{title}\n\n'
        f'**Asset Target:** {symbol}\n'
        f'**Current Price:** ${current_price:,.2f}\n'
        f'**Current RSI Value:** {current_rsi:.2f}\n'
        f'{expected_section}'
        f'📊 **Predictive Pattern Learning:**\n'
        f'Verified **{matches} matches** over the last 7 days matching this RSI profile.\n'
        f'{summary}\n\n'
        f'*Directional Outlook:* {directional_note}*'
    )

    return message, color


def fetch_and_analyze(config: Config | None = None, webhook_client: DiscordWebhookClient | None = None) -> None:
    global LAST_STATUS
    if config is None:
        config = get_config()
    if webhook_client is None:
        webhook_client = DiscordWebhookClient(config.discord_webhook_url)

    logging.info('Beginning analysis cycle for %s at %s', config.symbol, datetime.now(timezone.utc).isoformat())

    df = None
    spot_price = None

    if config.data_source == 'coingecko':
        spot_price = fetch_coingecko_spot_price(config.symbol)
        df = fetch_coingecko_price_data(config.symbol, days=7, interval='hourly')
        if df is None or df.empty:
            logging.warning('Primary source CoinGecko failed; falling back to yfinance.')
            df = fetch_yfinance_price_data(config.symbol, config.timeframe)
            if spot_price is None:
                spot_price = fetch_yfinance_spot_price(config.symbol)
    else:
        spot_price = fetch_yfinance_spot_price(config.symbol)
        df = fetch_yfinance_price_data(config.symbol, config.timeframe)
        if df is None or df.empty:
            logging.warning('Primary source yfinance failed; falling back to CoinGecko.')
            df = fetch_coingecko_price_data(config.symbol, days=7, interval='hourly')
            if spot_price is None:
                spot_price = fetch_coingecko_spot_price(config.symbol)

    if df is None or df.empty:
        logging.warning('No price data available for analysis. Ending cycle.')
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [str(col).lower() for col in df.columns]
    if not set(['open', 'high', 'low', 'close', 'volume']).issubset(set(df.columns)):
        logging.warning('Fetched data is missing required columns. Columns found: %s', list(df.columns))
        return

    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df['close'] = df['close'].astype(float)
    # ensure close column is a Series for type checkers
    close_series = pd.Series(df['close'])
    df['rsi'] = calculate_rsi(close_series)
    # pandas-stubs may be strict about dropna signature; ignore here
    df = df.dropna(subset=['rsi'])  # type: ignore[call-arg]
    df = df.copy()
    if len(df) < 5:
        logging.warning('Not enough price data after RSI calculation: %s rows', len(df))
        return

    current_price = float(df['close'].iloc[-1])
    current_rsi = float(df['rsi'].iloc[-1])
    prev_rsi = float(df['rsi'].iloc[-2])

    if spot_price is not None:
        logging.info('Using spot price %s for %s (overriding latest close).', spot_price, config.symbol)
        current_price = spot_price

    bull_prob, bear_prob, matches, avg_pct_change, vol_pct = run_predictive_learning(df)
    expected_target = None
    expected_range_low = None
    expected_range_high = None
    if matches > 0:
        expected_target = current_price * (1 + avg_pct_change / 100.0)
        expected_range_low = current_price * (1 + (avg_pct_change - vol_pct) / 100.0)
        expected_range_high = current_price * (1 + (avg_pct_change + vol_pct) / 100.0)

    is_extreme_oversold = current_rsi <= 25 or (prev_rsi < 30 and current_rsi >= 30)
    is_extreme_overbought = current_rsi >= 75 or (prev_rsi > 70 and current_rsi <= 70)

    direction = 'neutral'
    if is_extreme_oversold or bull_prob > 60:
        direction = 'bullish'
    elif is_extreme_overbought or bear_prob > 60:
        direction = 'bearish'

    if direction in {'bullish', 'bearish'}:
        alert_text, color = build_alert_message(
            config.symbol,
            direction,
            current_price,
            current_rsi,
            matches,
            bull_prob,
            bear_prob,
            avg_pct_change,
            vol_pct,
            expected_target,
            expected_range_low,
            expected_range_high,
        )
        webhook_client.send_embed('🧠 SYSTEM PREDICTION ENGINE 🧠', alert_text, color=color)
    else:
        logging.info('No alert sent. bull_prob=%.1f bear_prob=%.1f current_rsi=%.2f', bull_prob, bear_prob, current_rsi)

    LAST_STATUS = {
        'symbol': config.symbol,
        'price': current_price,
        'rsi': current_rsi,
        'bull_prob': round(bull_prob, 2),
        'bear_prob': round(bear_prob, 2),
        'matches': matches,
        'avg_pct_change': round(avg_pct_change, 2),
        'volatility_pct': round(vol_pct, 2),
        'expected_target': round(expected_target, 2) if expected_target is not None else None,
        'expected_range_low': round(expected_range_low, 2) if expected_range_low is not None else None,
        'expected_range_high': round(expected_range_high, 2) if expected_range_high is not None else None,
        'direction': direction,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }


class HealthCheckServer(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/status':
            if not verify_status_request(self.headers, parse_qs(parsed.query)):
                self.send_response(401)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'unauthorized'}).encode())
                return

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            payload = LAST_STATUS or {'status': 'no data yet'}
            self.wfile.write(json.dumps(payload).encode())
            return

        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot Status: Operational and Connected.')

    def log_message(self, format, *args):
        return


def verify_status_request(headers: Any, query_params: Mapping[str, Any]) -> bool:
    token = os.getenv('STATUS_TOKEN')
    if not token:
        return True

    auth = None
    try:
        auth = headers.get('Authorization')  # type: ignore[attr-defined]
    except Exception:
        # headers may be a mapping-like object without .get
        try:
            auth = headers['Authorization']  # type: ignore[index]
        except Exception:
            auth = None

    if isinstance(auth, str) and auth.strip().lower().startswith('bearer '):
        return auth.strip()[7:] == token

    query_token = None
    try:
        query_token = query_params.get('token')  # type: ignore[attr-defined]
    except Exception:
        try:
            query_token = query_params['token']  # type: ignore[index]
        except Exception:
            query_token = None
    if query_token and query_token[0] == token:
        return True

    return False


def bot_loop(config: Config, webhook_client: DiscordWebhookClient) -> None:
    time.sleep(5)
    if webhook_client.enabled:
        webhook_client.send_embed('🤖 Self-learning bot initialized and awaiting market signals.', color=3447003)

    while True:
        try:
            # Run both analysis and signal generation each cycle
            fetch_and_analyze(config, webhook_client)
            try:
                generate_market_signals(config, webhook_client)
            except Exception as exc:
                logging.exception('generate_market_signals failed: %s', exc)
        except Exception as exc:
            logging.exception('Unexpected error in fetch_and_analyze: %s', exc)
        time.sleep(config.check_interval)


def bind_http_server(port: int) -> HTTPServer:
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(('0.0.0.0', candidate), HealthCheckServer)
            logging.info('Bound health server on port %s', candidate)
            return server
        except OSError as exc:
            logging.warning('Port %s unavailable: %s', candidate, exc)
    logging.error('Could not bind any port in range %s-%s', port, port + 9)
    sys.exit(1)


def main() -> None:
    config = get_config()
    if not config.discord_webhook_url:
        logging.warning('DISCORD_WEBHOOK_URL is not configured. Alerts will not be sent.')

    webhook_client = DiscordWebhookClient(config.discord_webhook_url)
    # Optionally start a lightweight Discord bot if explicitly enabled.
    # Set `START_DISCORD_BOT=true` and provide `TOKEN` as a repository secret to enable.
    try:
        start_discord = os.getenv('START_DISCORD_BOT', 'false').strip().lower() == 'true'
    except Exception:
        start_discord = False
    if start_discord:
        def start_discord_bot_if_enabled():
            token = os.getenv('TOKEN')
            if not token:
                logging.warning('START_DISCORD_BOT is set but TOKEN is missing. Skipping Discord bot startup.')
                return
            try:
                import discord
                from discord.ext import commands
            except Exception as exc:
                logging.warning('Discord library not available; cannot start Discord bot: %s', exc)
                return

            bot = commands.Bot(command_prefix='!')

            @bot.event
            async def on_ready():
                logging.info('Discord bot logged in as %s', bot.user)

            @bot.command()
            async def ping(ctx):
                await ctx.send('Pong!')

            def _run():
                try:
                    bot.run(token)
                except Exception as exc:
                    logging.exception('Discord bot runtime error: %s', exc)

            t = threading.Thread(target=_run, name='discord-bot-thread', daemon=True)
            t.start()

        start_discord_bot_if_enabled()

    # Log whether a Discord token is present (masked) for runtime debugging.
    token = os.getenv('TOKEN')
    if token:
        display = f"***{len(token) - 6}***" if len(token) > 6 else "***"
        logging.info('Discord TOKEN present (masked): %s', display)
    else:
        logging.info('Discord TOKEN not present in environment.')
    # CLI: allow running only signal generation (for Railway scheduled jobs)
    parser = argparse.ArgumentParser()
    parser.add_argument('--signals-only', action='store_true', help='Run signal generator once and exit')
    args = parser.parse_args()

    if args.signals_only:
        generate_market_signals(config, webhook_client)
        return

    thread = threading.Thread(target=bot_loop, args=(config, webhook_client), daemon=True)
    thread.start()

    httpd = bind_http_server(config.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info('Shutdown requested. Stopping bot.')
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
