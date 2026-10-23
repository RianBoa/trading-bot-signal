# core/data_feed.py
from __future__ import annotations

import os
from typing import Optional

import pandas as pd
import ccxt


class DataFeed:
    def __init__(self, api_key: str = "", api_secret: str = ""):
        """
        Bybit через ccxt. Поддержка spot/swap через EXCHANGE_TYPE.
        """
        self.debug = os.getenv("FEED_DEBUG", "0").strip().lower() in ("1", "true", "yes", "y", "on")
        self.ex_type = (os.getenv("EXCHANGE_TYPE", "swap") or "swap").strip().lower()  # spot | swap

        # Market data is public. Keep the arguments for existing callers, but
        # never attach credentials: load_markets() otherwise also requests
        # private currency metadata and an expired key prevents startup.
        self.client = ccxt.bybit({
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap" if self.ex_type == "swap" else "spot",
            }
        })
        self.client.load_markets()

    def _log(self, msg: str) -> None:
        if self.debug:
            print(msg)

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """
        Внутренний формат бота: BTCUSDT
        ccxt bybit:
          spot: BTC/USDT
          swap: BTC/USDT:USDT
        """
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT" if self.ex_type == "swap" else f"{base}/USDT"
        return symbol

    def get_last_price(self, symbol: str) -> Optional[float]:
        """
        Live price для трекинга.
        """
        try:
            sym = self._to_ccxt_symbol(symbol)
            t = self.client.fetch_ticker(sym)
            last = t.get("last")
            if last is None:
                return None
            return float(last)
        except Exception as e:
            self._log(f"[FEED] fetch_ticker failed for {symbol}: {e}")
            return None

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        """
        interval: '1d','4h','1h','15m','5m'
        """
        try:
            sym = self._to_ccxt_symbol(symbol)
            raw = self.client.fetch_ohlcv(sym, timeframe=interval, limit=limit)
        except Exception as e:
            self._log(f"[FEED] fetch_ohlcv failed for {symbol} {interval}: {e}")
            return pd.DataFrame()

        if not raw:
            return pd.DataFrame()

        df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume"])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        # DROP_LAST_INPROGRESS_CANDLE (как у тебя было)
        if len(df) > 2:
            df = df.iloc[:-1].copy()

        return df[["open", "high", "low", "close", "volume"]]
