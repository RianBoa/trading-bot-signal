from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


class BotStateStore:
    """
    Persists Telegram bot state:
      - private chat_id
      - reply roots for ENTER -> TP/EXIT threading
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Tuple[Optional[int], Dict[Tuple[int, str], int]]:
        chat_id: Optional[int] = None
        reply_roots: Dict[Tuple[int, str], int] = {}
        try:
            if not self.path.exists():
                return chat_id, reply_roots

            data = json.loads(self.path.read_text(encoding="utf-8"))
            rr = data.get("reply_roots") or {}
            if isinstance(rr, dict):
                for k, v in rr.items():
                    if not isinstance(k, str) or "|" not in k:
                        continue
                    cid_s, sym = k.split("|", 1)
                    try:
                        cid = int(cid_s)
                        mid = int(v)
                    except Exception:
                        continue
                    reply_roots[(cid, sym.strip().upper())] = mid

            raw_cid = data.get("chat_id")
            if raw_cid is not None:
                try:
                    chat_id = int(raw_cid)
                except Exception:
                    chat_id = None
        except Exception:
            pass
        return chat_id, reply_roots

    def save(self, *, chat_id: Optional[int], reply_roots: Dict[Tuple[int, str], int]) -> None:
        try:
            data = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}

            if chat_id is not None:
                data["chat_id"] = int(chat_id)
            data["updated_ts"] = time.time()
            data["reply_roots"] = {f"{cid}|{sym}": int(mid) for (cid, sym), mid in reply_roots.items()}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def load_recent_emissions(self) -> Dict[str, float]:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("recent_emissions") or {}
            if not isinstance(raw, dict):
                return {}
            out: Dict[str, float] = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not key:
                    continue
                try:
                    out[key] = float(value)
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    def save_recent_emissions(self, recent_emissions: Dict[str, float]) -> None:
        try:
            data = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}

            data["updated_ts"] = time.time()
            data["recent_emissions"] = {
                str(k): float(v)
                for k, v in recent_emissions.items()
                if str(k)
            }
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def load_signal_states(self) -> Dict[str, object]:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data.get("signal_states") or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def save_signal_states(self, signal_states: Dict[str, object]) -> None:
        try:
            data = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}

            data["updated_ts"] = time.time()
            data["signal_states"] = signal_states
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
