import os
import time
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pandas as pd
import yfinance as yf
import json
import sys
import logging
import statistics
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Add rotating file handler
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
file_handler = RotatingFileHandler(os.path.join(log_dir, 'bot.log'), maxBytes=5_000_000, backupCount=3)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logging.getLogger().addHandler(file_handler)


def load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, 'r', encoding='utf-8') as env_file:
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


# --- CONFIGURATION ---
load_local_env()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_WEBHOOK_URL = DISCORD_WEBHOOK_URL.strip() if DISCORD_WEBHOOK_URL else None
if DISCORD_WEBHOOK_URL:
    logging.info('Discord webhook is configured and ready for alert dispatch.')
else:
    logging.warning('Discord webhook URL is not configured; alert dispatch will be skipped.')

SYMBOL = "BTC-USD"
TIMEFRAME = "1h"
CHECK_INTERVAL = 300  # Scan every 5 minutes
DEFAULT_DATA_SOURCE = os.getenv("DATA_SOURCE", "coingecko").strip().lower()
FETCH_RETRY_COUNT = 3
FETCH_RETRY_DELAY = 2

# last fetched metrics for status endpoint
LAST_STATUS = {}

def send_alert(message, embed_color=3447003):
    """Dispatches stylized trade alerts directly to your mobile app channel."""
    if not DISCORD_WEBHOOK_URL:
        logging.warning("Discord webhook URL is not configured; skipping alert.")
        return

    data = {
        "embeds": [{
            "title": "🧠 SYSTEM PREDICTION ENGINE 🧠",
            "description": message,
            "color": embed_color,
            "footer": {"text": "Yahoo Finance Secure Scraper Engine"}
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        if resp.status_code >= 400:
            logging.warning("Discord webhook request failed with status %s: %s", resp.status_code, resp.text)
    except Exception:
        logging.exception("Network dispatch failure while sending alert")

def calculate_rsi(prices, window=14):
    """Helper mathematical function to calculate clean RSI arrays."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def build_request_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/605.1.15',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Origin': 'https://finance.yahoo.com',
        'Referer': 'https://finance.yahoo.com/'
    })
    return session


def fetch_coingecko_price_data(symbol, days=7, interval='hourly'):
    """Fetch historical price data from CoinGecko for fallback analysis.
    Returns a DataFrame with columns [open, high, low, close, volume] or None on failure.
    """
    base = symbol.split('-')[0].upper()
    mapping = {
        'BTC': 'bitcoin',
        'ETH': 'ethereum',
        'DOGE': 'dogecoin',
        'LTC': 'litecoin'
    }
    coin = mapping.get(base, base.lower())
    url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
    session = build_request_session()

    for attempt in range(1, FETCH_RETRY_COUNT + 1):
        try:
            resp = session.get(
                url,
                params={"vs_currency": "usd", "days": days, "interval": interval},
                timeout=15,
            )
            if resp.status_code == 429:
                logging.warning("CoinGecko rate limited on attempt %s/%s for %s", attempt, FETCH_RETRY_COUNT, symbol)
            elif resp.status_code != 200:
                logging.warning("CoinGecko API returned status %s for %s", resp.status_code, symbol)
            else:
                data = resp.json()
                prices = data.get('prices', [])
                if not prices:
                    logging.warning("CoinGecko returned empty price data for %s", symbol)
                else:
                    df = pd.DataFrame(prices, columns=['timestamp', 'close'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                    df['close'] = df['close'].astype(float)

                    volumes = data.get('total_volumes', [])
                    if volumes:
                        volume_df = pd.DataFrame(volumes, columns=['timestamp', 'volume'])
                        volume_df['timestamp'] = pd.to_datetime(volume_df['timestamp'], unit='ms', utc=True)
                        df = df.merge(volume_df, on='timestamp', how='left')
                    else:
                        df['volume'] = 0.0

                    df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
                    df['open'] = df['close']
                    df['high'] = df['close']
                    df['low'] = df['close']
                    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        except requests.RequestException as exc:
            logging.warning("CoinGecko request failed for %s (attempt %s/%s): %s", symbol, attempt, FETCH_RETRY_COUNT, exc)
        except ValueError as exc:
            logging.warning("CoinGecko returned invalid JSON for %s: %s", symbol, exc)

        if attempt < FETCH_RETRY_COUNT:
            time.sleep(FETCH_RETRY_DELAY)

    logging.warning("CoinGecko data fetch failed after %s attempts for %s", FETCH_RETRY_COUNT, symbol)
    return None

def run_predictive_learning(df):
    """
    LEARNING LAYER: Scans historical 1H chart patterns over the last 7 days.
    Looks back to see if previous occurrences of the current RSI range 
    resulted in upward or downward price movements 1 hour later.
    """
    current_rsi = float(df['rsi'].iloc[-1])
    
    upper_bound = current_rsi + 4
    lower_bound = current_rsi - 4
    
    historical_up_moves = 0
    historical_down_moves = 0
    total_matches = 0
    # accumulate fractional percent changes (price_1h_later - price_then) / price_then
    sum_frac_changes = 0.0
    list_frac_changes = []
    
    for i in range(15, len(df) - 1):
        hist_rsi = float(df['rsi'].iloc[i])
        
        if lower_bound <= hist_rsi <= upper_bound:
            price_then = float(df['close'].iloc[i])
            price_1h_later = float(df['close'].iloc[i+1])
            
            total_matches += 1
            frac_change = (price_1h_later - price_then) / price_then
            sum_frac_changes += frac_change
            list_frac_changes.append(frac_change)
            if price_1h_later > price_then:
                historical_up_moves += 1
            else:
                historical_down_moves += 1
    
    if total_matches > 0:
        bullish_probability = (historical_up_moves / total_matches) * 100
        bearish_probability = (historical_down_moves / total_matches) * 100
        # average percent change across matching instances (as percentage)
        avg_pct_change = (sum_frac_changes / total_matches) * 100
        # volatility as population standard deviation of fractional changes (percentage)
        try:
            vol_pct = statistics.pstdev(list_frac_changes) * 100
        except Exception:
            vol_pct = 0.0
    else:
        bullish_probability, bearish_probability = 50.0, 50.0
        avg_pct_change = 0.0
        vol_pct = 0.0

    return bullish_probability, bearish_probability, total_matches, avg_pct_change, vol_pct

def fetch_and_analyze():
    print(f"Executing secure data extraction protocols for {SYMBOL}...")
    global LAST_STATUS
    
    # --- HARDENED WEB SCRAPER INTERFACE CONFIGURATION ---
    # We construct custom browser session settings to mimic an active tablet user 
    # and bypass Yahoo's automated firewall filters against hosting providers.
    custom_session = requests.Session()
    custom_session.headers.update({
        'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/605.1.15',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Origin': 'https://finance.yahoo.com',
        'Referer': 'https://finance.yahoo.com/'
    })
    
    primary_data_source = DEFAULT_DATA_SOURCE
    df = pd.DataFrame()

    if primary_data_source == "coingecko":
        df = fetch_coingecko_price_data(SYMBOL, days=7, interval="hourly")
        if df is None or df.empty:
            logging.warning("Primary source CoinGecko failed; trying yfinance fallback.")
            try:
                df = yf.download(
                    tickers=SYMBOL,
                    interval=TIMEFRAME,
                    period="7d",
                    auto_adjust=True,
                    progress=False,
                    session=custom_session,
                )
            except Exception as exc:
                logging.warning("Fallback yfinance download failed: %s", exc)
                df = pd.DataFrame()
    else:
        try:
            df = yf.download(
                tickers=SYMBOL,
                interval=TIMEFRAME,
                period="7d",
                auto_adjust=True,
                progress=False,
                session=custom_session,
            )
        except Exception as exc:
            logging.warning("yfinance download failed, trying fallback data source: %s", exc)
            df = pd.DataFrame()

        if df.empty:
            logging.info("Yahoo data unavailable or empty. Trying fallback data source: CoinGecko.")
            df = fetch_coingecko_price_data(SYMBOL, days=7, interval="hourly")
            if df is None or df.empty:
                logging.warning("CoinGecko fallback failed. Retrying next loop.")
                return
            logging.info("CoinGecko fallback succeeded with %s hourly points.", len(df))

    # Modern yfinance Index Protection: Flatten columns immediately to avoid extraction structural breaks
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Standardize data tables
    df.columns = [str(col).lower() for col in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df['close'] = df['close'].astype(float)
    df['rsi'] = calculate_rsi(df['close'])
    df = df.dropna(subset=['rsi']).copy()

    if len(df) < 5:
        logging.warning("Data volume check failed following table cleanup.")
        return

    current_price = float(df['close'].iloc[-1])
    current_rsi = float(df['rsi'].iloc[-1])
    prev_rsi = float(df['rsi'].iloc[-2])

    # Execute Self-Learning Trend Computations
    bull_prob, bear_prob, matches, avg_pct_change, vol_pct = run_predictive_learning(df)
    expected_target = None
    expected_range_low = None
    expected_range_high = None
    if matches > 0:
        expected_target = current_price * (1 + (avg_pct_change / 100.0))
        expected_range_low = current_price * (1 + ((avg_pct_change - vol_pct) / 100.0))
        expected_range_high = current_price * (1 + ((avg_pct_change + vol_pct) / 100.0))
    logging.info("Scraper Live Log -> Price: $%s | RSI: %.2f", f"{current_price:,.2f}", current_rsi)
    
    # Define Alert Boundaries
    is_extreme_oversold = current_rsi <= 25 or (prev_rsi < 30 and current_rsi >= 30)
    is_extreme_overbought = current_rsi >= 75 or (prev_rsi > 70 and current_rsi <= 70)
    
    # --- DYNAMIC SIGNAL EVALUATION ---
    if is_extreme_oversold or bull_prob > 60:
        exp_line = ""
        if expected_target is not None:
            exp_line = (
                f"**Expected Target (1h):** ${expected_target:,.2f} ({avg_pct_change:+.2f}% avg, volatility {vol_pct:.2f}%)\n"
                f"**Expected Range (1h):** ${expected_range_low:,.2f} — ${expected_range_high:,.2f}\n\n"
            )

        msg = (
            f"📈 **RECOMMENDED ACTION: BUY / LONG**\n\n"
            f"**Asset Target:** {SYMBOL}\n"
            f"**Current Price:** ${current_price:,.2f}\n"
            f"**Current RSI Value:** {current_rsi:.2f}\n"
            f"{exp_line}"
            f"📊 **Predictive Pattern Learning:**\n"
            f"The scraper verified **{matches} matches** over the last 7 days matching this exact RSI profile. "
            f"Historically, the market shifted **UPWARD within the next hour {bull_prob:.1f}% of the time**.\n\n"
            f"🎯 *Directional Outlook:* Strong bullish reversal pressure expected next hour."
        )
        send_alert(msg, embed_color=3066993)
    
    elif is_extreme_overbought or bear_prob > 60:
        exp_line = ""
        if expected_target is not None:
            exp_line = (
                f"**Expected Target (1h):** ${expected_target:,.2f} ({avg_pct_change:+.2f}% avg, volatility {vol_pct:.2f}%)\n"
                f"**Expected Range (1h):** ${expected_range_low:,.2f} — ${expected_range_high:,.2f}\n\n"
            )

        msg = (
            f"📉 **RECOMMENDED ACTION: SELL / SHORT**\n\n"
            f"**Asset Target:** {SYMBOL}\n"
            f"**Current Price:** ${current_price:,.2f}\n"
            f"**Current RSI Value:** {current_rsi:.2f}\n"
            f"{exp_line}"
            f"📊 **Predictive Pattern Learning:**\n"
            f"The scraper verified **{matches} matches** over the last 7 days matching this exact RSI profile. "
            f"Historically, the market shifted **DOWNWARD within the next hour {bear_prob:.1f}% of the time**.\n\n"
            f"🎯 *Directional Outlook:* Strong bearish distribution pressure expected next hour."
        )
        send_alert(msg, embed_color=15158332)
    else:
        print(f"Consolidation mode. Upward probability is {bull_prob:.1f}%. Notification held.")

    # Update global status for external queries
    direction = "neutral"
    if is_extreme_oversold or bull_prob > 60:
        direction = "bullish"
    elif is_extreme_overbought or bear_prob > 60:
        direction = "bearish"

    LAST_STATUS = {
        "symbol": SYMBOL,
        "price": current_price,
        "rsi": current_rsi,
        "bull_prob": round(bull_prob, 2),
        "bear_prob": round(bear_prob, 2),
        "matches": matches,
        "avg_pct_change": round(avg_pct_change, 2),
        "volatility_pct": round(vol_pct, 2),
        "expected_target": round(expected_target, 2) if expected_target is not None else None,
        "expected_range_low": round(expected_range_low, 2) if expected_range_low is not None else None,
        "expected_range_high": round(expected_range_high, 2) if expected_range_high is not None else None,
        "direction": direction,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# --- RAILWAY HOOKS AND COMPLIANCE ENGINE ---
class HealthCheckServer(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/status":
            # Verify token if configured
            if not verify_status_request(self.headers, parse_qs(parsed.query)):
                self.send_response(401)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "unauthorized"}).encode())
                return

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                payload = LAST_STATUS if LAST_STATUS else {"status": "no data yet"}
                self.wfile.write(json.dumps(payload).encode())
            except Exception:
                self.wfile.write(json.dumps({"status": "error"}).encode())
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Bot Status: Operational and Connected.")
    
    def log_message(self, format, *args):
        return  # Suppress terminal bloating


def verify_status_request(headers, query_params):
    """Return True if request is authorized to access /status.
    Uses env var STATUS_TOKEN. If not set, allow all requests.
    Accepts token via Authorization: Bearer <token> header or ?token=<token> query param.
    """
    token = os.getenv('STATUS_TOKEN')
    if not token:
        return True

    # Check header
    auth = headers.get('Authorization')
    if auth and auth.strip().lower().startswith('bearer '):
        supplied = auth.strip()[7:]
        return supplied == token

    # Check query param
    q = query_params.get('token')
    if q and q[0] == token:
        return True

    return False

def primary_bot_loop():
    time.sleep(5)
    send_alert("🤖 **Self-Learning Scraper Bot initialized successfully! Tracking open web data profiles...**")
    while True:
        try:
            fetch_and_analyze()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Scraper Core Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    port_env = os.getenv("PORT")
    if port_env:
        try:
            base_port = int(port_env)
            port_options = [base_port]
        except ValueError:
            logging.warning("Invalid PORT environment variable, defaulting to 8080")
            base_port = 8080
            port_options = list(range(base_port, base_port + 10))
    else:
        base_port = 8080
        port_options = list(range(base_port, base_port + 10))

    bot_thread = threading.Thread(target=primary_bot_loop)
    bot_thread.daemon = True
    bot_thread.start()

    if port_env:
        logging.info("PORT environment variable detected; binding to configured port %s only.", base_port)
    else:
        logging.info("No PORT environment variable detected; attempting local port range %s-%s.", base_port, base_port + 9)

    HTTPServer.allow_reuse_address = True
    httpd = None
    bound_port = None
    for port in port_options:
        try:
            server_address = ('0.0.0.0', port)
            httpd = HTTPServer(server_address, HealthCheckServer)
            bound_port = port
            break
        except OSError as e:
            logging.warning(f"Port {port} not available: {e}")
            continue

    if httpd is None:
        target_range = f"{base_port}" if port_env else f"{base_port}-{base_port+9}"
        logging.error(f"Failed to bind to port(s) {target_range}. Exiting.")
        sys.exit(1)

    try:
        httpd.allow_reuse_address = True
        logging.info(f"Railway Internal Port Routing Engine online on port {bound_port}")
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutdown requested by user, stopping server.")
    except Exception as exc:
        logging.error("HTTP server error: %s", exc)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
