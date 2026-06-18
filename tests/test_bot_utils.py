import os
import sys
import pandas as pd

# Ensure repo root is on path so tests can import `bot`
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from bot import calculate_rsi


def test_calculate_rsi_basic():
    # Create a monotonically increasing price series
    prices = pd.Series([i for i in range(1, 31)], dtype=float)
    rsi = calculate_rsi(prices, window=14)
    # RSI should be a pandas Series and last value between 0 and 100
    assert hasattr(rsi, 'iloc')
    last = float(rsi.iloc[-1])
    assert 0.0 <= last <= 100.0


def test_fetch_coingecko_fallback_to_yfinance(monkeypatch):
    import bot

    class DummyResponse:
        status_code = 429
        def json(self):
            return {}

    class DummySession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            return DummyResponse()

    def dummy_download(*args, **kwargs):
        return pd.DataFrame({
            'Open': [1.0 + i for i in range(20)],
            'High': [1.0 + i for i in range(20)],
            'Low': [1.0 + i for i in range(20)],
            'Close': [1.0 + i for i in range(20)],
            'Volume': [1000] * 20,
        })

    monkeypatch.setattr(bot, 'build_request_session', lambda: DummySession())
    monkeypatch.setattr(bot, 'DEFAULT_DATA_SOURCE', 'coingecko')
    monkeypatch.setattr(bot.yf, 'download', dummy_download)
    monkeypatch.setattr(bot, 'send_alert', lambda *args, **kwargs: None)

    bot.fetch_and_analyze()
    assert bot.LAST_STATUS.get('symbol') == 'BTC-USD'
    assert bot.LAST_STATUS.get('direction') in {'bullish', 'bearish', 'neutral'}
