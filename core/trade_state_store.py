from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


class TradeStateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        try:
            if not self.path.exists():
                return {}
            data = json.loads(
                self.path.read_text(encoding="utf-8")
            )
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(
        self,
        *,
        active_trades: Dict[str, Dict[str, Any]],
        tp_state: Dict[str, Dict[str, Any]],
    ) -> None:
        try:
            payload = {
                "updated_ts": time.time(),
                "active_trades": active_trades,
                "tp_state": tp_state,
            }
            self.path.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
