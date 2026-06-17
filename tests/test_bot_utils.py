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
