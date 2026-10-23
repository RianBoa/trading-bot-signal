
# core/m1/store.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

import json
import re
import pandas as pd

from core.m1.config import AIConfig
from core.m1.model import OnlineLogReg


def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Фиксирует dtype колонок, чтобы pd.concat не ругался на all-NA колонки.
    """
    if df is None:
        return pd.DataFrame()

    schema_cols = [
        "ts_open", "ts_close", "symbol", "side",
        "entry", "stop", "tp", "exit",
        "outcome", "rr", "meta_json", "features_json",
    ]

    # гарантируем наличие всех колонок
    for c in schema_cols:
        if c not in df.columns:
            df[c] = pd.NA

    # datetime
    df["ts_open"] = pd.to_datetime(df["ts_open"], errors="coerce")
    df["ts_close"] = pd.to_datetime(df["ts_close"], errors="coerce")

    # numeric
    for c in ("entry", "stop", "tp", "exit", "rr"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # string
    for c in ("symbol", "side", "outcome", "meta_json", "features_json"):
        df[c] = df[c].astype("string")

    return df[schema_cols]


def _to_float(v: Any, default: float = 0.0) -> float:
    """Безопасно приводит к float: поддерживает int/float/строки '1.23' / '1,23'."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        try:
            x = float(v)
            return x if pd.notna(x) else default
        except Exception:
            return default
    s = str(v).strip()
    if not s:
        return default
    try:
        x = float(s.replace(",", "."))
        return x if pd.notna(x) else default
    except Exception:
        return default


def _parse_rr(v: Any, default: float = 0.0) -> float:
    """
    Приводит RR к числу reward/risk.

    Поддерживает:
      - 1.5 / "1.5" / "1,5"
      - "1:1.50" -> 1.50
      - "1/1.50" -> 1.50
      - "RR 1:2" -> 2.0
      - "1 : 1.5" (с пробелами)
    """
    if v is None:
        return default

    # уже число
    if isinstance(v, (int, float)):
        try:
            x = float(v)
            return x if pd.notna(x) else default
        except Exception:
            return default

    s = str(v).strip().lower()
    if not s:
        return default

    # если просто число строкой
    x = _to_float(s, default=float("nan"))
    if pd.notna(x):
        return float(x)

    # форматы "a:b" или "a/b"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[:/]\s*(\d+(?:[.,]\d+)?)", s)
    if not m:
        return default

    a = _to_float(m.group(1), default=float("nan"))
    b = _to_float(m.group(2), default=float("nan"))
    if not pd.notna(a) or not pd.notna(b) or a == 0:
        return default

    # риск:прибыль => reward/risk
    return float(b / a)


@dataclass
class TradeStore:
    cfg: AIConfig

    def __post_init__(self):
        if isinstance(self.cfg.data_dir, str):
            self.cfg.data_dir = Path(self.cfg.data_dir)

        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.cfg.data_dir / "trades.parquet"
        self.model_path = self.cfg.data_dir / "model.json"

        if not self.trades_path.exists():
            df = pd.DataFrame(columns=[
                "ts_open",
                "ts_close",
                "symbol",
                "side",
                "entry",
                "stop",
                "tp",
                "exit",
                "outcome",      # "TP" / "SL" / None
                "rr",
                "meta_json",
                "features_json",
            ])
            # FIX: сразу фиксируем схему, чтобы дальше не было concat warning
            df = _ensure_schema(df)
            df.to_parquet(self.trades_path, index=False)

        self.model = OnlineLogReg.load(self.model_path)

    # ---------- IO ----------

    def _read(self) -> pd.DataFrame:
        try:
            df = pd.read_parquet(self.trades_path) if self.trades_path.exists() else pd.DataFrame()
            return _ensure_schema(df)
        except Exception:
            return _ensure_schema(pd.DataFrame())

    def _write(self, df: pd.DataFrame) -> None:
        df = _ensure_schema(df)
        df.to_parquet(self.trades_path, index=False)

    # ---------- stats ----------

    def count_labeled_trades(self) -> int:
        df = self._read()
        if df.empty or "outcome" not in df.columns:
            return 0
        return int(df["outcome"].notna().sum())

    # ---------- log API expected by ai_live ----------

    def log_enter(self, *, symbol: str, sig: dict, features: Dict[str, Any]) -> None:
        ts_open = pd.Timestamp.utcnow()
        side = str(sig.get("side", ""))

        entry = _to_float(sig.get("entry_price"), 0.0)
        stop = _to_float(sig.get("stop_price"), 0.0)
        tp = _to_float(sig.get("tp_price"), 0.0)

        # FIX: rr может быть строкой "1:1.50"
        rr = _parse_rr(sig.get("rr"), default=0.0)

        meta = {
            "tf": sig.get("tf"),
            "narrative": sig.get("narrative"),
            "trigger_reason": sig.get("trigger_reason"),
            "vc": sig.get("vc"),
        }

        self.append_open(
            ts_open=ts_open,
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            tp=tp,
            rr=rr,
            meta=meta,
            features=features,
        )

    def log_exit(self, *, symbol: str, sig: dict, outcome: str) -> None:
        ts_close = pd.Timestamp.utcnow()
        exit_price = _to_float(sig.get("exit_price"), 0.0)

        # Важно: entry_price должен совпасть с entry в parquet
        entry_price = sig.get("entry_price")

        if entry_price is None:
            # fallback: попробуем найти последнюю незакрытую сделку по символу
            df = self._read()
            if df.empty:
                return
            m = (df["symbol"] == symbol) & (df["outcome"].isna())
            if not m.any():
                return
            entry_price = _to_float(df.loc[m].iloc[-1].get("entry"), 0.0)

        self.mark_closed(
            ts_close=ts_close,
            symbol=symbol,
            entry=_to_float(entry_price, 0.0),
            exit_price=exit_price,
            outcome=outcome,
        )

    # ---------- parquet operations ----------

    def append_open(
        self,
        *,
        ts_open,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        tp: float,
        rr: float,
        meta: Optional[Dict[str, Any]] = None,
        features: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        FIX: убираем FutureWarning от pd.concat с empty/all-NA колонками
        через фиксацию схемы (dtype) для df и row_df.
        """
        df = self._read()

        row = {
            "ts_open": ts_open,
            "ts_close": None,
            "symbol": symbol,
            "side": side,
            "entry": _to_float(entry, 0.0),
            "stop": _to_float(stop, 0.0),
            "tp": _to_float(tp, 0.0),
            "exit": None,
            "outcome": None,
            "rr": _to_float(rr, 0.0),
            "meta_json": json.dumps(meta, ensure_ascii=False) if meta else None,
            "features_json": json.dumps(features, ensure_ascii=False) if features else None,
        }

        row_df = _ensure_schema(pd.DataFrame([row]))
        df = _ensure_schema(df)

        if df.empty:
            self._write(row_df)
            return

        df = pd.concat([df, row_df], ignore_index=True)
        self._write(df)

    def mark_closed(
        self,
        *,
        ts_close,
        symbol: str,
        entry: float,
        exit_price: float,
        outcome: str,
    ) -> None:
        df = self._read()
        if df.empty:
            return

        mask = (df["symbol"] == symbol) & (df["outcome"].isna())
        if not mask.any():
            return

        # предпочитаем совпадение entry (с плавающей точкой лучше через допуск)
        try:
            entry_f = float(entry)
        except Exception:
            entry_f = None

        if entry_f is not None and "entry" in df.columns:
            mask2 = mask & (df["entry"].astype(float) - entry_f).abs().lt(1e-9)
            if mask2.any():
                idx = df[mask2].index.max()
            else:
                idx = df[mask].index.max()
        else:
            idx = df[mask].index.max()

        df.at[idx, "ts_close"] = ts_close
        df.at[idx, "exit"] = _to_float(exit_price, 0.0)
        df.at[idx, "outcome"] = str(outcome)
        self._write(df)

    def load_labeled(self) -> pd.DataFrame:
        df = self._read()
        if df.empty:
            return df
        return df[df["outcome"].notna()].copy()

    # ---------- ML ----------

    def _iter_xy(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            fj = row.get("features_json")
            if not fj:
                continue
            try:
                x = json.loads(fj)
            except Exception:
                continue

            y = 1 if str(row.get("outcome")) == self.cfg.positive_outcome else 0
            yield x, y

    def retrain_model(self) -> bool:
        df = self.load_labeled()
        if df.empty:
            return False

        X = []
        y = []
        for x, yy in self._iter_xy(df):
            X.append(x)
            y.append(yy)

        if len(y) < self.cfg.min_train_samples:
            return False

        self.model.fit_batch(X, y)
        self.model.save(self.model_path)
        return True

    def predict_proba(self, features: Dict[str, Any]) -> float:
        return float(self.model.predict_proba(features))
