import pandas as pd
import math
import pytest

from bot import calculate_rsi


@pytest.mark.parametrize("i", range(100))
def test_calculate_rsi_repeated(i):
    # simple increasing series to ensure RSI produces numeric output
    prices = pd.Series([float(x) for x in range(1, 21)])
    rsi = calculate_rsi(prices, window=14)
    # last RSI value should be a finite float (not NaN after window)
    val = float(rsi.dropna().iloc[-1])
    assert math.isfinite(val)
