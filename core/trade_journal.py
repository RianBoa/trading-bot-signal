from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps_safe(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return "{}"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _parse_rr(v: Any) -> Optional[float]:
    """
    Принимаем rr в разных форматах:
      - 1.5
      - "1.5"
      - "1,5"
      - "1:1.50"  -> 1.50
      - "RR 1:2"  -> 2
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip()
    if not s:
        return None

    # "1:1.50" -> берем правую часть
    if ":" in s:
        s = s.split(":", 1)[1].strip()

    s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


@dataclass
class TradeRow:
    ts_open: str
    ts_close: Optional[str]
    symbol: str
    side: str
    entry: float
    stop: float
    tp: float
    exit: Optional[float]
    outcome: Optional[str]
    rr: Optional[float]
    tf: Optional[str]
    narrative: Optional[str]
    trigger_reason: Optional[str]
    vc: Optional[str]
    meta_json: str
    features_json: str
    telegram_chat_id_open: Optional[int]
    telegram_message_id_open: Optional[int]
    telegram_chat_id_close: Optional[int]
    telegram_message_id_close: Optional[int]


class TradeJournal:
    """
    Пишем сделки в SQLite.
    CSV всегда пересобираем из SQLite.
    Parquet — по возможности (если есть pandas + pyarrow/fastparquet).
    """

    def __init__(
        self,
        db_path: str,
        csv_path: Optional[str] = None,
        parquet_path: Optional[str] = None,
        export_on_each_event: bool = True,
    ):
        self.db_path = Path(db_path)
        self.csv_path = Path(csv_path) if csv_path else None
        self.parquet_path = Path(parquet_path) if parquet_path else None
        self.export_on_each_event = export_on_each_event

        _ensure_dir(self.db_path)
        if self.csv_path:
            _ensure_dir(self.csv_path)
        if self.parquet_path:
            _ensure_dir(self.parquet_path)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # WAL полезен для живого бота
        try:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass

        self._ensure_schema()
        self._migrate_columns()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    # ----------------- schema / migrations -----------------

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_open TEXT NOT NULL,
                    ts_close TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop REAL NOT NULL,
                    tp REAL NOT NULL,
                    exit REAL,
                    outcome TEXT,
                    rr REAL,
                    tf TEXT,
                    narrative TEXT,
                    trigger_reason TEXT,
                    vc TEXT,
                    meta_json TEXT,
                    features_json TEXT,
                    telegram_chat_id_open INTEGER,
                    telegram_message_id_open INTEGER,
                    telegram_chat_id_close INTEGER,
                    telegram_message_id_close INTEGER
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_symbol_open ON trades(symbol, ts_open);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_symbol_close ON trades(symbol, ts_close);"
            )
            self._conn.commit()

    def _column_exists(self, table: str, col: str) -> bool:
        cur = self._conn.execute(f"PRAGMA table_info({table});")
        return any(row["name"] == col for row in cur.fetchall())

    def _migrate_columns(self) -> None:
        """
        Добавляем колонки, если старая схема.
        """
        with self._lock:
            needed = {
                "tf": "TEXT",
                "narrative": "TEXT",
                "trigger_reason": "TEXT",
                "vc": "TEXT",
                "meta_json": "TEXT",
                "features_json": "TEXT",
                "telegram_chat_id_open": "INTEGER",
                "telegram_message_id_open": "INTEGER",
                "telegram_chat_id_close": "INTEGER",
                "telegram_message_id_close": "INTEGER",
            }
            for col, ddl in needed.items():
                if not self._column_exists("trades", col):
                    self._conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl};")
            self._conn.commit()

    # ----------------- core API -----------------

    def ingest_signal(
        self,
        symbol: str,
        sig: Dict[str, Any],
        telegram_chat_id: Optional[int] = None,
        telegram_message_id: Optional[int] = None,
        features: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        ENTER: signal="ENTER", side, entry_price, stop_price, tp_price, rr, tf, ...
        EXIT:  signal="EXIT_TP"/"EXIT_SL", exit_price, ...
        """
        if not isinstance(sig, dict):
            return

        st = sig.get("signal")

        if st == "ENTER":
            entry = sig.get("entry_price")
            stop = sig.get("stop_price")
            tp = sig.get("tp_price")
            if entry is None or stop is None or tp is None:
                return

            self._open_trade(
                symbol=symbol,
                side=str(sig.get("side", "")).upper(),
                entry=float(entry),
                stop=float(stop),
                tp=float(tp),
                rr=_parse_rr(sig.get("rr")),
                tf=str(sig.get("tf")) if sig.get("tf") is not None else None,
                narrative=str(sig.get("narrative")) if sig.get("narrative") is not None else None,
                trigger_reason=str(sig.get("trigger_reason")) if sig.get("trigger_reason") is not None else None,
                vc=str(sig.get("vc")) if sig.get("vc") is not None else None,
                meta=sig,
                features=features,
                telegram_chat_id_open=telegram_chat_id,
                telegram_message_id_open=telegram_message_id,
            )
            if self.export_on_each_event:
                self.export_all()

        elif st in ("EXIT_TP", "EXIT_SL"):
            outcome = "TP" if st == "EXIT_TP" else "SL"
            exit_price = sig.get("exit_price")
            if exit_price is None:
                return

            self._close_trade(
                symbol=symbol,
                exit=float(exit_price),
                outcome=outcome,
                telegram_chat_id_close=telegram_chat_id,
                telegram_message_id_close=telegram_message_id,
            )
            if self.export_on_each_event:
                self.export_all()

    def find_open_thread_root(
        self,
        chat_id: int,
        symbols: list[str],
    ) -> Optional[Tuple[int, str]]:
        keys = [
            str(sym or "").upper().strip()
            for sym in symbols
            if str(sym or "").upper().strip()
        ]
        if not keys:
            return None

        placeholders = ",".join("?" for _ in keys)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT symbol, telegram_message_id_open
                FROM trades
                WHERE ts_close IS NULL
                  AND telegram_chat_id_open = ?
                  AND telegram_message_id_open IS NOT NULL
                  AND UPPER(symbol) IN ({placeholders})
                ORDER BY id DESC;
                """,
                (int(chat_id), *keys),
            ).fetchall()

        by_symbol = {}
        for row in rows:
            try:
                sym = str(row["symbol"] or "").upper().strip()
                mid = int(row["telegram_message_id_open"])
            except Exception:
                continue
            if sym and sym not in by_symbol:
                by_symbol[sym] = mid

        for key in keys:
            mid = by_symbol.get(key)
            if mid:
                return int(mid), key
        return None

    # ----------------- trade operations -----------------

    def _find_last_open_trade_id(self, symbol: str) -> Optional[int]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id
                FROM trades
                WHERE symbol = ?
                  AND ts_close IS NULL
                ORDER BY id DESC
                LIMIT 1;
                """,
                (symbol,),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None

    def _open_trade(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        tp: float,
        rr: Optional[float],
        tf: Optional[str],
        narrative: Optional[str],
        trigger_reason: Optional[str],
        vc: Optional[str],
        meta: Optional[Dict[str, Any]],
        features: Optional[Dict[str, Any]],
        telegram_chat_id_open: Optional[int],
        telegram_message_id_open: Optional[int],
    ) -> None:
        ts_open = _utc_now_iso()
        with self._lock:
            # защита от дубля: если уже есть открытая по символу — новую не создаём
            if self._find_last_open_trade_id(symbol) is not None:
                return

            self._conn.execute(
                """
                INSERT INTO trades (
                    ts_open, ts_close, symbol, side,
                    entry, stop, tp, exit, outcome, rr,
                    tf, narrative, trigger_reason, vc,
                    meta_json, features_json,
                    telegram_chat_id_open, telegram_message_id_open
                ) VALUES (
                    ?, NULL, ?, ?,
                    ?, ?, ?, NULL, NULL, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?
                );
                """,
                (
                    ts_open, symbol, side,
                    entry, stop, tp, rr,
                    tf, narrative, trigger_reason, vc,
                    _json_dumps_safe(meta or {}),
                    _json_dumps_safe(features or {}),
                    telegram_chat_id_open, telegram_message_id_open,
                ),
            )
            self._conn.commit()

    def _close_trade(
        self,
        symbol: str,
        exit: float,
        outcome: str,
        telegram_chat_id_close: Optional[int],
        telegram_message_id_close: Optional[int],
    ) -> None:
        ts_close = _utc_now_iso()
        with self._lock:
            trade_id = self._find_last_open_trade_id(symbol)
            if trade_id is None:
                return

            self._conn.execute(
                """
                UPDATE trades
                SET ts_close = ?,
                    exit = ?,
                    outcome = ?,
                    telegram_chat_id_close = ?,
                    telegram_message_id_close = ?
                WHERE id = ?;
                """,
                (
                    ts_close, exit, outcome,
                    telegram_chat_id_close, telegram_message_id_close,
                    trade_id,
                ),
            )
            self._conn.commit()

    # ----------------- exports / stats -----------------

    def export_csv(self) -> None:
        if not self.csv_path:
            return

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    ts_open, ts_close, symbol, side, entry, stop, tp, exit, outcome, rr,
                    tf, narrative, trigger_reason, vc, meta_json, features_json,
                    telegram_chat_id_open, telegram_message_id_open,
                    telegram_chat_id_close, telegram_message_id_close
                FROM trades
                ORDER BY ts_open ASC;
                """
            ).fetchall()

        tmp = self.csv_path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ts_open","ts_close","symbol","side","entry","stop","tp","exit","outcome","rr",
                "tf","narrative","trigger_reason","vc","meta_json","features_json",
                "telegram_chat_id_open","telegram_message_id_open",
                "telegram_chat_id_close","telegram_message_id_close",
            ])
            for r in rows:
                writer.writerow([r[k] for k in r.keys()])

        os.replace(tmp, self.csv_path)

    def export_parquet(self) -> None:
        if not self.parquet_path:
            return

        try:
            import pandas as pd  # type: ignore
        except Exception:
            return

        engine_ok = False
        try:
            import pyarrow  # noqa
            engine_ok = True
        except Exception:
            pass

        if not engine_ok:
            try:
                import fastparquet  # noqa
                engine_ok = True
            except Exception:
                pass

        if not engine_ok:
            return

        with self._lock:
            df = pd.read_sql_query(
                """
                SELECT
                    ts_open, ts_close, symbol, side, entry, stop, tp, exit, outcome, rr,
                    tf, narrative, trigger_reason, vc, meta_json, features_json,
                    telegram_chat_id_open, telegram_message_id_open,
                    telegram_chat_id_close, telegram_message_id_close
                FROM trades
                ORDER BY ts_open ASC;
                """,
                self._conn,
            )

        tmp = self.parquet_path.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, self.parquet_path)

    def export_all(self) -> None:
        self.export_csv()
        self.export_parquet()

    def winrate(self) -> Tuple[float, int, int, int]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN outcome='TP' THEN 1 ELSE 0 END) AS tp,
                    SUM(CASE WHEN outcome='SL' THEN 1 ELSE 0 END) AS sl,
                    SUM(CASE WHEN outcome IN ('TP','SL') THEN 1 ELSE 0 END) AS closed
                FROM trades;
                """
            ).fetchone()

        tp = int(row["tp"] or 0)
        sl = int(row["sl"] or 0)
        closed = int(row["closed"] or 0)
        wr = (tp / closed) if closed > 0 else 0.0
        return wr, closed, tp, sl
