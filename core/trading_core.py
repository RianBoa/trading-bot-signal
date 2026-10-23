from __future__ import annotations

import json
from typing import List, Optional

from common.symbols import _is_ascii_symbol, _normalize_symbol
from core.data_feed import DataFeed
from core.market_scanner import MarketScanner
from core.m1.ai_live import AILive
from core.m1.config import AIConfig
from core.m1.store import TradeStore
from core.ohlcv_cache import OHLCVCache
from core.settings import Settings
from core.strategy_config import load_narrative_config
from core.strategy_narrative import NarrativeStrategy
from core.trade_lifecycle import TradeLifecycleManager
from core.trade_state_store import TradeStateStore


BLACKLIST_SYMBOLS = {"XAUUSDT", "PAXGUSDT", "BANKUSDT"}


def _safe_make_feed(api_key: str, api_secret: str) -> DataFeed:
    try:
        feed = DataFeed(api_key, api_secret)  # type: ignore[arg-type]
        return feed
    except TypeError:
        feed = DataFeed()  # type: ignore[call-arg]
        init_fn = getattr(feed, "init", None)
        if callable(init_fn):
            init_fn(api_key, api_secret)
        return feed


def _safe_make_scanner(
    api_key: str,
    api_secret: str,
    min_vol_24h: float,
) -> MarketScanner:
    try:
        sc = MarketScanner(
            api_key,
            api_secret,
            min_vol_24h,
        )  # type: ignore[arg-type]
        return sc
    except TypeError:
        sc = MarketScanner()  # type: ignore[call-arg]
        init_fn = getattr(sc, "init", None)
        if callable(init_fn):
            init_fn(
                api_key,
                api_secret,
                min_vol_24h,
            )
        return sc


class Core:
    def __init__(
        self,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or Settings.from_env()

        self.feed = _safe_make_feed(
            self.settings.api_key,
            self.settings.api_secret,
        )

        self.strategy = NarrativeStrategy(
            load_narrative_config()
        )

        self.scanner = _safe_make_scanner(
            self.settings.api_key,
            self.settings.api_secret,
            self.settings.min_volume_24h,
        )

        trade_state_path = (
            self.settings.bot_state_path.parent
            / "trade_lifecycle_state.json"
        )
        self.trade_lifecycle = TradeLifecycleManager(
            TradeStateStore(trade_state_path)
        )

        self.ohlcv_cache = OHLCVCache(
            default_ttl_sec=float(
                self.settings.ohlcv_cache_ttl_sec
            ),
            ttl_by_tf=dict(
                self.settings.ohlcv_cache_ttl_by_tf
            ),
        )

        self.ai_cfg = AIConfig()
        self.ai_store = TradeStore(
            self.ai_cfg
        )

        self.ai = AILive(
            self.ai_cfg,
            self.ai_store,
            self.strategy,
        )

        self._ticks = 0

        self._cache_metrics_every = int(
            self.settings.ohlcv_cache_metrics_every
        )

        self.ascii_only_symbols = bool(
            self.settings.symbol_ascii_only
        )

    def _log_signal_summary(
        self,
        results: dict,
        symbols: List[str],
    ) -> None:
        counts: dict[str, int] = {}
        examples: dict[str, List[str]] = {}

        for symbol, sig in results.items():
            if not isinstance(sig, dict):
                state = "INVALID"
            else:
                state = str(
                    sig.get("signal") or "UNKNOWN"
                ).upper()

            counts[state] = (
                counts.get(state, 0) + 1
            )

            sample = examples.setdefault(
                state,
                [],
            )
            if len(sample) < 3:
                sample.append(str(symbol))

        counts_json = json.dumps(
            counts,
            ensure_ascii=False,
            sort_keys=True,
        )
        examples_json = json.dumps(
            examples,
            ensure_ascii=False,
            sort_keys=True,
        )

        print(
            f"[SIGNALS] ticks={self._ticks} "
            f"symbols={len(symbols)} "
            f"active_trades={len(self.trade_lifecycle.active_trades)} "
            f"counts={counts_json} "
            f"examples={examples_json}"
        )

        detail_states = {
            "NO_TRIGGER",
            "WAIT_PHASE",
            "ENTER",
            "ERROR",
        }
        for symbol, sig in results.items():
            if not isinstance(sig, dict):
                continue

            state = str(
                sig.get("signal") or "UNKNOWN"
            ).upper()
            if state not in detail_states:
                continue

            parts = [
                "[SIGNALS][DETAIL]",
                f"sym={symbol}",
                f"state={state}",
            ]

            for key in (
                "setup",
                "vc",
                "reason",
                "debug",
                "trigger_reason",
                "error",
            ):
                value = sig.get(key)
                if value:
                    parts.append(
                        f"{key}={str(value)}"
                    )

            print(" ".join(parts))

    def _get_dynamic_symbols(self) -> List[str]:
        symbols = self.scanner.scan() or []

        out: List[str] = []
        seen = set()

        for s in symbols:
            sym = _normalize_symbol(str(s))

            if sym in BLACKLIST_SYMBOLS:
                continue

            if (
                self.ascii_only_symbols
                and not _is_ascii_symbol(sym)
            ):
                continue

            if sym in seen:
                continue

            seen.add(sym)
            out.append(sym)

            if len(out) >= int(
                self.settings.max_symbols
            ):
                break

        for sym in self.trade_lifecycle.active_symbols():
            if sym in BLACKLIST_SYMBOLS:
                self.trade_lifecycle.drop_symbol(sym)
                continue

            if sym and sym not in seen:
                out.append(sym)
                seen.add(sym)

        return out

    def _build_tf_data(
        self,
        symbol: str,
    ) -> dict:
        limit = 300

        return {
            "D": self.ohlcv_cache.get_or_fetch(
                symbol,
                "1d",
                limit,
                lambda: self.feed.get_klines(
                    symbol,
                    "1d",
                    limit,
                ),
            ),
            "4H": self.ohlcv_cache.get_or_fetch(
                symbol,
                "4h",
                limit,
                lambda: self.feed.get_klines(
                    symbol,
                    "4h",
                    limit,
                ),
            ),
            "1H": self.ohlcv_cache.get_or_fetch(
                symbol,
                "1h",
                limit,
                lambda: self.feed.get_klines(
                    symbol,
                    "1h",
                    limit,
                ),
            ),
            "15M": self.ohlcv_cache.get_or_fetch(
                symbol,
                "15m",
                limit,
                lambda: self.feed.get_klines(
                    symbol,
                    "15m",
                    limit,
                ),
            ),
            "5M": self.ohlcv_cache.get_or_fetch(
                symbol,
                "5m",
                limit,
                lambda: self.feed.get_klines(
                    symbol,
                    "5m",
                    limit,
                ),
            ),
        }

    def get_signals(self):
        self._ticks += 1

        results = {}

        symbols = self._get_dynamic_symbols()

        for symbol in symbols:
            data = self._build_tf_data(
                symbol
            )

            df_1H = data.get("1H")
            df_5M = data.get("5M")

            if (
                df_1H is None
                or df_1H.empty
            ):
                results[symbol] = {
                    "signal": "NO_DATA"
                }
                continue

            last_price = None

            try:
                last_price = self.feed.get_last_price(
                    symbol
                )
            except Exception:
                last_price = None

            if last_price is None:
                last_price = float(
                    df_1H["close"].iloc[-1]
                )
            else:
                last_price = float(
                    last_price
                )

            candle_high, candle_low = (
                self.trade_lifecycle.last_closed_hilo(
                    df_5M
                )
            )

            if self.trade_lifecycle.has_active_trade(
                symbol
            ):
                manage = self.trade_lifecycle.check_active_trade(
                    symbol,
                    last_price,
                    candle_high,
                    candle_low,
                )

                if manage.get(
                    "signal"
                ) in (
                    "EXIT_SL",
                    "EXIT_TP",
                ):
                    results[symbol] = manage

                    self.trade_lifecycle.drop_symbol(
                        symbol
                    )

                    continue

                results[symbol] = manage
                continue

            sig = self.strategy.generate_signal(
                data
            )

            sig = self.ai.on_signal(
                symbol,
                sig,
                data,
                self.trade_lifecycle.active_trades,
            )

            if sig.get("signal") == "ENTER":
                sig = self.trade_lifecycle.register_enter(
                    symbol,
                    sig,
                )

            results[symbol] = sig

        if (
            self._cache_metrics_every > 0
            and (
                self._ticks
                % self._cache_metrics_every
                == 0
            )
        ):
            s = self.ohlcv_cache.stats()

            self._log_signal_summary(
                results,
                symbols,
            )

            print(
                f"[CACHE] ticks={self._ticks} "
                f"hits={s['hits']} "
                f"misses={s['misses']} "
                f"stale_fallbacks={s['stale_fallbacks']} "
                f"entries={s['entries']}"
            )

        return results
