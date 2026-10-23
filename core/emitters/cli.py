from __future__ import annotations

from core.emitters.base import EventEmitter
from core.events import BaseEvent, SignalEvent


class CliEmitter(EventEmitter):
    def emit(self, event: BaseEvent) -> None:
        if isinstance(event, SignalEvent):
            print(
                f"[SIGNAL] {event.symbol} {event.side} "
                f"entry={event.entry} sl={event.stop_loss} tp={event.take_profit}"
            )
        else:
            print(f"[EVENT] {event.event_type}")