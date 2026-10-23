from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, List, Optional

from bot.anti_revenge import AntiRevengeGuard
from bot.command_handlers import TelegramCommandHandlers
from bot.emission_policy import EmissionPolicy
from bot.job_runner import TelegramJobRunner
from bot.message_sender import TelegramMessageSender
from bot.signal_formatter import SignalFormatter
from bot.signal_reader import SignalReader
from bot.state_store import BotStateStore
from bot.thread_registry import ThreadRegistry
from core.settings import Settings
from core.trade_journal import TradeJournal
from telegram.ext import ApplicationBuilder, CommandHandler

try:
    from telegram.request import HTTPXRequest  # type: ignore
except Exception:
    HTTPXRequest = None  # type: ignore


class TelegramBot:
    def __init__(
        self,
        token: str,
        core: Any,
        *,
        engine: Any = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or Settings.from_env()
        self.token = (token or "").strip()
        self.core = core
        self.engine = engine

        # base services
        self.formatter = SignalFormatter()
        self.sender = TelegramMessageSender()
        self.signal_reader = SignalReader(core=self.core, engine=self.engine)

        # config
        self.poll_interval_sec = int(self.settings.poll_interval_sec)
        self.enter_cooldown_sec = int(self.settings.enter_cooldown_sec)
        self.exit_cooldown_sec = int(self.settings.exit_cooldown_sec)

        self.telegram_states = {
            x.strip().upper()
            for x in (self.settings.telegram_emit_states or [])
            if str(x).strip()
        }
        self.telegram_states.add("TP_HIT")

        # broadcast ids
        ids: List[int] = []
        ids += [int(x) for x in (self.settings.telegram_channel_chat_ids or [])]
        ids += [int(x) for x in (self.settings.telegram_group_chat_ids or [])]
        ids += [int(x) for x in (self.settings.telegram_extra_chat_ids or [])]
        self.broadcast_ids = sorted(list({int(x) for x in ids if str(x).strip()}))

        # state / threading
        initial_private_chat_id = self.settings.telegram_private_chat_id
        self.state_path = (
            Path(self.settings.bot_state_path)
            if self.settings.bot_state_path
            else Path("ai_data") / "bot_state.json"
        )
        self.state_store = BotStateStore(self.state_path)
        self.trade_journal = TradeJournal(
            db_path=str(
                self.state_path.parent
                / "trade_journal.sqlite3"
            ),
            csv_path=str(
                self.state_path.parent
                / "trade_journal.csv"
            ),
        )
        self.thread_registry = ThreadRegistry(
            state_store=self.state_store,
            private_chat_id=initial_private_chat_id,
            trade_journal=self.trade_journal,
        )
        self.thread_registry.load()

        # policies
        self.emission_policy = EmissionPolicy(
            enter_cooldown_sec=self.enter_cooldown_sec,
            exit_cooldown_sec=self.exit_cooldown_sec,
        )
        self.anti_revenge = AntiRevengeGuard()

        # jobs
        self.job_runner = TelegramJobRunner(
            engine=self.engine,
            sender=self.sender,
            formatter=self.formatter,
            thread_registry=self.thread_registry,
            emission_policy=self.emission_policy,
            anti_revenge=self.anti_revenge,
            targets_provider=self._targets,
            telegram_states=self.telegram_states,
            trade_journal=self.trade_journal,
        )

        # command handlers
        self.handlers = TelegramCommandHandlers(
            settings=self.settings,
            core=self.core,
            engine=self.engine,
            signal_reader=self.signal_reader,
            thread_registry=self.thread_registry,
            targets_provider=self._targets,
            signal_state_machine=self.job_runner.signal_state_machine,
        )

    def _targets(self) -> List[int]:
        out: List[int] = []

        for cid in self.broadcast_ids:
            if cid not in out:
                out.append(int(cid))

        private_chat_id = self.thread_registry.get_private_chat_id()
        if private_chat_id is not None and int(private_chat_id) not in out:
            out.append(int(private_chat_id))

        return out

    async def _on_error(
        self,
        update: object,
        context: Any,
    ) -> None:
        err = getattr(context, "error", None)
        if err is None:
            print("[BOT][ERROR] unknown")
            return

        print(
            f"[BOT][ERROR] type={type(err).__name__} "
            f"message={err}"
        )

        try:
            traceback.print_exception(
                type(err),
                err,
                err.__traceback__,
            )
        except Exception:
            pass

    def run(self) -> None:
        if not self.token:
            raise RuntimeError("TELEGRAM_TOKEN is empty")

        request = None
        if HTTPXRequest is not None:
            try:
                request = HTTPXRequest(
                    connect_timeout=float(self.settings.tg_connect_timeout),
                    read_timeout=float(self.settings.tg_read_timeout),
                    write_timeout=float(self.settings.tg_write_timeout),
                    pool_timeout=float(self.settings.tg_pool_timeout),
                )
            except Exception:
                request = None

        builder = ApplicationBuilder().token(self.token)
        if request is not None:
            builder = builder.request(request)

        app = builder.build()
        app.add_error_handler(self._on_error)

        # handlers
        app.add_handler(CommandHandler("start", self.handlers.start))
        app.add_handler(CommandHandler("signal", self.handlers.signal))
        app.add_handler(CommandHandler("state", self.handlers.state))
        app.add_handler(CommandHandler("broadcasttest", self.handlers.broadcasttest))

        # jobs
        if app.job_queue is not None:
            if self.engine is not None:
                app.job_queue.run_repeating(
                    self.job_runner.engine_tick,
                    interval=self.poll_interval_sec,
                    first=1,
                )

            app.job_queue.run_repeating(
                self.job_runner.auto_post,
                interval=self.poll_interval_sec,
                first=5,
            )

        print(
            f"[BOT] starting polling... interval={self.poll_interval_sec}s "
            f"chat_id={self.thread_registry.get_private_chat_id()} "
            f"broadcast_ids={self.broadcast_ids} "
            f"telegram_states={sorted(self.telegram_states)}"
        )

        app.run_polling(close_loop=False)
