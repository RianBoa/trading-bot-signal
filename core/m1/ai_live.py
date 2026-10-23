# core/m1/ai_live.py
from __future__ import annotations

from typing import Dict
import pandas as pd

from core.strategy_narrative import NarrativeStrategy, ActiveTrade
from core.m1.config import AIConfig
from core.m1.store import TradeStore
from core.m1.feature_extractor import FeatureExtractor


class AILive:
    """
    Live ML-gate layer:
    - логирует ENTER/EXIT + фичи в Parquet
    - (опционально) блокирует ENTER, если p(win) ниже порога
    - переобучается каждые N размеченных сделок
    """

    def __init__(self, cfg: AIConfig, store: TradeStore, strategy: NarrativeStrategy):
        self.cfg = cfg
        self.store = store
        self.strategy = strategy

        # FeatureExtractor у тебя уже есть — оставляем как есть.
        # Если вдруг твой FeatureExtractor не принимает cfg — поменяй на FeatureExtractor()
        self.fe = FeatureExtractor(cfg)

        self._last_retrain_labeled_count = 0

    def on_signal(
        self,
        symbol: str,
        sig: dict,
        data: Dict[str, pd.DataFrame],
        active_trades: Dict[str, ActiveTrade],
    ) -> dict:

        if not isinstance(sig, dict) or not self.cfg.enabled:
            return sig

        st = sig.get("signal")

        # Переобучение по счетчику размеченных сделок
        self._maybe_retrain()

        # ENTER: лог + (опционально) гейт
        if st == "ENTER":
            # print("FE type:", type(self.fe))
            # print("FE module:", type(self.fe).__module__)
            # print("FE file:", __import__(type(self.fe).__module__).__file__)
            # print("FE dir has make_features:", hasattr(self.fe, "make_features"))
            # print("FE methods sample:", [m for m in dir(self.fe) if "feature" in m.lower()])

            x = self.fe.make_features(symbol=symbol, sig=sig, data=data)
            self.store.log_enter(symbol=symbol, sig=sig, features=x)

            # если запрещено блокировать — просто пропускаем
            if not self.cfg.allow_block_entries:
                return sig

            # пока мало обучающих примеров — не блокируем
            if self.store.count_labeled_trades() < self.cfg.min_train_samples:
                return sig

            proba = self.store.predict_proba(x)  # p(win)
            sig["ai_p_tp"] = float(proba)

            if proba < float(self.cfg.min_prob_to_allow):
                return {
                    "signal": "AI_REJECT",
                    "reason": f"p(TP)={proba:.3f} < thr={self.cfg.min_prob_to_allow:.2f}",
                    "ai_p_tp": float(proba),
                }

            return sig

        # EXIT: ставим метку последней открытой сделки по символу
        if st in ("EXIT_TP", "EXIT_SL"):
            outcome = "TP" if st == "EXIT_TP" else "SL"
            self.store.log_exit(symbol=symbol, sig=sig, outcome=outcome)
            return sig

        return sig

    def _maybe_retrain(self) -> None:
        if not self.cfg.training_enabled:
            return

        labeled = self.store.count_labeled_trades()
        if labeled < self.cfg.min_train_samples:
            return

        # переобучаемся каждые N новых размеченных сделок
        if labeled - self._last_retrain_labeled_count < self.cfg.retrain_every_n_trades:
            return

        ok = self.store.retrain_model()
        if ok:
            self._last_retrain_labeled_count = labeled
