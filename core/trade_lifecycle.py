from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.strategy_narrative import ActiveTrade
from core.trade_state_store import TradeStateStore


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


class TradeLifecycleManager:
    """
    Owns active trade state and TP/SL lifecycle logic.
    """

    def __init__(
        self,
        state_store: Optional[TradeStateStore] = None,
    ):
        self.state_store = state_store
        self.active_trades: dict[str, ActiveTrade] = {}
        self._tp_state: dict[str, Dict[str, Any]] = {}
        self._load_state()

    def active_symbols(self) -> List[str]:
        return list(self.active_trades.keys())

    def has_active_trade(self, symbol: str) -> bool:
        return symbol in self.active_trades

    def get_active_trade(self, symbol: str) -> ActiveTrade:
        return self.active_trades[symbol]

    def drop_symbol(self, symbol: str) -> None:
        removed_trade = self.active_trades.pop(symbol, None)
        removed_tp = self._tp_state.pop(symbol, None)
        if removed_trade is not None or removed_tp is not None:
            self._persist_state()

    def register_enter(self, symbol: str, sig: dict) -> dict:
        tps = self._extract_tp_prices(sig)
        side = (sig.get("side") or "").upper()
        self._init_tp_state(symbol, side, tps)

        tp_main = float(tps[-1]) if tps else float(sig["tp_price"])
        trade = ActiveTrade(
            side=sig["side"],
            entry=sig["entry_price"],
            stop=sig["stop_price"],
            tp=tp_main,
            tf=sig["tf"],
            narrative=sig.get("narrative", ""),
            symbol=symbol,
        )
        self.active_trades[symbol] = trade

        if tps:
            sig["tp_prices"] = tps
            sig["tp_price"] = tp_main
        self._persist_state()
        return sig

    @staticmethod
    def last_closed_hilo(df_5m) -> Tuple[Optional[float], Optional[float]]:
        try:
            if df_5m is None or df_5m.empty or len(df_5m) < 1:
                return None, None
            last = df_5m.iloc[-1]
            hi = float(last["high"])
            lo = float(last["low"])
            return hi, lo
        except Exception:
            return None, None

    def check_active_trade(
        self,
        symbol: str,
        last_price: float,
        candle_high: Optional[float],
        candle_low: Optional[float],
    ) -> dict:
        trade = self.active_trades[symbol]
        side = (trade.side or "").upper()

        st = self._tp_state.get(symbol) or {}
        tps = st.get("tps") or [float(trade.tp)]
        hit = int(st.get("hit") or 0)
        pending = list(st.get("pending") or [])
        closed = bool(st.get("closed") or False)
        total = len(tps)

        hi = float(candle_high) if candle_high is not None else float(last_price)
        lo = float(candle_low) if candle_low is not None else float(last_price)

        if side == "LONG":
            if lo <= float(trade.stop):
                self._tp_state.pop(symbol, None)
                self._persist_state()
                return {
                    "signal": "EXIT_SL",
                    "side": trade.side,
                    "exit_price": float(trade.stop),
                    "stop_price": float(trade.stop),
                    "tf": trade.tf,
                    "trigger_high": hi,
                    "trigger_low": lo,
                    "validation_reason": "sl_reached",
                }
        else:
            if hi >= float(trade.stop):
                self._tp_state.pop(symbol, None)
                self._persist_state()
                return {
                    "signal": "EXIT_SL",
                    "side": trade.side,
                    "exit_price": float(trade.stop),
                    "stop_price": float(trade.stop),
                    "tf": trade.tf,
                    "trigger_high": hi,
                    "trigger_low": lo,
                    "validation_reason": "sl_reached",
                }

        if pending:
            nxt = pending.pop(0)
            self._tp_state[symbol] = {"tps": tps, "hit": hit, "pending": pending, "closed": closed}
            self._persist_state()
            return {
                "signal": "TP_HIT",
                "side": trade.side,
                "exit_price": float(last_price),
                "tf": trade.tf,
                "tp_index": int(nxt["tp_index"]),
                "tp_total": int(nxt["tp_total"]),
                "tp_price": float(nxt["tp_price"]),
                "trigger_high": hi,
                "trigger_low": lo,
                "validation_reason": "tp_reached",
            }

        if not closed and total > 0:
            new_hit = hit

            if side == "LONG":
                while new_hit < total and hi >= float(tps[new_hit]):
                    new_hit += 1
            else:
                while new_hit < total and lo <= float(tps[new_hit]):
                    new_hit += 1

            if new_hit > hit:
                for i in range(hit + 1, new_hit + 1):
                    if i <= total:
                        pending.append({"tp_index": i, "tp_total": total, "tp_price": float(tps[i - 1])})
                hit = new_hit
                if hit >= total:
                    closed = True

            self._tp_state[symbol] = {"tps": tps, "hit": hit, "pending": pending, "closed": closed}

            if pending:
                nxt = pending.pop(0)
                self._tp_state[symbol] = {"tps": tps, "hit": hit, "pending": pending, "closed": closed}
                self._persist_state()
                return {
                    "signal": "TP_HIT",
                    "side": trade.side,
                    "exit_price": float(last_price),
                    "tf": trade.tf,
                    "tp_index": int(nxt["tp_index"]),
                    "tp_total": int(nxt["tp_total"]),
                    "tp_price": float(nxt["tp_price"]),
                    "trigger_high": hi,
                    "trigger_low": lo,
                    "validation_reason": "tp_reached",
                }

        if closed:
            tp_last = float(tps[-1]) if tps else float(trade.tp)
            self._tp_state.pop(symbol, None)
            self._persist_state()
            return {
                "signal": "EXIT_TP",
                "side": trade.side,
                "exit_price": float(tp_last),
                "tf": trade.tf,
                "tp_index": int(total),
                "tp_total": int(total),
                "tp_price": float(tp_last),
                "trigger_high": hi,
                "trigger_low": lo,
                "validation_reason": "tp_reached",
            }

        return {
            "signal": "HOLD",
            "side": trade.side,
            "entry_price": trade.entry,
            "stop_price": trade.stop,
            "tp_price": trade.tp,
            "tf": trade.tf,
            "narrative": trade.narrative,
        }

    def _extract_tp_prices(self, sig: dict) -> List[float]:
        tps: List[float] = []
        tp_prices = sig.get("tp_prices")
        if isinstance(tp_prices, (list, tuple)):
            for v in tp_prices:
                fv = _to_float(v)
                if fv is not None:
                    tps.append(float(fv))

        if not tps:
            fv = _to_float(sig.get("tp_price"))
            if fv is not None:
                tps = [float(fv)]

        out: List[float] = []
        seen = set()
        for v in tps:
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
        return out

    def _init_tp_state(self, symbol: str, side: str, tps: List[float]) -> None:
        if not tps:
            return
        if side == "LONG":
            tps2 = sorted(tps)
        else:
            tps2 = sorted(tps, reverse=True)
        self._tp_state[symbol] = {"tps": tps2, "hit": 0, "pending": [], "closed": False}

    def _load_state(self) -> None:
        if self.state_store is None:
            return

        data = self.state_store.load()
        raw_trades = data.get("active_trades") or {}
        raw_tp_state = data.get("tp_state") or {}

        if isinstance(raw_trades, dict):
            for symbol, raw in raw_trades.items():
                if not isinstance(symbol, str) or not isinstance(raw, dict):
                    continue
                try:
                    self.active_trades[symbol] = ActiveTrade(
                        side=str(raw.get("side") or "").upper(),
                        entry=float(raw["entry"]),
                        stop=float(raw["stop"]),
                        tp=float(raw["tp"]),
                        tf=str(raw.get("tf") or ""),
                        narrative=str(raw.get("narrative") or ""),
                        symbol=str(raw.get("symbol") or symbol),
                    )
                except Exception:
                    continue

        if isinstance(raw_tp_state, dict):
            for symbol, raw in raw_tp_state.items():
                if not isinstance(symbol, str) or not isinstance(raw, dict):
                    continue
                try:
                    tps = [
                        float(v)
                        for v in (raw.get("tps") or [])
                    ]
                    pending = []
                    for item in raw.get("pending") or []:
                        if not isinstance(item, dict):
                            continue
                        pending.append(
                            {
                                "tp_index": int(item["tp_index"]),
                                "tp_total": int(item["tp_total"]),
                                "tp_price": float(item["tp_price"]),
                            }
                        )
                    self._tp_state[symbol] = {
                        "tps": tps,
                        "hit": int(raw.get("hit") or 0),
                        "pending": pending,
                        "closed": bool(raw.get("closed") or False),
                    }
                except Exception:
                    continue

    def _persist_state(self) -> None:
        if self.state_store is None:
            return

        active_trades = {
            symbol: {
                "side": trade.side,
                "entry": float(trade.entry),
                "stop": float(trade.stop),
                "tp": float(trade.tp),
                "tf": trade.tf,
                "narrative": trade.narrative,
                "symbol": trade.symbol,
            }
            for symbol, trade in self.active_trades.items()
        }
        tp_state = {}
        for symbol, raw in self._tp_state.items():
            tp_state[symbol] = {
                "tps": [
                    float(v)
                    for v in (raw.get("tps") or [])
                ],
                "hit": int(raw.get("hit") or 0),
                "pending": [
                    {
                        "tp_index": int(item["tp_index"]),
                        "tp_total": int(item["tp_total"]),
                        "tp_price": float(item["tp_price"]),
                    }
                    for item in (raw.get("pending") or [])
                    if isinstance(item, dict)
                    and "tp_index" in item
                    and "tp_total" in item
                    and "tp_price" in item
                ],
                "closed": bool(raw.get("closed") or False),
            }
        self.state_store.save(
            active_trades=active_trades,
            tp_state=tp_state,
        )
