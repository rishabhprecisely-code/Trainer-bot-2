import os
import sys
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import bot


def make_price_df(n=48):
    # hourly points
    timestamps = pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n, freq="h")
    values = [10000.0 + i for i in range(n)]
    return pd.DataFrame(
        {"timestamp": timestamps, "open": values, "high": values, "low": values, "close": values, "volume": [1000] * n}
    )


def test_generate_market_signals(monkeypatch, tmp_path):
    # monkeypatch data fetchers to return deterministic data
    monkeypatch.setattr(bot, "fetch_coingecko_price_data", lambda *args, **kwargs: make_price_df())
    monkeypatch.setattr(bot, "fetch_yfinance_price_data", lambda *args, **kwargs: make_price_df())

    # prevent real webhook calls
    class DummyWebhook:
        def __init__(self, url):
            self.url = url

        def send_embed(self, title, description=None, color=0):
            return True

    monkeypatch.setattr(bot, "DiscordWebhookClient", DummyWebhook)

    # use a config with default settings
    cfg = bot.get_config()
    bot.generate_market_signals(cfg)

    assert "signals" in bot.LAST_STATUS
    assert isinstance(bot.LAST_STATUS["signals"], list)
    assert len(bot.LAST_STATUS["signals"]) == 3
