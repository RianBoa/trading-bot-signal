from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from bot.signal_state import SignalStateMachine
from bot.signal_validator import SignalValidator
from core.trade_journal import TradeJournal
from telegram.ext import ContextTypes


class TelegramJobRunner:
    def __init__(
        self,
        *,
        engine: Any,
        sender: Any,
        formatter: Any,
        thread_registry: Any,
        emission_policy: Any,
        anti_revenge: Any,
        targets_provider: Any,
        telegram_states: set[str],
        persistent_dedup_ttl_sec: int | None = None,
        signal_state_machine: SignalStateMachine | None = None,
        signal_validator: SignalValidator | None = None,
        trade_journal: TradeJournal | None = None,
    ):
        self.engine = engine
        self.sender = sender
        self.formatter = formatter
        self.thread_registry = thread_registry
        self.emission_policy = emission_policy
        self.anti_revenge = anti_revenge
        self.targets_provider = targets_provider
        self.telegram_states = telegram_states
        self._auto_post_running = False
        self.signal_state_machine = signal_state_machine or SignalStateMachine(
            cooldown_after_sl_sec=15 * 60,
        )
        self.signal_validator = signal_validator or SignalValidator()
        self.trade_journal = trade_journal
        self._load_signal_state_machine()
        self.persistent_dedup_ttl_sec = int(
            persistent_dedup_ttl_sec
            if persistent_dedup_ttl_sec is not None
            else max(
                int(getattr(self.emission_policy, "enter_cooldown_sec", 300) or 300),
                int(getattr(self.emission_policy, "exit_cooldown_sec", 60) or 60),
            )
        )

    def _dedup_targets(self, targets: List[int]) -> List[int]:
        out: List[int] = []
        seen = set()
        for target in targets:
            try:
                chat_id = int(target)
            except Exception:
                continue
            if chat_id in seen:
                continue
            seen.add(chat_id)
            out.append(chat_id)
        return out

    def _load_recent_emissions(self) -> Dict[str, float]:
        store = getattr(self.thread_registry, "state_store", None)
        load_fn = getattr(store, "load_recent_emissions", None)
        if not callable(load_fn):
            return {}
        loaded = load_fn()
        return loaded if isinstance(loaded, dict) else {}

    def _save_recent_emissions(self, recent: Dict[str, float]) -> None:
        store = getattr(self.thread_registry, "state_store", None)
        save_fn = getattr(store, "save_recent_emissions", None)
        if callable(save_fn):
            save_fn(recent)

    def _load_signal_state_machine(self) -> None:
        store = getattr(self.thread_registry, "state_store", None)
        load_fn = getattr(store, "load_signal_states", None)
        if not callable(load_fn):
            return
        try:
            self.signal_state_machine.load_dict(load_fn())
        except Exception:
            pass

    def _save_signal_state_machine(self) -> None:
        store = getattr(self.thread_registry, "state_store", None)
        save_fn = getattr(store, "save_signal_states", None)
        if not callable(save_fn):
            return
        try:
            save_fn(self.signal_state_machine.to_dict())
        except Exception:
            pass

    def _was_recently_emitted(self, fingerprint: str, now: int) -> bool:
        ttl = max(0, int(self.persistent_dedup_ttl_sec))
        if ttl <= 0:
            return False

        recent = self._load_recent_emissions()
        cutoff = now - ttl
        recent = {
            str(fp): float(ts)
            for fp, ts in recent.items()
            if str(fp) and float(ts) >= cutoff
        }

        last_ts = float(recent.get(fingerprint, 0) or 0)
        if last_ts >= cutoff:
            self._save_recent_emissions(recent)
            return True

        recent[fingerprint] = float(now)
        self._save_recent_emissions(recent)
        return False

    def _log_decision(
        self,
        *,
        symbol: str,
        state: str,
        emit: bool,
        layer: str,
        reason: str,
        fingerprint: str = "",
        targets: List[int] | None = None,
        machine_state: str = "",
    ) -> None:
        parts = [
            "[BOT][DECISION]",
            f"sym={symbol}",
            f"state={state}",
            f"emit={str(bool(emit)).lower()}",
            f"layer={layer}",
            f"reason={reason}",
        ]
        if machine_state:
            parts.append(f"machine_state={machine_state}")
        if fingerprint:
            parts.append(f"fp={fingerprint}")
        if targets is not None:
            parts.append(f"targets={targets}")
        print(" ".join(parts))

    async def engine_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.engine is None:
            return
        try:
            if hasattr(self.engine, "tick"):
                await self.engine.tick()
        except Exception as e:
            import traceback
            traceback.print_exc()

    async def auto_post(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._auto_post_running:
            return
        self._auto_post_running = True
        try:
            targets = self._dedup_targets(self.targets_provider())
            if not targets:
                return

            if self.engine is None or not hasattr(self.engine, "drain_events"):
                return

            events: List[Tuple[str, dict]] = await self.engine.drain_events()
            if not events:
                return

            for symbol_raw, sig in events:
                if not isinstance(sig, dict):
                    continue

                symbol = str(symbol_raw)
                state = (sig.get("signal") or "").upper()
                if state not in self.telegram_states:
                    continue

                msg_text = self.formatter.format_signal(symbol, sig)
                if not msg_text:
                    continue

                keys = self.thread_registry.keys_for_threading(symbol, sig, msg_text)
                sym0 = (
                    keys[0]
                    if keys
                    else self.thread_registry.norm_symbol(symbol) or symbol
                ).upper()

                now = int(time.time())
                aliases = list(keys)
                if sym0 not in aliases:
                    aliases.insert(0, sym0)

                fp = self.emission_policy.fingerprint(sym0, sig)
                if not self.emission_policy.should_emit(sym0, sig):
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="emission_policy",
                        reason="cooldown_or_same_fingerprint",
                        fingerprint=fp,
                        targets=targets,
                    )
                    continue

                if not self.anti_revenge.allow(state, sym0, aliases, now):
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="anti_revenge",
                        reason="blocked",
                        fingerprint=fp,
                        targets=targets,
                    )
                    continue

                validation = self.signal_validator.validate(sig)
                if not validation.valid:
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="signal_validator",
                        reason=validation.reason,
                        fingerprint=fp,
                        targets=targets,
                    )
                    continue

                if self._was_recently_emitted(fp, now):
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="persistent_dedup",
                        reason="recent_fingerprint",
                        fingerprint=fp,
                        targets=targets,
                    )
                    continue

                decision = self.signal_state_machine.decide(sym0, sig, now)
                self._save_signal_state_machine()
                if not decision.emit:
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="signal_state",
                        reason=decision.reason,
                        fingerprint=fp,
                        targets=targets,
                        machine_state=decision.state,
                    )
                    continue

                self._log_decision(
                    symbol=sym0,
                    state=state,
                    emit=True,
                    layer="final",
                    reason=decision.reason,
                    fingerprint=fp,
                    targets=targets,
                    machine_state=decision.state,
                )

                enter_delivered = False
                journal_enter_written = False
                journal_exit_written = False

                for chat_id in targets:
                    reply_to = None
                    used = None

                    if state in ("TP_HIT", "EXIT_SL", "EXIT_TP"):
                        reply_to, used = self.thread_registry.get_reply_root(
                            int(chat_id),
                            keys,
                        )
                        print(f"[BOT] reply_to chat={chat_id} sym={used or sym0} -> {reply_to}")
                        if reply_to is None:
                            self._log_decision(
                                symbol=sym0,
                                state=state,
                                emit=False,
                                layer="thread_registry",
                                reason="missing_reply_root",
                                fingerprint=fp,
                                targets=[int(chat_id)],
                                machine_state=decision.state,
                            )
                            continue

                    msg = await self.sender.safe_send(
                        context,
                        int(chat_id),
                        msg_text,
                        allow_reply_fallback=state not in ("TP_HIT", "EXIT_SL", "EXIT_TP"),
                        reply_to_message_id=reply_to,
                    )

                    if msg is None:
                        self._log_decision(
                            symbol=sym0,
                            state=state,
                            emit=False,
                            layer="sender",
                            reason="delivery_failed",
                            fingerprint=fp,
                            targets=[int(chat_id)],
                            machine_state=self.signal_state_machine.get(sym0).state,
                        )
                        continue

                    if state == "ENTER" and msg is not None:
                        enter_delivered = True
                        self.thread_registry.set_reply_root(
                            int(chat_id),
                            keys,
                            int(msg.message_id),
                        )
                        try:
                            self.thread_registry.save()
                        except Exception:
                            pass
                        print(f"[BOT] root set chat={chat_id} sym={sym0} mid={msg.message_id}")
                        if self.trade_journal is not None and not journal_enter_written:
                            try:
                                self.trade_journal.ingest_signal(
                                    sym0,
                                    sig,
                                    telegram_chat_id=int(chat_id),
                                    telegram_message_id=int(msg.message_id),
                                )
                                journal_enter_written = True
                            except Exception:
                                pass

                    if state in ("EXIT_SL", "EXIT_TP"):
                        if self.trade_journal is not None and not journal_exit_written:
                            try:
                                self.trade_journal.ingest_signal(
                                    sym0,
                                    sig,
                                    telegram_chat_id=int(chat_id),
                                    telegram_message_id=int(msg.message_id),
                                )
                                journal_exit_written = True
                            except Exception:
                                pass
                        self.thread_registry.clear_reply_roots(
                            int(chat_id),
                            keys,
                        )
                        try:
                            self.thread_registry.save()
                        except Exception:
                            pass
                if state == "ENTER" and not enter_delivered:
                    self.signal_state_machine.mark_delivery_failed(
                        sym0,
                        sig,
                    )
                    self._save_signal_state_machine()
                    self._log_decision(
                        symbol=sym0,
                        state=state,
                        emit=False,
                        layer="sender",
                        reason="enter_delivery_failed_all_targets",
                        fingerprint=fp,
                        targets=targets,
                        machine_state=self.signal_state_machine.get(sym0).state,
                    )
        finally:
            self._auto_post_running = False
