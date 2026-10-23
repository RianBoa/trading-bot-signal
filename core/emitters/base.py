from __future__ import annotations

from abc import ABC, abstractmethod

from core.events import BaseEvent


class EventEmitter(ABC):
    @abstractmethod
    def emit(self, event: BaseEvent) -> None:
        raise NotImplementedError