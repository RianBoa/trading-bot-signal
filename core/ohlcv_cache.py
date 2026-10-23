from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import pandas as pd


@dataclass
class CacheEntry:
    df: pd.DataFrame
    fetched_at: float


class OHLCVCache:
    """
    Lightweight in-memory TTL cache for OHLCV frames.
    Key: (symbol, timeframe, limit)
    """

    def __init__(self, default_ttl_sec: float = 30.0, ttl_by_tf: Optional[Dict[str, float]] = None):
        self.default_ttl_sec = float(default_ttl_sec)
        self.ttl_by_tf = {k.lower(): float(v) for k, v in (ttl_by_tf or {}).items()}
        self._entries: Dict[Tuple[str, str, int], CacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._stale_fallbacks = 0

    def _ttl_for(self, timeframe: str) -> float:
        return float(self.ttl_by_tf.get((timeframe or "").lower(), self.default_ttl_sec))

    def get_or_fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        fetch_fn: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        key = (str(symbol), str(timeframe).lower(), int(limit))
        now = time.time()
        ttl = self._ttl_for(timeframe)

        cur = self._entries.get(key)
        if cur is not None and (now - cur.fetched_at) < ttl:
            self._hits += 1
            return cur.df

        self._misses += 1
        fresh = fetch_fn()
        if fresh is not None and not fresh.empty:
            self._entries[key] = CacheEntry(df=fresh, fetched_at=now)
            return fresh

        # If fetch failed/empty, return stale cache if available
        if cur is not None:
            self._stale_fallbacks += 1
            return cur.df
        return pd.DataFrame()

    def stats(self) -> Dict[str, int]:
        return {
            "hits": int(self._hits),
            "misses": int(self._misses),
            "stale_fallbacks": int(self._stale_fallbacks),
            "entries": int(len(self._entries)),
        }

