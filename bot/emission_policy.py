from __future__ import annotations

import json
import time
from typing import Any, Dict, Tuple


class EmissionPolicy:
    def __init__(self, enter_cooldown_sec: int, exit_cooldown_sec: int):
        self.enter_cooldown_sec = int(enter_cooldown_sec)
        self.exit_cooldown_sec = int(exit_cooldown_sec)
        self._last_emit_by_symbol: Dict[str, Tuple[str, float]] = {}

    def fingerprint(self, symbol: str, sig: dict) -> str:
        st = (sig.get("signal") or "UNKNOWN").upper()

        def _j(x: Any) -> str:
            try:
                return json.dumps(x, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(x)

        if st == "ENTER":
            return "|".join(
                [
                    str(symbol),
                    "ENTER",
                    str(sig.get("side")),
                    str(sig.get("tf") or sig.get("timeframe") or ""),
                    str(sig.get("entry_price")),
                    str(sig.get("stop_price")),
                    _j(sig.get("tp_prices") or sig.get("tp_price") or ""),
                ]
            )

        if st == "TP_HIT":
            return "|".join(
                [
                    str(symbol),
                    "TP_HIT",
                    str(sig.get("side")),
                    str(sig.get("tp_index") or ""),
                    str(sig.get("tp_price") or ""),
                    str(sig.get("exit_price") or ""),
                    _j(sig.get("tp_hits") or ""),
                ]
            )

        if st in ("EXIT_SL", "EXIT_TP"):
            return "|".join(
                [
                    str(symbol),
                    st,
                    str(sig.get("side")),
                    str(sig.get("exit_price")),
                ]
            )

        return f"{symbol}|{st}"

    def should_emit(self, symbol: str, sig: dict) -> bool:
        fp = self.fingerprint(symbol, sig)
        now = time.time()
        st = (sig.get("signal") or "").upper()
        cooldown = self.enter_cooldown_sec if st == "ENTER" else self.exit_cooldown_sec

        last = self._last_emit_by_symbol.get(symbol)
        if last:
            last_fp, last_ts = last
            if fp == last_fp and (now - last_ts) < cooldown:
                return False

        self._last_emit_by_symbol[symbol] = (fp, now)
        return True