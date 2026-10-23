"""Expired account credentials must never disable public signal market data."""

import os
import unittest
from unittest.mock import Mock, patch

import ccxt

from core.data_feed import DataFeed
from core.market_scanner import MarketScanner


BYBIT = ccxt.bybit


def exchange_with_public_data(config):
    exchange = BYBIT(config)
    spot = config["options"]["defaultType"] == "spot"
    symbol = "BTC/USDT" if spot else "BTC/USDT:USDT"
    exchange.privateGetV5AssetCoinQueryInfo = Mock(
        side_effect=ccxt.AuthenticationError("Your api key has expired.")
    )
    exchange.fetch_markets = Mock(return_value=[{
        "id": "BTCUSDT", "symbol": symbol,
        "base": "BTC", "quote": "USDT", "settle": None if spot else "USDT",
        "baseId": "BTC", "quoteId": "USDT", "settleId": None if spot else "USDT",
        "type": "spot" if spot else "swap", "spot": spot, "swap": not spot,
        "contract": not spot, "linear": not spot, "inverse": False,
        "active": True, "precision": {}, "limits": {},
    }])
    exchange.fetch_ticker = Mock(return_value={"last": 100.0})
    exchange.fetch_tickers = Mock(return_value={
        symbol: {"last": 100.0, "quoteVolume": 60_000_000.0}
    })
    exchange.fetch_ohlcv = Mock(return_value=[
        [i * 60_000, 100, 102, 99, 101, 10] for i in range(4)
    ])
    # No accidental network calls are allowed in these regression tests.
    exchange.fetch = Mock(side_effect=AssertionError("Unexpected network request"))
    return exchange


class PublicMarketDataTests(unittest.TestCase):
    def test_authenticated_market_loading_reproduces_expired_key_failure(self):
        exchange = exchange_with_public_data({
            "apiKey": "expired-key", "secret": "expired-secret",
            "options": {"defaultType": "swap"},
        })
        with self.assertRaises(ccxt.AuthenticationError):
            exchange.load_markets()

    def test_feed_reads_prices_and_closed_candles_with_expired_legacy_keys(self):
        for market_type in ("spot", "swap"):
            with self.subTest(market_type=market_type), patch.dict(
                os.environ, {"EXCHANGE_TYPE": market_type}
            ), patch("ccxt.bybit", side_effect=exchange_with_public_data):
                feed = DataFeed("expired-key", "expired-secret")
                self.assertEqual(feed.get_last_price("BTCUSDT"), 100.0)
                candles = feed.get_klines("BTCUSDT", "1m", limit=4)
                self.assertEqual(len(candles), 3)
                self.assertEqual(list(candles.columns), [
                    "open", "high", "low", "close", "volume"
                ])
                expected = "BTC/USDT" if market_type == "spot" else "BTC/USDT:USDT"
                feed.client.fetch_ticker.assert_called_once_with(expected)
                feed.client.privateGetV5AssetCoinQueryInfo.assert_not_called()
                self.assertFalse(feed.client.apiKey)
                self.assertFalse(feed.client.secret)

    def test_scanner_keeps_volume_filter_without_private_authentication(self):
        for market_type in ("spot", "swap"):
            with self.subTest(market_type=market_type), patch.dict(
                os.environ, {"BYBIT_MARKET_TYPE": market_type, "BYBIT_TESTNET": "0"}
            ), patch("ccxt.bybit", side_effect=exchange_with_public_data):
                scanner = MarketScanner("expired-key", "expired-secret", 50_000_000)
                self.assertEqual(scanner.scan(), ["BTCUSDT"])
                scanner.min_volume = 70_000_000
                self.assertEqual(scanner.scan(), [])
                scanner.ex.privateGetV5AssetCoinQueryInfo.assert_not_called()
                self.assertFalse(scanner.ex.apiKey)
                self.assertFalse(scanner.ex.secret)


if __name__ == "__main__":
    unittest.main()
