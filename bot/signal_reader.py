from __future__ import annotations

import traceback
from typing import Any, Dict


class SignalReader:
    def __init__(self, core: Any, engine: Any = None):
        self.core = core
        self.engine = engine

    async def get_signals_safe(self) -> Dict[str, dict]:
        try:
            if self.engine is not None:
                snap = self.engine.get_snapshot() or {}
                if isinstance(snap, dict) and snap:
                    return snap

                if hasattr(self.engine, "ensure_warm"):
                    await self.engine.ensure_warm()
                    snap = self.engine.get_snapshot() or {}
                    if isinstance(snap, dict) and snap:
                        return snap

            if hasattr(self.core, "get_signals"):
                return self.core.get_signals() or {}

            if hasattr(self.core, "tick"):
                self.core.tick()

            if hasattr(self.core, "signals"):
                return self.core.signals or {}
        except Exception:
            traceback.print_exc()

        return {}