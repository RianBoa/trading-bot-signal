from __future__ import annotations

from typing import Dict, List


class AntiRevengeGuard:
    """
    Rules:
    - after ANY EXIT_SL on a symbol: block ENTER for 15 minutes
    - after 3 EXIT_SL in a row on a symbol: block ENTER for 60 minutes and reset streak
    - EXIT_TP resets streak
    """

    def __init__(self):
        self._sl_streak_by_sym: Dict[str, int] = {}
        self._cooldown_until: Dict[str, int] = {}

    def allow(self, state: str, symbol: str, aliases: List[str], now_ts: int) -> bool:
        state = (state or "").upper()
        symbol = (symbol or "").upper()

        if state == "EXIT_SL":
            streak = int(self._sl_streak_by_sym.get(symbol, 0) or 0) + 1
            self._sl_streak_by_sym[symbol] = streak

            for alias in aliases:
                self._cooldown_until[alias] = max(
                    int(self._cooldown_until.get(alias, 0) or 0),
                    now_ts + 15 * 60,
                )

            if streak >= 3:
                until = now_ts + 60 * 60
                for alias in aliases:
                    self._cooldown_until[alias] = max(
                        int(self._cooldown_until.get(alias, 0) or 0),
                        until,
                    )
                self._sl_streak_by_sym[symbol] = 0

                max_until = max(
                    int(self._cooldown_until.get(alias, 0) or 0)
                    for alias in aliases
                )
                print(
                    f"[BOT] anti-revenge(v3): 3x EXIT_SL on {symbol} -> "
                    f"block ENTER until={max_until} aliases={aliases}"
                )

            return True

        if state == "EXIT_TP":
            self._sl_streak_by_sym[symbol] = 0
            return True

        if state == "ENTER":
            cooldown_until = 0
            for alias in aliases:
                cooldown_until = max(
                    cooldown_until,
                    int(self._cooldown_until.get(alias, 0) or 0),
                )

            if cooldown_until and now_ts < cooldown_until:
                print(
                    f"[BOT] anti-revenge(v3): ENTER blocked sym={symbol} "
                    f"now={now_ts} until={cooldown_until} aliases={aliases}"
                )
                return False

        return True