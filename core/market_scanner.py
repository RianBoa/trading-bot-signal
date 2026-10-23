# core/market_scanner.py
from __future__ import annotations

import os
from typing import List, Tuple

import ccxt


class MarketScanner:
    """
    Bybit scanner via CCXT.

    Returns symbols like: BTCUSDT, ETHUSDT, SUIUSDT ...
    Uses Bybit tickers and filters by 24h quote volume in USDT.

    Notes:
    - CCXT unified symbols are like "BTC/USDT".
    - We convert them to "BTCUSDT" to match your bot's symbol format.
    - We skip stablecoin bases (USDC/USDT etc).
    """

    def __init__(self, api_key: str = "", api_secret: str = "", min_volume_24h: float = 50_000_000):
        self.min_volume = float(min_volume_24h)

        # stable bases to skip
        self.stable_bases = {"USDC", "BUSD", "TUSD", "USDP", "PAX", "FDUSD", "DAI"}

        # Choose market type: "spot" or "swap"
        # Default swap, because most USDT pairs/liquidity are there
        market_type = os.getenv("BYBIT_MARKET_TYPE", "swap").strip().lower()
        if market_type not in ("spot", "swap"):
            market_type = "swap"
        self.market_type = market_type

        # Only public market data is needed. Legacy credential arguments are
        # intentionally ignored so expired keys cannot break load_markets().
        self.ex = ccxt.bybit(
            {
                "enableRateLimit": True,
                "options": {
                    "defaultType": self.market_type,  # spot | swap
                },
            }
        )

        # some people use demo/testnet; keep it optional
        if os.getenv("BYBIT_TESTNET", "0").strip().lower() in ("1", "true", "yes", "y", "on"):
            try:
                self.ex.set_sandbox_mode(True)
            except Exception:
                pass

    @staticmethod
    def _to_bot_symbol(ccxt_symbol: str) -> str:
        """
        CCXT Bybit часто возвращает:
          - spot: "BTC/USDT"
          - swap: "BTC/USDT:USDT"
        Нам нужно: "BTCUSDT" (один раз).
        """
        base = ccxt_symbol.split("/")[0].strip()
        return f"{base}USDT"

    def scan(self) -> List[str]:
        """
        Return list of symbols: XXXUSDT, sorted by quote volume desc,
        filtered by min_volume_24h.
        """
        # Load markets so the exchange knows available symbols
        markets = self.ex.load_markets()

        # Fetch all tickers in one call (Bybit supports)
        tickers = self.ex.fetch_tickers()

        candidates: List[Tuple[str, float]] = []
        for sym, t in tickers.items():
            # CCXT symbol style: "BTC/USDT" or "BTC/USDT:USDT"
            if not isinstance(sym, str) or "/USDT" not in sym:
                continue

            # Ensure market exists and matches our chosen type (spot/swap)
            m = markets.get(sym)
            if not m:
                continue

            if self.market_type == "spot" and not m.get("spot", False):
                continue
            if self.market_type == "swap" and not m.get("swap", False):
                continue

            base = m.get("base")
            quote = m.get("quote")

            if quote != "USDT":
                continue
            if base in self.stable_bases:
                continue

            # CCXT unified ticker fields:
            qv = t.get("quoteVolume")

            # fallback: baseVolume * last
            if qv is None:
                bv = t.get("baseVolume")
                last = t.get("last")
                try:
                    if bv is not None and last is not None:
                        qv = float(bv) * float(last)
                except Exception:
                    qv = None

            if qv is None:
                continue

            try:
                qv = float(qv)
            except Exception:
                continue

            if qv >= self.min_volume:
                candidates.append((sym, qv))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # convert "BTC/USDT" or "BTC/USDT:USDT" -> "BTCUSDT"
        out = [self._to_bot_symbol(s) for s, _ in candidates]
        return out

