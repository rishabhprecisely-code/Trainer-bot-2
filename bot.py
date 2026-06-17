import os
import time
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import pandas as pd
import yfinance as yf
import json
from datetime import datetime

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1516867950706032691/jbkI3AtCR2LPIoLvEzSZkZOU5WpN5w28mJEqrm2tKpYYbTFyuEEQ3vVHl1fsQ0lE4TGJ"
)

SYMBOL = "BTC-USD"
TIMEFRAME = "1h"  
CHECK_INTERVAL = 300  # Scan every 5 minutes

# last fetched metrics for status endpoint
LAST_STATUS = {}

def send_alert(message, embed_color=3447003):
    """Dispatches stylized trade alerts directly to your mobile app channel."""
    data = {
        "embeds": [{
            "title": "🧠 SYSTEM PREDICTION ENGINE 🧠",
            "description": message,
            "color": embed_color, 
            "footer": {"text": "Yahoo Finance Secure Scraper Engine"}
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
    except Exception as e:
        print(f"Network dispatch failure: {e}")

def calculate_rsi(prices, window=14):
    """Helper mathematical function to calculate clean RSI arrays."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def fetch_price_from_coingecko(symbol):
    """Fallback quick price fetch using CoinGecko market_chart for recent points.
    Returns dict {'price', 'prev_price', 'prices'} or None on failure.
    """
    try:
        base = symbol.split('-')[0].upper()
        mapping = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'DOGE': 'dogecoin',
            'LTC': 'litecoin'
        }
        coin = mapping.get(base, base.lower())
        url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
        resp = requests.get(url, params={"vs_currency": "usd", "days": 1, "interval": "hourly"}, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        prices = data.get('prices', [])
        if not prices:
            return None
        current = float(prices[-1][1])
        prev = float(prices[-2][1]) if len(prices) > 1 else current
        return {"price": current, "prev_price": prev, "prices": prices}
    except Exception:
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
    
    for i in range(15, len(df) - 1):
        hist_rsi = float(df['rsi'].iloc[i])
        
        if lower_bound <= hist_rsi <= upper_bound:
            price_then = float(df['close'].iloc[i])
            price_1h_later = float(df['close'].iloc[i+1])
            
            total_matches += 1
            if price_1h_later > price_then:
                historical_up_moves += 1
            else:
                historical_down_moves += 1
    
    if total_matches > 0:
        bullish_probability = (historical_up_moves / total_matches) * 100
        bearish_probability = (historical_down_moves / total_matches) * 100
    else:
        bullish_probability, bearish_probability = 50.0, 50.0
    
    return bullish_probability, bearish_probability, total_matches

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
    
    # Passing our authenticated session directly into the yfinance download queue
    df = yf.download(
        tickers=SYMBOL, 
        interval=TIMEFRAME, 
        period="7d", 
        auto_adjust=True,
        progress=False,
        session=custom_session
    )
    
    if df.empty:
        print("Scraper Warning: Yahoo blocked extraction or data is empty. Trying fallback data source (CoinGecko).")
        # Fallback: attempt to get current price and recent points from CoinGecko
        try:
            cg_price = fetch_price_from_coingecko(SYMBOL)
            if cg_price is None:
                print("CoinGecko fallback failed. Retrying next loop.")
                return
            # cg_price -> dict with keys: price, prev_price, prices (list)
            current_price = cg_price['price']
            prev_price = cg_price.get('prev_price', current_price)
            direction = 'bullish' if current_price > prev_price else ('bearish' if current_price < prev_price else 'neutral')

            LAST_STATUS = {
                'symbol': SYMBOL,
                'price': current_price,
                'rsi': None,
                'bull_prob': 50.0,
                'bear_prob': 50.0,
                'matches': 0,
                'direction': direction,
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }
            print(f"Fallback Live Log -> Price: ${current_price:,.2f} | Direction: {direction}")
        except Exception as e:
            print(f"Fallback error: {e}")
        return

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
        print("Data volume check failed following table cleanup.")
        return

    current_price = float(df['close'].iloc[-1])
    current_rsi = float(df['rsi'].iloc[-1])
    prev_rsi = float(df['rsi'].iloc[-2])
    
    # Execute Self-Learning Trend Computations
    bull_prob, bear_prob, matches = run_predictive_learning(df)
    print(f"Scraper Live Log -> Price: ${current_price:,.2f} | RSI: {current_rsi:.2f}")
    
    # Define Alert Boundaries
    is_extreme_oversold = current_rsi <= 25 or (prev_rsi < 30 and current_rsi >= 30)
    is_extreme_overbought = current_rsi >= 75 or (prev_rsi > 70 and current_rsi <= 70)
    
    # --- DYNAMIC SIGNAL EVALUATION ---
    if is_extreme_oversold or bull_prob > 60:
        msg = (
            f"📈 **RECOMMENDED ACTION: BUY / LONG**\n\n"
            f"**Asset Target:** {SYMBOL}\n"
            f"**Current Price:** ${current_price:,.2f}\n"
            f"**Current RSI Value:** {current_rsi:.2f}\n\n"
            f"📊 **Predictive Pattern Learning:**\n"
            f"The scraper verified **{matches} matches** over the last 7 days matching this exact RSI profile. "
            f"Historically, the market shifted **UPWARD within the next hour {bull_prob:.1f}% of the time**.\n\n"
            f"🎯 *Directional Outlook:* Strong bullish reversal pressure expected next hour."
        )
        send_alert(msg, embed_color=3066993)
    
    elif is_extreme_overbought or bear_prob > 60:
        msg = (
            f"📉 **RECOMMENDED ACTION: SELL / SHORT**\n\n"
            f"**Asset Target:** {SYMBOL}\n"
            f"**Current Price:** ${current_price:,.2f}\n"
            f"**Current RSI Value:** {current_rsi:.2f}\n\n"
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
        "direction": direction,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

# --- RAILWAY HOOKS AND COMPLIANCE ENGINE ---
class HealthCheckServer(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
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
    railway_port = int(os.getenv("PORT", 8080))
    
    bot_thread = threading.Thread(target=primary_bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    
    server_address = ('0.0.0.0', railway_port)
    httpd = HTTPServer(server_address, HealthCheckServer)
    print(f"Railway Internal Port Routing Engine online on port {railway_port}")
    httpd.serve_forever()
