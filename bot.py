import requests
import pandas as pd

# Hardcoded Discord Webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1516519439963783361/8x8Kz3MNwbiKdtSnDQKQe3fbMCvUiHcqbZe9Lii4rJ8pF-NcWe-8GAjjmxzT98-C7igK"

def get_bitcoin_analysis():
    """Fetch 1-hour candles from Binance and calculate RSI + Moving Averages."""
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "limit": 100
    }
    response = requests.get(url, params=params, timeout=10)
    data = response.json()

    # Extract close prices into a pandas DataFrame
    closes = [float(candle[4]) for candle in data]
    df = pd.DataFrame(closes, columns=['close'])

    current_price = df['close'].iloc[-1]

    # Calculate Moving Averages (SMA 20 & SMA 50)
    df['sma_20'] = df['close'].rolling(window=20).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()
    
    sma_20 = df['sma_20'].iloc[-1]
    sma_50 = df['sma_50'].iloc[-1]

    # Calculate Relative Strength Index (RSI - 14 period)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    rsi = df['rsi'].iloc[-1]

    # Interpret Indicators
    if rsi >= 70:
        rsi_status = "⚠️ Overbought (Risk of pullback)"
    elif rsi <= 30:
        rsi_status = "🟢 Oversold (Potential buy zone)"
    else:
        rsi_status = "⚖️ Neutral"

    trend_status = "🐂 Bullish (Above SMA 50)" if current_price > sma_50 else "🐻 Bearish (Below SMA 50)"

    # Construct Discord Markdown Message
    message = (
        f"📊 **BITCOIN TECHNICAL ANALYSIS (1H)** 📊\n\n"
        f"💰 **Current Price:** ${current_price:,.2f} USDT\n\n"
        f"📈 **Indicators:**\n"
        f"• **RSI (14):** `{rsi:.2f}` → {rsi_status}\n"
        f"• **20 SMA:** ${sma_20:,.2f}\n"
        f"• **50 SMA:** ${sma_50:,.2f}\n\n"
        f"🔍 **Market Condition:** {trend_status}"
    )
    return message

def send_discord_alert(message):
    """Send analysis report directly to Discord channel via Webhook."""
    payload = {"content": message}
    res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    
    if res.status_code in [200, 204]:
        print("✅ Indicator report sent to Discord successfully!")
    else:
        print(f"❌ Discord Webhook Error: {res.status_code} - {res.text}")

if __name__ == "__main__":
    print("Running Bitcoin technical analysis for Discord...")
    analysis_report = get_bitcoin_analysis()
    send_discord_alert(analysis_report)
    print("Execution complete. Exiting.")
    
