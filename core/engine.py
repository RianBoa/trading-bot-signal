from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _norm_event_symbol(value: str) -> str:
    value = (value or "").upper().strip()
    value = value.split(":")[-1]
    value = re.sub(r"[^A-Z0-9]+", "", value)
    return value


class TradingEngine:
    """
    Runs the trading cycle and exposes:
      - last snapshot: full per-symbol signals dict
      - queued events: only newly produced ENTER/TP_HIT/EXIT_SL/EXIT_TP signals
    """

    def __init__(
        self,
        core: Any,
        *,
        emit_signals: Optional[Iterable[str]] = None,
        max_queue: int = 10000,
    ):
        self.core = core
        self.emit_signals: Set[str] = {
            s.upper() for s in (emit_signals or ("ENTER", "TP_HIT", "EXIT_SL", "EXIT_TP"))
        }
        self.max_queue = int(max_queue)

        self._snapshot: Dict[str, dict] = {}
        self._symbols: List[str] = []

        self._tick_lock = asyncio.Lock()
        self._queue_lock = asyncio.Lock()
        self._queue: deque[Tuple[str, dict]] = deque()
        self._last_tick_finished = False

        self._last_signal_state: Dict[str, str] = {}
        self._last_event_fingerprint: Dict[str, str] = {}

    def get_snapshot(self) -> Dict[str, dict]:
        return dict(self._snapshot)

    def get_symbols(self) -> List[str]:
        return list(self._symbols)

    def _fingerprint(self, symbol: str, sig: dict) -> str:
        st = str(sig.get("signal") or "").upper()

        if st == "ENTER":
            return "|".join(
                [
                    str(symbol),
                    st,
                    str(sig.get("side") or ""),
                    str(sig.get("tf") or sig.get("timeframe") or ""),
                    str(sig.get("entry") or sig.get("entry_price") or ""),
                    str(sig.get("sl") or sig.get("stop_price") or ""),
                    str(sig.get("tp1") or sig.get("tp") or sig.get("tp_price") or ""),
                ]
            )

        if st == "TP_HIT":
            return "|".join(
                [
                    str(symbol),
                    st,
                    str(sig.get("tp_index") or ""),
                    str(sig.get("tp_price") or ""),
                    str(sig.get("exit_price") or ""),
                ]
            )

        if st in ("EXIT_SL", "EXIT_TP"):
            return "|".join(
                [
                    str(symbol),
                    st,
                    str(sig.get("exit_price") or ""),
                ]
            )

        return f"{symbol}|{st}"

    async def tick(self) -> None:
        """
        Runs one full trading cycle and enqueues only new event signals.
        Heavy work is executed in a thread to avoid blocking Telegram's event loop.
        """
        async with self._tick_lock:
            signals = await asyncio.to_thread(self.core.get_signals)
            if not isinstance(signals, dict):
                signals = {}

            self._snapshot = signals
            self._symbols = list(signals.keys())

            async with self._queue_lock:
                for symbol, sig in signals.items():
                    if not isinstance(sig, dict):
                        continue

                    symbol = _norm_event_symbol(str(symbol)) or str(symbol)
                    state = str(sig.get("signal") or "").upper()
                    prev_state = self._last_signal_state.get(symbol, "")

                    # always refresh last seen state
                    self._last_signal_state[symbol] = state

                    if state not in self.emit_signals:
                        continue

                    fp = self._fingerprint(symbol, sig)
                    prev_fp = self._last_event_fingerprint.get(symbol)

                    should_enqueue = False

                    # enqueue when event state changed
                    if state != prev_state:
                        should_enqueue = True
                    # or same state but different event payload (e.g. TP_HIT tp_index changed)
                    elif fp != prev_fp:
                        should_enqueue = True

                    if not should_enqueue:
                        continue

                    self._last_event_fingerprint[symbol] = fp

                    if len(self._queue) >= self.max_queue:
                        self._queue.popleft()

                    self._queue.append((symbol, sig))

            self._last_tick_finished = True

    async def drain_events(self) -> List[Tuple[str, dict]]:
        out: List[Tuple[str, dict]] = []
        async with self._queue_lock:
            while self._queue:
                out.append(self._queue.popleft())
        return out

    async def ensure_warm(self) -> None:
        if self._snapshot or self._last_tick_finished:
            return
        await self.tick()
