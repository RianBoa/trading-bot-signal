# core/pivot_trigger.py
from __future__ import annotations

from typing import Optional, Dict, Any
import numpy as np
import pandas as pd


def mark_pivots(df: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """
    Каузальные pivot'ы (без заглядывания в будущее).

    pivot:
      1 = Swing High (confirmed)
      2 = Swing Low  (confirmed)

    Подтверждение pivot'а в точке center происходит только когда прошло window баров справа.
    На текущем баре i подтверждаем pivot для center = i - window, используя окно [i-2*window .. i].
    """
    df = df.copy()
    n = len(df)
    piv = np.zeros(n, dtype=int)

    if n < 2 * window + 1:
        df["pivot"] = piv
        return df

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    span = 2 * window + 1

    for i in range(span - 1, n):
        left = i - (span - 1)
        right = i
        center = i - window

        window_highs = highs[left:right + 1]
        window_lows = lows[left:right + 1]

        ch = highs[center]
        cl = lows[center]

        # Swing High
        if ch >= window_highs.max():
            piv[center] = 1

        # Swing Low (если уже 1, оставим 1)
        if cl <= window_lows.min() and piv[center] == 0:
            piv[center] = 2

    df["pivot"] = piv
    return df


def m15_pivot_trigger(
    df: pd.DataFrame,
    lookback: int = 80,
    mode: str = "close_reclaim",
    wick_confirm: bool = True,
    min_wick_pct: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Pivot trigger с фокусом на ЛОЖНЫЙ ПРОБОЙ / REJECTION последнего подтверждённого pivot.

    Возвращает dict:
      {
        "side": "LONG"|"SHORT",
        "kind": "FAKEOUT"|"REJECT"|"TRAP_2C",
        "level": float,
        "pivot_kind": "PIVOT_HIGH"|"PIVOT_LOW",
        "pivot_ts": <index value>,
        "reclaim_close": float,
        "overshoot_pct": float,
      }

    mode:
      - "close_reclaim" (рекомендуется): 1-свечный sweep+reclaim (rejection)
          SHORT: last.high > lvl AND last.close < lvl
          LONG : last.low  < lvl AND last.close > lvl
        kind будет: "REJECT" (по смыслу) / часто это и есть fakeout.
      - "two_close": 2-свечный bull/bear trap
          SHORT: prev.close > lvl AND last.close < lvl
          LONG : prev.close < lvl AND last.close > lvl
        kind будет: "TRAP_2C"

    wick_confirm:
      True -> требовать прокол уровнем хвостом (high/low) на last
    min_wick_pct:
      минимальный прокол в % от уровня (например 0.10 = 0.10%). 0.0 = без фильтра.
    """
    if df is None or df.empty or "pivot" not in df.columns or len(df) < 10:
        return None

    w = df.tail(lookback).copy()
    if len(w) < 2:
        return None

    need_cols = {"high", "low", "close"}
    if not need_cols.issubset(set(w.columns)):
        return None

    piv_highs = w[w["pivot"] == 1]
    piv_lows = w[w["pivot"] == 2]

    last = w.iloc[-1]
    prev = w.iloc[-2]

    def _overshoot_pct(up: bool, level: float) -> float:
        level = float(level)
        if level <= 0:
            return 0.0
        if up:
            overshoot = float(last["high"]) - level
        else:
            overshoot = level - float(last["low"])
        return max(0.0, (overshoot / level) * 100.0)

    def _wick_ok(up: bool, level: float) -> bool:
        if not wick_confirm:
            return True
        level = float(level)
        if level <= 0:
            return False

        if up:
            if float(last["high"]) <= level:
                return False
        else:
            if float(last["low"]) >= level:
                return False

        if min_wick_pct <= 0:
            return True

        return _overshoot_pct(up, level) >= float(min_wick_pct)

    # ======= SHORT: sweep/fakeout выше pivot-high (bull trap / rejection) =======
    if not piv_highs.empty:
        lvl_h = float(piv_highs["high"].iloc[-1])
        piv_ts = piv_highs.index[-1]

        if mode == "two_close":
            if float(prev["close"]) > lvl_h and float(last["close"]) < lvl_h and _wick_ok(True, lvl_h):
                return {
                    "side": "SHORT",
                    "kind": "TRAP_2C",
                    "level": lvl_h,
                    "pivot_kind": "PIVOT_HIGH",
                    "pivot_ts": piv_ts,
                    "reclaim_close": float(last["close"]),
                    "overshoot_pct": _overshoot_pct(True, lvl_h),
                }
        else:
            # close_reclaim: прокол вверх + закрытие обратно ниже
            if _wick_ok(True, lvl_h) and float(last["close"]) < lvl_h:
                return {
                    "side": "SHORT",
                    "kind": "REJECT",
                    "level": lvl_h,
                    "pivot_kind": "PIVOT_HIGH",
                    "pivot_ts": piv_ts,
                    "reclaim_close": float(last["close"]),
                    "overshoot_pct": _overshoot_pct(True, lvl_h),
                }

    # ======= LONG: sweep/fakeout ниже pivot-low (bear trap / rejection) =======
    if not piv_lows.empty:
        lvl_l = float(piv_lows["low"].iloc[-1])
        piv_ts = piv_lows.index[-1]

        if mode == "two_close":
            if float(prev["close"]) < lvl_l and float(last["close"]) > lvl_l and _wick_ok(False, lvl_l):
                return {
                    "side": "LONG",
                    "kind": "TRAP_2C",
                    "level": lvl_l,
                    "pivot_kind": "PIVOT_LOW",
                    "pivot_ts": piv_ts,
                    "reclaim_close": float(last["close"]),
                    "overshoot_pct": _overshoot_pct(False, lvl_l),
                }
        else:
            if _wick_ok(False, lvl_l) and float(last["close"]) > lvl_l:
                return {
                    "side": "LONG",
                    "kind": "REJECT",
                    "level": lvl_l,
                    "pivot_kind": "PIVOT_LOW",
                    "pivot_ts": piv_ts,
                    "reclaim_close": float(last["close"]),
                    "overshoot_pct": _overshoot_pct(False, lvl_l),
                }

    return None
