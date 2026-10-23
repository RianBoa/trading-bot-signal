from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bot.signal_formatter import chunk_text
from telegram import Update
from telegram.ext import ContextTypes


class TelegramCommandHandlers:
    def __init__(
        self,
        *,
        settings: Any,
        core: Any,
        engine: Any,
        signal_reader: Any,
        thread_registry: Any,
        targets_provider: Any,
        signal_state_machine: Any = None,
    ):
        self.settings = settings
        self.core = core
        self.engine = engine
        self.signal_reader = signal_reader
        self.thread_registry = thread_registry
        self.targets_provider = targets_provider
        self.signal_state_machine = signal_state_machine

        self.poll_interval_sec = int(self.settings.poll_interval_sec)

    # =========================
    # /start
    # =========================
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("CHAT ID =", update.effective_chat.id)
        cid = int(update.effective_chat.id)
        chat_type = (update.effective_chat.type or "").lower()

        if chat_type != "private":
            await update.effective_message.reply_text("Напиши /start мне в личку 🙂")
            return

        self.thread_registry.set_private_chat_id(cid)
        self.thread_registry.save()

        broadcast_ids = self.targets_provider()

        await update.effective_message.reply_text(
            "✅ BOT STARTED\n"
            f"Interval: {self.poll_interval_sec}s\n"
            f"Broadcast IDs: {', '.join(map(str, broadcast_ids)) if broadcast_ids else '(не задано)'}\n"
            "Команды: /signal, /state, /broadcasttest"
        )

    # =========================
    # /signal
    # =========================
    async def signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        signals = await self.signal_reader.get_signals_safe()

        universe: List[str] = []
        try:
            if self.engine is not None and hasattr(self.engine, "get_symbols"):
                universe = self.engine.get_symbols() or []
            else:
                sc = getattr(self.core, "scanner", None)
                if sc is not None and hasattr(sc, "scan"):
                    universe = sc.scan() or []
        except Exception:
            universe = []

        sym_set = set()
        for x in universe:
            if x:
                sym_set.add(str(x).strip().upper())
        if isinstance(signals, dict):
            for x in signals.keys():
                if x:
                    sym_set.add(str(x).strip().upper())

        symbols = sorted(sym_set)

        header = ["📊 Текущие сигналы"]
        header.append(f"Universe: {len(universe)}" if universe else "Universe: (unknown)")
        text_parts = ["\n".join(header), ""]

        def _pick(sig: dict, *keys: str, default: str = "") -> Any:
            for k in keys:
                v = sig.get(k)
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
                return v
            return default

        shown = 0

        for sym in symbols:
            sig = signals.get(sym) if isinstance(signals, dict) else None

            state: Optional[str] = None
            vc = ""

            if isinstance(sig, dict):
                state = str(_pick(sig, "signal", "state", "status", default="")).upper() or None
                vc = str(_pick(sig, "vc", "vc_reason", "reason", "message", "comment", default="") or "")
            else:
                for attr in (
                    "symbol_states",
                    "states",
                    "state_by_symbol",
                    "status_by_symbol",
                    "last_state_by_symbol",
                ):
                    mp = getattr(self.core, attr, None)
                    if isinstance(mp, dict) and sym in mp:
                        state = str(mp.get(sym)).upper()
                        break

            if not state:
                state = "WAIT_PHASE"

            lines = [sym, f"ℹ️ {state}"]
            if vc:
                lines.append(f"📦 VC: {vc}")

            text_parts.append("\n".join(lines))
            text_parts.append("")
            shown += 1

        if shown == 0:
            text_parts.append("(нет символов)")
            text_parts.append("")

        text = "\n".join(text_parts).strip() + "\n"

        for chunk in chunk_text(text):
            await update.effective_message.reply_text(chunk)

    # =========================
    # /broadcasttest
    # =========================
    async def broadcasttest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        targets = self.targets_provider()

        ok, fail = 0, 0

        for chat_id in targets:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"TEST: broadcast ok to {chat_id}",
                )
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[TG] broadcasttest failed chat_id={chat_id}: {type(e).__name__}: {e}")

        await update.effective_message.reply_text(
            f"Broadcast test done. OK={ok} FAIL={fail}\nTargets: {targets}"
        )

    # =========================
    # /state
    # =========================
    async def state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.signal_state_machine is None:
            await update.effective_message.reply_text("Signal state is not available.")
            return

        snapshot_fn = getattr(self.signal_state_machine, "snapshot", None)
        if not callable(snapshot_fn):
            await update.effective_message.reply_text("Signal state snapshot is not available.")
            return

        try:
            snapshot = snapshot_fn()
        except Exception as e:
            await update.effective_message.reply_text(
                f"Signal state read failed: {type(e).__name__}: {e}"
            )
            return

        if not snapshot:
            await update.effective_message.reply_text("Signal state is empty.")
            return

        lines = ["Signal state", ""]
        for symbol in sorted(snapshot.keys()):
            row = snapshot.get(symbol) or {}
            lines.extend(
                [
                    str(symbol),
                    f"state: {row.get('state') or 'WAIT'}",
                    f"last_signal: {row.get('last_signal') or '-'}",
                    f"max_tp_index: {row.get('max_tp_index') or 0}",
                    f"cooldown_until: {self._format_ts(row.get('cooldown_until'))}",
                    f"emitted_count: {row.get('emitted_count') or 0}",
                    "",
                ]
            )

        for chunk in chunk_text("\n".join(lines).strip() + "\n"):
            await update.effective_message.reply_text(chunk)

    def _format_ts(self, value: Any) -> str:
        try:
            ts = int(float(value or 0))
        except Exception:
            ts = 0
        if ts <= 0:
            return "-"
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
