from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SignalDecision:
    emit: bool
    reason: str
    state: str


@dataclass
class SymbolSignalState:
    state: str = "WAIT"
    last_fingerprint: str = ""
    last_signal: str = ""
    max_tp_index: int = 0
    cooldown_until: int = 0
    emitted_fingerprints: Dict[str, int] = field(default_factory=dict)


class SignalStateMachine:
    def __init__(self, *, cooldown_after_sl_sec: int = 15 * 60):
        self.cooldown_after_sl_sec = int(cooldown_after_sl_sec)
        self._by_symbol: Dict[str, SymbolSignalState] = {}

    def get(self, symbol: str) -> SymbolSignalState:
        sym = self._norm_symbol(symbol)
        if sym not in self._by_symbol:
            self._by_symbol[sym] = SymbolSignalState()
        return self._by_symbol[sym]

    def decide(self, symbol: str, sig: dict, now_ts: int) -> SignalDecision:
        sym = self._norm_symbol(symbol)
        state = self.get(sym)
        signal = str(sig.get("signal") or "").upper()
        fingerprint = self.fingerprint(sym, sig)

        if not signal:
            return SignalDecision(False, "missing_signal", state.state)

        if fingerprint in state.emitted_fingerprints:
            return SignalDecision(False, f"duplicate_{signal.lower()}", state.state)

        if signal == "ENTER":
            if state.cooldown_until and now_ts < state.cooldown_until:
                state.state = "COOLDOWN"
                return SignalDecision(False, "blocked_cooldown", state.state)

            if state.state in ("ENTER_SENT", "IN_TRADE", "TP_HIT"):
                return SignalDecision(False, "already_open", state.state)

            self._mark_emitted(state, signal, fingerprint, now_ts)
            state.state = "ENTER_SENT"
            state.max_tp_index = 0
            return SignalDecision(True, "new_enter", state.state)

        if signal == "TP_HIT":
            if state.state in ("WAIT", "COOLDOWN", "EXIT_SENT"):
                return SignalDecision(False, "tp_without_open_trade", state.state)

            tp_index = self._tp_index(sig)
            if tp_index and tp_index <= state.max_tp_index:
                return SignalDecision(False, "duplicate_or_old_tp", state.state)

            self._mark_emitted(state, signal, fingerprint, now_ts)
            state.state = "TP_HIT"
            if tp_index:
                state.max_tp_index = tp_index
            return SignalDecision(True, "new_tp", state.state)

        if signal in ("EXIT_SL", "EXIT_TP"):
            if state.state in ("WAIT", "COOLDOWN"):
                return SignalDecision(False, "exit_without_open_trade", state.state)

            if state.state == "EXIT_SENT":
                return SignalDecision(False, "already_closed", state.state)

            self._mark_emitted(state, signal, fingerprint, now_ts)
            state.state = "EXIT_SENT"
            if signal == "EXIT_SL":
                state.cooldown_until = max(
                    int(state.cooldown_until or 0),
                    int(now_ts) + self.cooldown_after_sl_sec,
                )
            return SignalDecision(True, f"new_{signal.lower()}", state.state)

        self._mark_emitted(state, signal, fingerprint, now_ts)
        state.state = signal
        return SignalDecision(True, "new_signal", state.state)

    def mark_delivery_failed(self, symbol: str, sig: dict) -> None:
        sym = self._norm_symbol(symbol)
        state = self.get(sym)
        signal = str(sig.get("signal") or "").upper()
        fingerprint = self.fingerprint(sym, sig)

        if state.last_fingerprint != fingerprint:
            return

        state.emitted_fingerprints.pop(fingerprint, None)

        if signal == "ENTER" and state.state == "ENTER_SENT":
            state.state = "WAIT"
            state.last_signal = ""
            state.last_fingerprint = ""
            state.max_tp_index = 0

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {
            sym: {
                "state": st.state,
                "last_signal": st.last_signal,
                "last_fingerprint": st.last_fingerprint,
                "max_tp_index": st.max_tp_index,
                "cooldown_until": st.cooldown_until,
                "emitted_count": len(st.emitted_fingerprints),
            }
            for sym, st in self._by_symbol.items()
        }

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {
            sym: {
                "state": st.state,
                "last_fingerprint": st.last_fingerprint,
                "last_signal": st.last_signal,
                "max_tp_index": int(st.max_tp_index or 0),
                "cooldown_until": int(st.cooldown_until or 0),
                "emitted_fingerprints": {
                    str(fp): int(ts)
                    for fp, ts in st.emitted_fingerprints.items()
                    if str(fp)
                },
            }
            for sym, st in self._by_symbol.items()
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return

        loaded: Dict[str, SymbolSignalState] = {}
        for raw_sym, raw_state in data.items():
            sym = self._norm_symbol(str(raw_sym))
            if not sym or not isinstance(raw_state, dict):
                continue

            emitted: Dict[str, int] = {}
            raw_emitted = raw_state.get("emitted_fingerprints") or {}
            if isinstance(raw_emitted, dict):
                for fp, ts in raw_emitted.items():
                    if not str(fp):
                        continue
                    try:
                        emitted[str(fp)] = int(float(ts))
                    except Exception:
                        continue

            try:
                max_tp_index = int(raw_state.get("max_tp_index") or 0)
            except Exception:
                max_tp_index = 0

            try:
                cooldown_until = int(raw_state.get("cooldown_until") or 0)
            except Exception:
                cooldown_until = 0

            loaded[sym] = SymbolSignalState(
                state=str(raw_state.get("state") or "WAIT").upper(),
                last_fingerprint=str(raw_state.get("last_fingerprint") or ""),
                last_signal=str(raw_state.get("last_signal") or "").upper(),
                max_tp_index=max_tp_index,
                cooldown_until=cooldown_until,
                emitted_fingerprints=emitted,
            )

        self._by_symbol.update(loaded)

    def fingerprint(self, symbol: str, sig: dict) -> str:
        signal = str(sig.get("signal") or "UNKNOWN").upper()

        if signal == "ENTER":
            return "|".join(
                [
                    symbol,
                    signal,
                    str(sig.get("side") or ""),
                    str(sig.get("tf") or sig.get("timeframe") or ""),
                    str(sig.get("entry_price") or sig.get("entry") or ""),
                    str(sig.get("stop_price") or sig.get("sl") or ""),
                    str(sig.get("tp_price") or sig.get("tp") or sig.get("tp1") or ""),
                ]
            )

        if signal == "TP_HIT":
            return "|".join(
                [
                    symbol,
                    signal,
                    str(sig.get("side") or ""),
                    str(sig.get("tp_index") or ""),
                    str(sig.get("tp_price") or ""),
                    str(sig.get("exit_price") or ""),
                ]
            )

        if signal in ("EXIT_SL", "EXIT_TP"):
            return "|".join(
                [
                    symbol,
                    signal,
                    str(sig.get("side") or ""),
                    str(sig.get("exit_price") or ""),
                ]
            )

        return f"{symbol}|{signal}"

    def _mark_emitted(
        self,
        state: SymbolSignalState,
        signal: str,
        fingerprint: str,
        now_ts: int,
    ) -> None:
        state.last_signal = signal
        state.last_fingerprint = fingerprint
        state.emitted_fingerprints[fingerprint] = int(now_ts)

    def _tp_index(self, sig: dict) -> Optional[int]:
        raw = sig.get("tp_index")
        try:
            return int(raw)
        except Exception:
            return None

    def _norm_symbol(self, symbol: str) -> str:
        return str(symbol or "").upper().strip().split(":")[-1]
