from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(slots=True)
class BaseEvent:
    event_type: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class SignalEvent(BaseEvent):
    symbol: str = ""
    side: str = ""
    timeframe: str = ""
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    narrative: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        symbol: str,
        side: str,
        timeframe: str = "",
        entry: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        narrative: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(event_type="signal")
        self.symbol = symbol
        self.side = side
        self.timeframe = timeframe
        self.entry = entry
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.narrative = narrative
        self.payload = payload or {}