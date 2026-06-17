from unittest.mock import patch
import json

from bot import fetch_price_from_coingecko


class MockResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


def test_fetch_price_from_coingecko_success():
    sample = {
        "prices": [
            [1620000000000, 100.0],
            [1620003600000, 101.5]
        ]
    }

    with patch('requests.get') as mock_get:
        mock_get.return_value = MockResponse(sample, status=200)
        res = fetch_price_from_coingecko('BTC-USD')
        assert res is not None
        assert res['price'] == 101.5
        assert res['prev_price'] == 100.0
