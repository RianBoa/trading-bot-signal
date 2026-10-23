# core/strategy_narrative.py
from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

from core.pivot_trigger import mark_pivots, m15_pivot_trigger
from core.fractals import detect_williams_fractals

Side = Literal["LONG", "SHORT", "NEUTRAL"]


@dataclass
class CandidateEntry:
    side: Side
    entry_price: float
    tf: str
    reason: str


@dataclass
class ActiveTrade:
    side: Side
    entry: float
    stop: float
    tp: float
    tf: str
    narrative: str
    symbol: str


class NarrativeStrategy:
    """
    Top-Down:
      1) Bias: 4H + 1H
      2) Setup: 15M
      3) VC(1H): "Absorption sweep" (Turtle Soup-like). In relaxed mode VC is a filter, not a hard gate.
      4) Entry: 5M (optional fallback: enter on 15M setup if 5M doesn't trigger)
      5) TP: up to 4 targets by Williams fractals on 1H

    Returns dict compatible with your core.get_signals() aggregator.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ):
        cfg = config or {}
        volume_cfg = cfg.get(
            "volumeConfirmation",
            {},
        )
        entry_cfg = cfg.get(
            "entryTriggers",
            {},
        )
        stop_cfg = cfg.get(
            "stopLoss",
            {},
        )
        take_profit_cfg = cfg.get(
            "takeProfit",
            {},
        )

        self.risk_per_trade = float(
            cfg.get("riskPerTrade", 0.01)
        )
        self.rr_min = float(
            cfg.get("rrMin", 1.50)
        )

        self.vc_mode = str(
            volume_cfg.get("mode", "relaxed")
            or "relaxed"
        ).strip().lower()
        if self.vc_mode not in (
            "strict",
            "relaxed",
        ):
            self.vc_mode = "relaxed"

        # overshoot must be >= this * ATR(14)
        self.vc_abs_lookback = int(
            volume_cfg.get("absLookback", 20)
        )
        self.vc_abs_min_atr = float(
            volume_cfg.get("absMinAtr", 0.08)
        )
        self.vc_abs_require_close_back = bool(
            volume_cfg.get(
                "absRequireCloseBack",
                True,
            )
        )

        self.pivot_window_15m = int(
            entry_cfg.get("pivotWindow15m", 3)
        )
        self.pivot_window_5m = int(
            entry_cfg.get("pivotWindow5m", 4)
        )
        self.momentum_body_k_15m = float(
            entry_cfg.get(
                "momentumBodyK15m",
                0.30,
            )
        )
        self.momentum_body_k_5m = float(
            entry_cfg.get(
                "momentumBodyK5m",
                0.30,
            )
        )

        self.min_wick_15m = float(
            entry_cfg.get("minWick15m", 0.05)
        )
        self.min_wick_5m = float(
            entry_cfg.get("minWick5m", 0.10)
        )

        self.allow_15m_entry_fallback = bool(
            entry_cfg.get(
                "allow15mEntryFallback",
                True,
            )
        )
        self.near_pivot_enabled = bool(
            entry_cfg.get(
                "nearPivotEnabled",
                True,
            )
        )
        self.near_pivot_atr_mult = float(
            entry_cfg.get(
                "nearPivotAtrMult",
                0.50,
            )
        )

        self.stop_mode = str(
            stop_cfg.get("mode", "atr")
            or "atr"
        ).strip().lower()
        if self.stop_mode not in (
            "atr",
            "williams_second",
        ):
            self.stop_mode = "atr"
        self.atr_stop_mult = float(
            stop_cfg.get("atrStopMult", 1.00)
        )
        self.min_stop_pct = float(
            stop_cfg.get("minStopPct", 0.005)
        )

        self.max_tp_levels = int(
            take_profit_cfg.get(
                "williamsTpMax",
                4,
            )
        )
        self.max_tp_levels = max(1, min(4, self.max_tp_levels))

        self.min_tp_gap_atr = float(
            take_profit_cfg.get(
                "minTpGapAtr",
                0.25,
            )
        )

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = np.maximum(high - low, np.maximum((high - close_prev).abs(), (low - close_prev).abs()))
        v = tr.rolling(period).mean().iloc[-1]
        return float(v) if pd.notna(v) else float(tr.mean())

    @staticmethod
    def _sma(series: pd.Series, n: int) -> float:
        v = series.tail(n).mean()
        return float(v) if pd.notna(v) else float(series.mean())

    @staticmethod
    def detect_fractals_11(df: pd.DataFrame) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
        highs: List[Dict[str, float]] = []
        lows: List[Dict[str, float]] = []
        if df is None or df.empty or len(df) < 3:
            return highs, lows

        for i in range(1, len(df) - 1):
            prev = df.iloc[i - 1]
            cur = df.iloc[i]
            nxt = df.iloc[i + 1]

            if float(cur["high"]) > float(prev["high"]) and float(cur["high"]) > float(nxt["high"]):
                highs.append({"index": i, "price": float(cur["high"])})
            if float(cur["low"]) < float(prev["low"]) and float(cur["low"]) < float(nxt["low"]):
                lows.append({"index": i, "price": float(cur["low"])})

        return highs, lows

    @staticmethod
    def get_dr_from_fractals(df: pd.DataFrame) -> Optional[Dict[str, float]]:
        highs, lows = NarrativeStrategy.detect_fractals_11(df)
        if not highs or not lows:
            return None

        last_close = float(df["close"].iloc[-1])
        highs_above = [h for h in highs if h["price"] > last_close]
        lows_below = [l for l in lows if l["price"] < last_close]
        if not highs_above or not lows_below:
            return None

        dr_high = min(highs_above, key=lambda x: x["price"])["price"]
        dr_low = max(lows_below, key=lambda x: x["price"])["price"]
        return {"high": float(dr_high), "low": float(dr_low), "mid": float((dr_high + dr_low) / 2.0)}

    def calc_narrative(self, df_4H: pd.DataFrame, df_1H: pd.DataFrame) -> Tuple[Side, str, int, int]:
        if df_4H is None or df_1H is None or df_4H.empty or df_1H.empty:
            return "NEUTRAL", "No data for Narrative (4H/1H)", 0, 0
        if len(df_4H) < 80 or len(df_1H) < 80:
            return "NEUTRAL", "Not enough data for Narrative (4H/1H)", 0, 0

        dr4 = self.get_dr_from_fractals(df_4H)
        if dr4 is None:
            return "NEUTRAL", "No valid DR on 4H", 0, 0

        price_4h = float(df_4H["close"].iloc[-1])
        pos_4h = "DISCOUNT" if price_4h < dr4["mid"] else "PREMIUM"

        sma4 = self._sma(df_4H["close"], 50)
        sma1 = self._sma(df_1H["close"], 30)

        p4 = float(df_4H["close"].iloc[-1])
        p1 = float(df_1H["close"].iloc[-1])
        trend4: Side = "LONG" if p4 > sma4 else "SHORT"
        trend1: Side = "LONG" if p1 > sma1 else "SHORT"

        score_long = 0
        score_short = 0

        score_long += 1 if pos_4h == "DISCOUNT" else 0
        score_short += 1 if pos_4h == "PREMIUM" else 0
        score_long += 1 if trend4 == "LONG" else 0
        score_short += 1 if trend4 == "SHORT" else 0
        score_long += 1 if trend1 == "LONG" else 0
        score_short += 1 if trend1 == "SHORT" else 0

        if score_long >= 2 and score_long > score_short:
            return (
                "LONG",
                f"Bias LONG (4H->1H): 4H_DR={pos_4h}, 4H_trend={trend4}, 1H_trend={trend1} (votes L/S={score_long}/{score_short})",
                score_long,
                score_short,
            )

        if score_short >= 2 and score_short > score_long:
            return (
                "SHORT",
                f"Bias SHORT (4H->1H): 4H_DR={pos_4h}, 4H_trend={trend4}, 1H_trend={trend1} (votes L/S={score_long}/{score_short})",
                score_long,
                score_short,
            )

        return (
            "NEUTRAL",
            f"Mixed context (4H->1H): 4H_DR={pos_4h}, 4H_trend={trend4}, 1H_trend={trend1} (votes L/S={score_long}/{score_short})",
            score_long,
            score_short,
        )

    def detect_absorption_sweep(self, df_1H: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if df_1H is None or df_1H.empty or len(df_1H) < max(30, self.vc_abs_lookback + 2):
            return None

        w = df_1H.tail(self.vc_abs_lookback + 1)
        prev = w.iloc[:-1]
        last = w.iloc[-1]

        prev_high = float(prev["high"].max())
        prev_low = float(prev["low"].min())

        atr = self._atr(df_1H, 14)
        min_overshoot = max(0.0, self.vc_abs_min_atr * atr)

        last_high = float(last["high"])
        last_low = float(last["low"])
        last_close = float(last["close"])

        bear_overshoot = last_high - prev_high
        bull_overshoot = prev_low - last_low

        if bear_overshoot >= min_overshoot and last_high > prev_high:
            if (not self.vc_abs_require_close_back) or (last_close < prev_high):
                return {"dir": "BEAR", "kind": "ABSORB_SWEEP_HIGH", "level": prev_high}

        if bull_overshoot >= min_overshoot and last_low < prev_low:
            if (not self.vc_abs_require_close_back) or (last_close > prev_low):
                return {"dir": "BULL", "kind": "ABSORB_SWEEP_LOW", "level": prev_low}

        return None

    def volume_confirmation(self, df_1H: pd.DataFrame) -> Tuple[Optional[str], str, str]:
        sw = self.detect_absorption_sweep(df_1H)
        if sw is None:
            return None, "VC: none (no absorption sweep)", "NONE"
        txt = f"VC ABSORB: {sw['kind']} level={float(sw['level']):.6f}"
        return str(sw["dir"]), txt, "ABSORB"

    def _describe_trigger_tf(
        self,
        df: pd.DataFrame,
        side: Side,
        tf_label: str,
        pivot_window: int,
        momentum_body_k: float,
        min_wick_pct: float,
    ) -> str:
        if df is None or df.empty or len(df) < 40:
            return f"{tf_label}: insufficient_data"

        df2 = mark_pivots(df, window=pivot_window)
        last = df2.iloc[-1]
        prev = df2.iloc[-2]
        last_close = float(last["close"])
        last_open = float(last["open"])
        body = abs(last_close - last_open)
        avg_range = float(
            (
                df2.tail(14)["high"]
                - df2.tail(14)["low"]
            ).mean()
        )

        parts: List[str] = []

        trig = m15_pivot_trigger(
            df2,
            lookback=80,
            mode="close_reclaim",
            wick_confirm=True,
            min_wick_pct=min_wick_pct,
        )
        parts.append(
            f"pivot_reclaim={'yes' if trig is not None and trig.get('side') == side else 'no'}"
        )

        trig2 = m15_pivot_trigger(
            df2,
            lookback=80,
            mode="two_close",
            wick_confirm=True,
            min_wick_pct=min_wick_pct,
        )
        parts.append(
            f"pivot_trap={'yes' if trig2 is not None and trig2.get('side') == side else 'no'}"
        )

        if avg_range > 0:
            min_body = float(momentum_body_k) * avg_range
            if side == "LONG":
                momentum_ok = (
                    body >= min_body
                    and last_close > last_open
                    and last_close > float(prev["high"])
                )
            else:
                momentum_ok = (
                    body >= min_body
                    and last_close < last_open
                    and last_close < float(prev["low"])
                )
            parts.append(
                f"momentum={'yes' if momentum_ok else 'no'} body={body:.6f}/{min_body:.6f}"
            )
        else:
            parts.append("momentum=no avg_range<=0")

        if self.near_pivot_enabled:
            atr_val = self._atr(df2, 14)
            if atr_val > 0:
                max_dist = float(
                    self.near_pivot_atr_mult
                ) * atr_val
                if side == "LONG":
                    piv_lows = df2[
                        df2["pivot"] == 2
                    ]
                    if not piv_lows.empty:
                        lvl = float(
                            piv_lows["low"].iloc[-1]
                        )
                        dist = last_close - lvl
                        near_ok = (
                            last_close >= lvl
                            and dist <= max_dist
                        )
                        parts.append(
                            f"near_pivot={'yes' if near_ok else 'no'} support={lvl:.6f} dist={dist:.6f}/{max_dist:.6f}"
                        )
                    else:
                        parts.append("near_pivot=no no_pivot_low")
                else:
                    piv_highs = df2[
                        df2["pivot"] == 1
                    ]
                    if not piv_highs.empty:
                        lvl = float(
                            piv_highs["high"].iloc[-1]
                        )
                        dist = lvl - last_close
                        near_ok = (
                            last_close <= lvl
                            and dist <= max_dist
                        )
                        parts.append(
                            f"near_pivot={'yes' if near_ok else 'no'} resistance={lvl:.6f} dist={dist:.6f}/{max_dist:.6f}"
                        )
                    else:
                        parts.append("near_pivot=no no_pivot_high")
            else:
                parts.append("near_pivot=no atr<=0")
        else:
            parts.append("near_pivot=disabled")

        return f"{tf_label}: " + " | ".join(parts)

    def _trigger_tf(
        self,
        df: pd.DataFrame,
        side: Side,
        tf_label: str,
        pivot_window: int,
        momentum_body_k: float,
        min_wick_pct: float,
    ) -> Optional[CandidateEntry]:
        if df is None or df.empty or len(df) < 40:
            return None

        df2 = mark_pivots(df, window=pivot_window)

        trig = m15_pivot_trigger(df2, lookback=80, mode="close_reclaim", wick_confirm=True, min_wick_pct=min_wick_pct)
        if trig is not None and trig.get("side") == side:
            entry = float(df2["close"].iloc[-1])
            lvl = float(trig.get("level", entry))
            kind = str(trig.get("kind", "PIVOT"))
            pk = str(trig.get("pivot_kind", "PIVOT"))
            ov = float(trig.get("overshoot_pct", 0.0))
            return CandidateEntry(side=side, entry_price=entry, tf=tf_label, reason=f"Pivot {kind} ({pk}) lvl={lvl:.6f} sweep={ov:.2f}%")

        trig2 = m15_pivot_trigger(df2, lookback=80, mode="two_close", wick_confirm=True, min_wick_pct=min_wick_pct)
        if trig2 is not None and trig2.get("side") == side:
            entry = float(df2["close"].iloc[-1])
            lvl = float(trig2.get("level", entry))
            pk = str(trig2.get("pivot_kind", "PIVOT"))
            ov = float(trig2.get("overshoot_pct", 0.0))
            return CandidateEntry(side=side, entry_price=entry, tf=tf_label, reason=f"Pivot TRAP_2C ({pk}) lvl={lvl:.6f} sweep={ov:.2f}%")

        last = df2.iloc[-1]
        prev = df2.iloc[-2]
        body = abs(float(last["close"]) - float(last["open"]))
        avg_range = float((df2.tail(14)["high"] - df2.tail(14)["low"]).mean())
        if avg_range <= 0:
            return None
        min_body = float(momentum_body_k) * avg_range

        if side == "LONG":
            if body >= min_body and float(last["close"]) > float(last["open"]) and float(last["close"]) > float(prev["high"]):
                return CandidateEntry(side=side, entry_price=float(last["close"]), tf=tf_label, reason=f"Momentum LONG ({tf_label}) body>={momentum_body_k:.2f}*avg_range")

        if side == "SHORT":
            if body >= min_body and float(last["close"]) < float(last["open"]) and float(last["close"]) < float(prev["low"]):
                return CandidateEntry(side=side, entry_price=float(last["close"]), tf=tf_label, reason=f"Momentum SHORT ({tf_label}) body>={momentum_body_k:.2f}*avg_range")

        if self.near_pivot_enabled:
            atr_val = self._atr(df2, 14)
            if atr_val > 0:
                max_dist = float(
                    self.near_pivot_atr_mult
                ) * atr_val
                last_close = float(
                    df2["close"].iloc[-1]
                )

                if side == "LONG":
                    piv_lows = df2[
                        df2["pivot"] == 2
                    ]
                    if not piv_lows.empty:
                        lvl = float(
                            piv_lows["low"].iloc[-1]
                        )
                        if (
                            last_close >= lvl
                            and (last_close - lvl)
                            <= max_dist
                        ):
                            return CandidateEntry(
                                side=side,
                                entry_price=last_close,
                                tf=tf_label,
                                reason=(
                                    f"NearPivot LONG ({tf_label}) "
                                    f"support={lvl:.6f} "
                                    f"dist={((last_close - lvl) / atr_val):.2f}ATR"
                                ),
                            )

                if side == "SHORT":
                    piv_highs = df2[
                        df2["pivot"] == 1
                    ]
                    if not piv_highs.empty:
                        lvl = float(
                            piv_highs["high"].iloc[-1]
                        )
                        if (
                            last_close <= lvl
                            and (lvl - last_close)
                            <= max_dist
                        ):
                            return CandidateEntry(
                                side=side,
                                entry_price=last_close,
                                tf=tf_label,
                                reason=(
                                    f"NearPivot SHORT ({tf_label}) "
                                    f"resistance={lvl:.6f} "
                                    f"dist={((lvl - last_close) / atr_val):.2f}ATR"
                                ),
                            )

        return None

    def trigger_15m_setup(self, df_15M: pd.DataFrame, side: Side) -> Optional[CandidateEntry]:
        return self._trigger_tf(df_15M, side, "15M", self.pivot_window_15m, self.momentum_body_k_15m, self.min_wick_15m)

    def trigger_5m_entry(self, df_5M: pd.DataFrame, side: Side) -> Optional[CandidateEntry]:
        return self._trigger_tf(df_5M, side, "5M", self.pivot_window_5m, self.momentum_body_k_5m, self.min_wick_5m)

    def _stop_by_williams_second(self, df_1H: pd.DataFrame, side: Side, entry: float) -> Optional[float]:
        fr = detect_williams_fractals(df_1H)
        if not fr:
            return None
        if side == "LONG":
            lows = sorted([float(f["price"]) for f in fr if f.get("type") == "low" and float(f["price"]) < entry], reverse=True)
            return float(lows[1] if len(lows) >= 2 else lows[0]) if lows else None
        highs = sorted([float(f["price"]) for f in fr if f.get("type") == "high" and float(f["price"]) > entry])
        return float(highs[1] if len(highs) >= 2 else highs[0]) if highs else None

    def _tp_levels_by_williams(self, df_1H: pd.DataFrame, side: Side, entry: float, stop: float) -> List[float]:
        if df_1H is None or df_1H.empty or len(df_1H) < 50:
            return []
        fr = detect_williams_fractals(df_1H)
        if not fr:
            return []

        atr = self._atr(df_1H, 14)
        min_gap = max(0.0, self.min_tp_gap_atr * atr)

        if side == "LONG":
            levels = sorted({float(f["price"]) for f in fr if f.get("type") == "high" and float(f["price"]) > entry})
        else:
            levels = sorted({float(f["price"]) for f in fr if f.get("type") == "low" and float(f["price"]) < entry}, reverse=True)

        out: List[float] = []
        risk = abs(entry - stop)
        for lvl in levels:
            if len(out) >= self.max_tp_levels:
                break
            if abs(lvl - entry) < min_gap:
                continue
            if risk > 0:
                rr = abs(lvl - entry) / risk
                if rr < 0.60:
                    continue
            out.append(float(lvl))
        return out[: self.max_tp_levels]

    def calc_stop_tp(self, entry: float, side: Side, df_1H: pd.DataFrame) -> Tuple[float, float, List[float]]:
        atr = self._atr(df_1H, 14)

        stop: Optional[float] = None
        if self.stop_mode == "williams_second":
            stop = self._stop_by_williams_second(df_1H, side, entry)
        if stop is None:
            d = atr * float(self.atr_stop_mult)
            stop = entry - d if side == "LONG" else entry + d
        stop = float(stop)

        min_dist = entry * self.min_stop_pct
        actual_dist = abs(entry - stop)
        if actual_dist < min_dist:
            stop = (
                entry - min_dist
                if side == "LONG"
                else entry + min_dist
            )

        tps = self._tp_levels_by_williams(df_1H, side, entry, stop)
        if not tps:
            risk = abs(entry - stop)
            reward = risk * self.rr_min
            tps = [float(entry + reward if side == "LONG" else entry - reward)]

        tp_main = float(tps[-1])
        return stop, tp_main, tps

    def generate_signal(self, data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        try:
            df_4H = data.get("4H")
            df_1H = data.get("1H")
            df_15M = data.get("15M")
            df_5M = data.get("5M")

            if any(d is None or d.empty for d in (df_4H, df_1H, df_15M, df_5M)):
                return {"signal": "NO_DATA"}

            side_bias, narrative_text, score_long, score_short = self.calc_narrative(df_4H, df_1H)
            if side_bias == "NEUTRAL":
                return {"signal": "NO_TREND", "narrative": narrative_text}

            setup_15m = self.trigger_15m_setup(df_15M, side_bias)
            if setup_15m is None:
                debug_15m = self._describe_trigger_tf(
                    df_15M,
                    side_bias,
                    "15M",
                    self.pivot_window_15m,
                    self.momentum_body_k_15m,
                    self.min_wick_15m,
                )
                return {
                    "signal": "NO_TRIGGER",
                    "narrative": narrative_text,
                    "setup": "No setup on 15M",
                    "debug": debug_15m,
                }

            vc_dir, vc_text, vc_strength = self.volume_confirmation(df_1H)

            if self.vc_mode == "strict" and vc_dir is None:
                return {"signal": "WAIT_PHASE", "narrative": narrative_text, "vc": vc_text, "setup": setup_15m.reason}

            if self.vc_mode == "relaxed" and vc_dir is None:
                strong_bias = (score_long == 3 and side_bias == "LONG") or (score_short == 3 and side_bias == "SHORT")
                setup_is_pivot = ("Pivot" in setup_15m.reason)
                if not (strong_bias or setup_is_pivot):
                    return {"signal": "WAIT_PHASE", "narrative": narrative_text, "vc": vc_text + " | relaxed: need strong bias or pivot setup", "setup": setup_15m.reason}
                vc_dir = "BULL" if side_bias == "LONG" else "BEAR"
                vc_text = "VC RELAXED: pass (bias/setup)"

            if side_bias == "LONG" and vc_dir == "BEAR" and vc_strength == "ABSORB":
                return {"signal": "WAIT_PHASE", "narrative": narrative_text, "vc": vc_text + " | Conflict with LONG bias", "setup": setup_15m.reason}
            if side_bias == "SHORT" and vc_dir == "BULL" and vc_strength == "ABSORB":
                return {"signal": "WAIT_PHASE", "narrative": narrative_text, "vc": vc_text + " | Conflict with SHORT bias", "setup": setup_15m.reason}

            entry_5m = self.trigger_5m_entry(df_5M, side_bias)
            entry_candidate = entry_5m

            if entry_candidate is None and self.allow_15m_entry_fallback:
                entry_candidate = CandidateEntry(
                    side=setup_15m.side,
                    entry_price=float(df_15M["close"].iloc[-1]),
                    tf="15M",
                    reason=f"Fallback entry on 15M close ({setup_15m.reason})",
                )

            if entry_candidate is None:
                debug_5m = self._describe_trigger_tf(
                    df_5M,
                    side_bias,
                    "5M",
                    self.pivot_window_5m,
                    self.momentum_body_k_5m,
                    self.min_wick_5m,
                )
                return {
                    "signal": "WAIT_PHASE",
                    "narrative": narrative_text,
                    "vc": vc_text,
                    "setup": f"15M OK: {setup_15m.reason} | waiting 5M entry",
                    "debug": debug_5m,
                }

            stop, tp_main, tps = self.calc_stop_tp(entry_candidate.entry_price, entry_candidate.side, df_1H)
            risk = abs(entry_candidate.entry_price - stop)
            reward = abs(tp_main - entry_candidate.entry_price)
            rr_num = (reward / risk) if risk > 0 else 0.0

            if rr_num < self.rr_min:
                return {"signal": "WAIT_PHASE", "narrative": narrative_text, "vc": vc_text, "setup": setup_15m.reason, "reason": f"RR too low: 1:{rr_num:.2f} < min 1:{self.rr_min:.2f}"}

            tp_fields: Dict[str, Any] = {}
            for i, lvl in enumerate(tps[:4], start=1):
                tp_fields[f"tp{i}_price"] = round(float(lvl), 6)

            return {
                "signal": "ENTER",
                "side": entry_candidate.side,
                "entry_price": round(float(entry_candidate.entry_price), 6),
                "stop_price": round(float(stop), 6),
                "tp_price": round(float(tp_main), 6),
                "tp_prices": [round(float(x), 6) for x in tps[:4]],
                **tp_fields,
                "rr": round(float(rr_num), 4),
                "rr_str": f"1:{rr_num:.2f}",
                "risk_percent": f"{self.risk_per_trade * 100:.2f}%",
                "tf": entry_candidate.tf,
                "narrative": narrative_text,
                "vc": vc_text,
                "setup": f"{setup_15m.reason}",
                "trigger_reason": f"Setup15M: {setup_15m.reason} | Entry: {entry_candidate.reason}",
                "meta": {
                    "vc_mode": self.vc_mode,
                    "vc_strength": vc_strength,
                    "stop_mode": self.stop_mode,
                    "atr_stop_mult": self.atr_stop_mult,
                    "allow_15m_entry_fallback": self.allow_15m_entry_fallback,
                },
            }

        except Exception as e:
            traceback.print_exc()
            return {"signal": "ERROR", "error": str(e)}
