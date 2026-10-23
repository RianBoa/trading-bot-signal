# core/m1/feature_extractor.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Any, Union
import numpy as np
import pandas as pd


@dataclass
class FeatureExtractor:
    cfg: Optional[Any] = None   # принимает AIConfig или что угодно
    """
    Делает признаки из твоих OHLCV dataframe.
    Ожидает data: {"D":df, "4H":df, "1H":df, "15M":df, "5M":df}
    Все df должны иметь колонки: open, high, low, close, volume
    Индекс: datetime или любой упорядоченный
    """

    # ---- helpers ----
    @staticmethod
    def _safe_last(df: Optional[pd.DataFrame], col: str) -> float:
        if df is None or df.empty or col not in df.columns:
            return float("nan")
        try:
            return float(df[col].iloc[-1])
        except Exception:
            return float("nan")

    @staticmethod
    def _ret(df: Optional[pd.DataFrame], n: int) -> float:
        if df is None or df.empty or len(df) < n + 1 or "close" not in df.columns:
            return float("nan")
        c = df["close"].astype(float)
        prev = c.iloc[-1 - n]
        if prev == 0 or not np.isfinite(prev):
            return float("nan")
        return float(c.iloc[-1] / prev - 1.0)

    @staticmethod
    def _atr(df: Optional[pd.DataFrame], period: int = 14) -> float:
        if (
            df is None or df.empty or len(df) < period + 2
            or not {"high", "low", "close"}.issubset(df.columns)
        ):
            return float("nan")

        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close_prev = df["close"].astype(float).shift(1)

        tr = np.maximum(
            high - low,
            np.maximum((high - close_prev).abs(), (low - close_prev).abs())
        )
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if np.isfinite(atr) else float("nan")

    @staticmethod
    def _rvol(df: Optional[pd.DataFrame], period: int = 48) -> float:
        # realized vol по 5M или 15M – просто std доходностей
        if df is None or df.empty or len(df) < period + 2 or "close" not in df.columns:
            return float("nan")
        r = df["close"].astype(float).pct_change()
        v = r.tail(period).std()
        return float(v) if np.isfinite(v) else float("nan")

    @staticmethod
    def _sig_get(sig: Any, key: str, default: float = float("nan")) -> float:
        """
        Достаёт поле из sig, если sig:
        - dict: sig.get(key)
        - объект: getattr(sig, key)
        Любые нечисловые/пустые значения -> default
        """
        try:
            if isinstance(sig, dict):
                val = sig.get(key, default)
            else:
                val = getattr(sig, key, default)
            if val is None:
                return default
            # булевы -> 0/1
            if isinstance(val, bool):
                return float(val)
            return float(val)
        except Exception:
            return default

    @staticmethod
    def _nan_to_num_dict(d: Dict[str, float], fill: float = 0.0) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k, v in d.items():
            try:
                out[k] = float(v)
            except Exception:
                out[k] = fill
            if not np.isfinite(out[k]):
                out[k] = fill
        return out

    # ---- core builder (твой) ----
    def build(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        d1 = data.get("D")
        h4 = data.get("4H")
        h1 = data.get("1H")
        m15 = data.get("15M")
        m5 = data.get("5M")

        feats: Dict[str, float] = {}

        # price levels
        feats["px_1h"] = self._safe_last(h1, "close")
        feats["px_4h"] = self._safe_last(h4, "close")
        feats["px_5m"] = self._safe_last(m5, "close")

        # returns
        feats["ret_1h_6"] = self._ret(h1, 6)      # ~6 часов
        feats["ret_4h_6"] = self._ret(h4, 6)      # ~1 день
        feats["ret_15m_16"] = self._ret(m15, 16)  # ~4 часа
        feats["ret_5m_48"] = self._ret(m5, 48)    # ~4 часа

        # vol / atr
        feats["atr_1h_14"] = self._atr(h1, 14)
        feats["atr_4h_14"] = self._atr(h4, 14)
        feats["rvol_5m_48"] = self._rvol(m5, 48)
        feats["rvol_15m_48"] = self._rvol(m15, 48)

        # volumes
        feats["vol_1h"] = self._safe_last(h1, "volume")
        feats["vol_4h"] = self._safe_last(h4, "volume")
        feats["vol_5m"] = self._safe_last(m5, "volume")

        # simple normalization hints
        feats["atr1h_pct"] = (
            float(feats["atr_1h_14"] / feats["px_1h"])
            if np.isfinite(feats["atr_1h_14"]) and np.isfinite(feats["px_1h"]) and feats["px_1h"] != 0
            else float("nan")
        )
        feats["atr4h_pct"] = (
            float(feats["atr_4h_14"] / feats["px_4h"])
            if np.isfinite(feats["atr_4h_14"]) and np.isfinite(feats["px_4h"]) and feats["px_4h"] != 0
            else float("nan")
        )

        return feats

    # ---- compatibility layer for ai_live.py ----
    def make_features(
        self,
        symbol: str,
        sig: Optional[Any] = None,
        data: Optional[Dict[str, pd.DataFrame]] = None,
        *,
        nan_fill: Optional[float] = 0.0,
    ) -> Dict[str, float]:
        """
        Совместимо с вызовом из ai_live.py:
            self.fe.make_features(symbol=symbol, sig=sig, data=data)

        Возвращает dict[str,float] признаков (OHLCV + сигнальные поля).
        nan_fill:
          - 0.0 (по умолчанию): заменяет NaN/inf на 0 (удобно для моделей)
          - None: оставляет NaN как есть
        """
        data = data or {}

        feats = self.build(symbol=symbol, data=data)

        # добавим немного из "sig" (не ломая ничего если полей нет)
        if sig is not None:
            feats["sig_side"] = self._sig_get(sig, "side")          # иногда строка -> даст nan (ок)
            feats["sig_dir"] = self._sig_get(sig, "dir")            # если ты хранишь направление числом
            feats["sig_score"] = self._sig_get(sig, "score")
            feats["sig_conf"] = self._sig_get(sig, "confidence")
            feats["sig_tf"] = self._sig_get(sig, "tf")
            feats["sig_is_long"] = self._sig_get(sig, "is_long")
            feats["sig_is_short"] = self._sig_get(sig, "is_short")

        # опционально нормализуем NaN/inf
        if nan_fill is None:
            return feats
        return self._nan_to_num_dict(feats, fill=float(nan_fill))
