from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SignalValidation:
    valid: bool
    reason: str


class SignalValidator:
    def validate(self, sig: dict) -> SignalValidation:
        state = str(sig.get("signal") or "").upper()
        if state == "TP_HIT":
            return self._validate_tp_hit(sig)
        if state == "EXIT_TP":
            return self._validate_exit_tp(sig)
        if state == "EXIT_SL":
            return self._validate_exit_sl(sig)
        return SignalValidation(True, "not_close_signal")

    def _validate_tp_hit(self, sig: dict) -> SignalValidation:
        side = str(sig.get("side") or "").upper()
        tp = _to_float(sig.get("tp_price"))
        hi = _to_float(sig.get("trigger_high"))
        lo = _to_float(sig.get("trigger_low"))

        if tp is None or (hi is None and lo is None):
            return SignalValidation(True, "missing_validation_fields")

        if side == "LONG" and hi is not None and hi >= tp:
            return SignalValidation(True, "tp_reached")
        if side == "SHORT" and lo is not None and lo <= tp:
            return SignalValidation(True, "tp_reached")
        return SignalValidation(False, "tp_not_reached")

    def _validate_exit_tp(self, sig: dict) -> SignalValidation:
        side = str(sig.get("side") or "").upper()
        tp = _to_float(sig.get("tp_price") or sig.get("exit_price"))
        hi = _to_float(sig.get("trigger_high"))
        lo = _to_float(sig.get("trigger_low"))

        if tp is None or (hi is None and lo is None):
            return SignalValidation(True, "missing_validation_fields")

        if side == "LONG" and hi is not None and hi >= tp:
            return SignalValidation(True, "tp_reached")
        if side == "SHORT" and lo is not None and lo <= tp:
            return SignalValidation(True, "tp_reached")
        return SignalValidation(False, "tp_not_reached")

    def _validate_exit_sl(self, sig: dict) -> SignalValidation:
        side = str(sig.get("side") or "").upper()
        stop = _to_float(sig.get("stop_price") or sig.get("exit_price"))
        hi = _to_float(sig.get("trigger_high"))
        lo = _to_float(sig.get("trigger_low"))

        if stop is None or (hi is None and lo is None):
            return SignalValidation(True, "missing_validation_fields")

        if side == "LONG" and lo is not None and lo <= stop:
            return SignalValidation(True, "sl_reached")
        if side == "SHORT" and hi is not None and hi >= stop:
            return SignalValidation(True, "sl_reached")
        return SignalValidation(False, "sl_not_reached")


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None
