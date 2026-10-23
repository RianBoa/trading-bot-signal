from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bot.state_store import BotStateStore
from core.trade_journal import TradeJournal


class ThreadRegistry:
    def __init__(
        self,
        state_store: BotStateStore,
        private_chat_id: Optional[int] = None,
        trade_journal: Optional[TradeJournal] = None,
    ):
        self.state_store = state_store
        self.private_chat_id = private_chat_id
        self.trade_journal = trade_journal
        self.reply_roots: Dict[Tuple[int, str], int] = {}

    def load(self) -> None:
        loaded_chat_id, loaded_roots = self.state_store.load()
        self.reply_roots.update(loaded_roots)
        if self.private_chat_id is None and loaded_chat_id is not None:
            self.private_chat_id = loaded_chat_id

    def save(self) -> None:
        self.state_store.save(
            chat_id=self.private_chat_id,
            reply_roots=self.reply_roots,
        )

    def set_private_chat_id(self, chat_id: int) -> None:
        self.private_chat_id = int(chat_id)

    def get_private_chat_id(self) -> Optional[int]:
        return self.private_chat_id

    def get_reply_root(self, chat_id: int, aliases: List[str]) -> Tuple[Optional[int], Optional[str]]:
        for alias in aliases:
            mid = self.reply_roots.get((int(chat_id), alias))
            if mid:
                return int(mid), alias
        if self.trade_journal is not None:
            found = self.trade_journal.find_open_thread_root(
                int(chat_id),
                aliases,
            )
            if found is not None:
                mid, alias = found
                self.set_reply_root(
                    int(chat_id),
                    [alias],
                    int(mid),
                )
                try:
                    self.save()
                except Exception:
                    pass
                return int(mid), alias
        return None, None

    def set_reply_root(self, chat_id: int, aliases: List[str], message_id: int) -> None:
        for alias in aliases:
            self.reply_roots[(int(chat_id), alias)] = int(message_id)

    def clear_reply_roots(self, chat_id: int, aliases: List[str]) -> None:
        for alias in aliases:
            self.reply_roots.pop((int(chat_id), alias), None)

    def norm_symbol(self, value: str) -> str:
        value = (value or "").upper().strip()
        value = value.split(":")[-1]
        value = re.sub(r"[^A-Z0-9]+", "", value)
        return value

    def keys_for_threading(self, symbol: str, sig: dict, msg_text: str) -> List[str]:
        keys: List[str] = []
        up = (msg_text or "").upper()

        m = re.search(r"\b([A-Z0-9]{2,}USDT)\b", up)
        if m:
            keys.append(self.norm_symbol(m.group(1)))

        keys.append(self.norm_symbol(symbol))

        for key in ("symbol", "ticker", "pair", "instrument"):
            value = sig.get(key)
            if value:
                keys.append(self.norm_symbol(str(value)))

        seen = set()
        out: List[str] = []
        for key in keys:
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)

        return out or [self.norm_symbol(symbol) or (symbol or "").upper()]
