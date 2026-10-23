from __future__ import annotations

import argparse
import asyncio
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The simulator does not talk to Telegram. Some production modules import
# ContextTypes only for type hints, so provide a tiny stub when the package is
# not installed in the current test interpreter.
if "telegram.ext" not in sys.modules:
    telegram_mod = types.ModuleType("telegram")
    telegram_ext_mod = types.ModuleType("telegram.ext")

    class _ContextTypes:
        DEFAULT_TYPE = object

    class _Update:
        pass

    telegram_mod.Update = _Update
    telegram_ext_mod.ContextTypes = _ContextTypes
    telegram_mod.ext = telegram_ext_mod
    sys.modules.setdefault("telegram", telegram_mod)
    sys.modules.setdefault("telegram.ext", telegram_ext_mod)

from bot.anti_revenge import AntiRevengeGuard
from bot.command_handlers import TelegramCommandHandlers
from bot.emission_policy import EmissionPolicy
from bot.job_runner import TelegramJobRunner
from bot.signal_state import SignalStateMachine
from bot.signal_validator import SignalValidator
from bot.signal_formatter import SignalFormatter
from bot.state_store import BotStateStore
from bot.thread_registry import ThreadRegistry
from core.engine import TradingEngine


SignalMap = Dict[str, dict]


@dataclass
class SentMessage:
    message_id: int
    chat_id: int
    text: str
    reply_to_message_id: int | None


class FakeCore:
    def __init__(self, frames: Iterable[SignalMap]):
        self.frames = list(frames)
        self.index = 0

    def get_signals(self) -> SignalMap:
        if not self.frames:
            return {}
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame


class FakeSender:
    def __init__(self):
        self.sent: List[SentMessage] = []
        self._next_message_id = 1000

    async def safe_send(
        self,
        context: Any,
        chat_id: int,
        text: str,
        **kwargs: Any,
    ) -> SentMessage:
        self._next_message_id += 1
        msg = SentMessage(
            message_id=self._next_message_id,
            chat_id=int(chat_id),
            text=text,
            reply_to_message_id=kwargs.get("reply_to_message_id"),
        )
        self.sent.append(msg)
        return msg


def enter(
    *,
    side: str = "LONG",
    tf: str = "5m",
    entry: Any = 100.0,
    stop: Any = 95.0,
    tp: Any = 110.0,
    symbol: str | None = None,
) -> dict:
    sig = {
        "signal": "ENTER",
        "side": side,
        "tf": tf,
        "entry_price": entry,
        "stop_price": stop,
        "tp_price": tp,
    }
    if symbol is not None:
        sig["symbol"] = symbol
    return sig


def exit_sl(*, side: str = "LONG", exit_price: Any = 95.0) -> dict:
    return {
        "signal": "EXIT_SL",
        "side": side,
        "exit_price": exit_price,
    }


def exit_sl_checked(
    *,
    side: str = "LONG",
    exit_price: Any = 95.0,
    stop_price: Any = 95.0,
    trigger_high: Any = 100.0,
    trigger_low: Any = 94.0,
) -> dict:
    sig = exit_sl(side=side, exit_price=exit_price)
    sig.update(
        {
            "stop_price": stop_price,
            "trigger_high": trigger_high,
            "trigger_low": trigger_low,
        }
    )
    return sig


def tp_hit(
    *,
    side: str = "LONG",
    tp_index: int = 1,
    tp_price: Any = 110.0,
    exit_price: Any = 110.0,
) -> dict:
    return {
        "signal": "TP_HIT",
        "side": side,
        "tp_index": tp_index,
        "tp_price": tp_price,
        "exit_price": exit_price,
    }


def tp_hit_checked(
    *,
    side: str = "LONG",
    tp_index: int = 1,
    tp_price: Any = 110.0,
    exit_price: Any = 110.0,
    trigger_high: Any = 111.0,
    trigger_low: Any = 99.0,
) -> dict:
    sig = tp_hit(
        side=side,
        tp_index=tp_index,
        tp_price=tp_price,
        exit_price=exit_price,
    )
    sig.update(
        {
            "trigger_high": trigger_high,
            "trigger_low": trigger_low,
        }
    )
    return sig


def exit_tp(*, side: str = "LONG", exit_price: Any = 120.0) -> dict:
    return {
        "signal": "EXIT_TP",
        "side": side,
        "exit_price": exit_price,
    }


def exit_tp_checked(
    *,
    side: str = "LONG",
    exit_price: Any = 120.0,
    tp_price: Any = 120.0,
    trigger_high: Any = 121.0,
    trigger_low: Any = 99.0,
) -> dict:
    sig = exit_tp(side=side, exit_price=exit_price)
    sig.update(
        {
            "tp_price": tp_price,
            "trigger_high": trigger_high,
            "trigger_low": trigger_low,
        }
    )
    return sig


def make_runner(
    engine: TradingEngine,
    sender: FakeSender,
    *,
    targets: List[int],
    state_path: Path,
    enter_cooldown_sec: int,
    exit_cooldown_sec: int,
) -> TelegramJobRunner:
    store = BotStateStore(state_path)
    registry = ThreadRegistry(store)
    registry.load()
    return TelegramJobRunner(
        engine=engine,
        sender=sender,
        formatter=SignalFormatter(),
        thread_registry=registry,
        emission_policy=EmissionPolicy(
            enter_cooldown_sec=enter_cooldown_sec,
            exit_cooldown_sec=exit_cooldown_sec,
        ),
        anti_revenge=AntiRevengeGuard(),
        targets_provider=lambda: list(targets),
        telegram_states={"ENTER", "TP_HIT", "EXIT_SL", "EXIT_TP"},
    )


async def run_frames(
    frames: Iterable[SignalMap],
    *,
    targets: List[int] | None = None,
    ticks: int | None = None,
    state_path: Path,
    enter_cooldown_sec: int = 300,
    exit_cooldown_sec: int = 60,
) -> Tuple[TradingEngine, FakeSender]:
    sender = FakeSender()
    core = FakeCore(frames)
    engine = TradingEngine(core)
    runner = make_runner(
        engine,
        sender,
        targets=targets or [111],
        state_path=state_path,
        enter_cooldown_sec=enter_cooldown_sec,
        exit_cooldown_sec=exit_cooldown_sec,
    )

    frame_count = ticks if ticks is not None else len(core.frames)
    for _ in range(frame_count):
        await runner.engine_tick(None)
        await runner.auto_post(None)

    return engine, sender


async def scenario_same_enter(tmp_dir: Path) -> str:
    state_path = tmp_dir / "same_enter.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": enter()},
        {"BTCUSDT": enter()},
        {"BTCUSDT": enter()},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "same_enter_same_process",
        len(sender.sent),
        expected=1,
        note="Identical ENTER in one process should be emitted once.",
    )


async def scenario_symbol_alias(tmp_dir: Path) -> str:
    state_path = tmp_dir / "symbol_alias.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": enter(symbol="BTCUSDT")},
        {"BYBIT:BTCUSDT": enter(symbol="BTCUSDT")},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "same_enter_symbol_alias",
        len(sender.sent),
        expected=1,
        note=(
            "Same trade under different raw symbol keys currently passes as a new event."
        ),
    )


async def scenario_restart_memory_loss(tmp_dir: Path) -> str:
    state_path = tmp_dir / "restart.json"
    state_path.unlink(missing_ok=True)
    frame = [{"ETHUSDT": enter(entry=2000.0, stop=1900.0, tp=2200.0)}]
    _, first = await run_frames(frame, state_path=state_path)
    _, second = await run_frames(frame, state_path=state_path)
    total = len(first.sent) + len(second.sent)
    return verdict(
        "same_enter_after_restart",
        total,
        expected=1,
        note="Engine and emission fingerprints are in memory, so restart can re-emit.",
    )


async def scenario_duplicate_targets(tmp_dir: Path) -> str:
    state_path = tmp_dir / "duplicate_targets.json"
    state_path.unlink(missing_ok=True)
    frames = [{"SOLUSDT": enter(entry=10.0, stop=9.0, tp=12.0)}]
    _, sender = await run_frames(
        frames,
        targets=[111, 111],
        state_path=state_path,
    )
    return verdict(
        "duplicate_targets",
        len(sender.sent),
        expected=1,
        note="Job runner sends once per target entry; target provider must dedup.",
    )


async def scenario_exit_then_enter_block(tmp_dir: Path) -> str:
    state_path = tmp_dir / "exit_then_enter.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"XRPUSDT": exit_sl(exit_price=0.5)},
        {"XRPUSDT": enter(entry=0.6, stop=0.5, tp=0.7)},
    ]
    _, sender = await run_frames(
        frames,
        state_path=state_path,
        enter_cooldown_sec=300,
    )
    return verdict(
        "enter_after_exit_sl",
        len(sender.sent),
        expected=1,
        note="EXIT_SL should emit, immediate ENTER should be blocked by anti-revenge.",
    )


async def scenario_same_tp_hit(tmp_dir: Path) -> str:
    state_path = tmp_dir / "same_tp_hit.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "same_tp_hit_same_process",
        len(sender.sent),
        expected=1,
        note="Identical TP_HIT should be emitted once.",
    )


async def scenario_next_tp_hit_allowed(tmp_dir: Path) -> str:
    state_path = tmp_dir / "next_tp_hit_allowed.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
        {"BTCUSDT": tp_hit(tp_index=2, tp_price=115.0, exit_price=115.0)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "next_tp_hit_allowed",
        len(sender.sent),
        expected=2,
        note="TP1 duplicate should be skipped, TP2 should still be emitted.",
    )


async def scenario_tp_hit_price_variant_blocked(tmp_dir: Path) -> str:
    state_path = tmp_dir / "tp_hit_price_variant_blocked.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)},
        {"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.01)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "tp_hit_price_variant_blocked",
        len(sender.sent),
        expected=1,
        note="Repeated TP1 with a slightly changed exit price should be blocked by state.",
    )


async def scenario_tp_hit_variant_after_restart_blocked(tmp_dir: Path) -> str:
    state_path = tmp_dir / "tp_hit_variant_after_restart_blocked.json"
    state_path.unlink(missing_ok=True)
    await run_frames(
        [{"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.0)}],
        state_path=state_path,
    )
    _, second = await run_frames(
        [{"BTCUSDT": tp_hit(tp_index=1, tp_price=110.0, exit_price=110.01)}],
        state_path=state_path,
    )
    return verdict(
        "tp_hit_variant_after_restart_blocked",
        len(second.sent),
        expected=0,
        note="Persisted state should block repeated TP1 after restart even with changed price.",
    )


async def scenario_same_exit_tp(tmp_dir: Path) -> str:
    state_path = tmp_dir / "same_exit_tp.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": exit_tp(exit_price=120.0)},
        {"BTCUSDT": exit_tp(exit_price=120.0)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "same_exit_tp_same_process",
        len(sender.sent),
        expected=1,
        note="Identical EXIT_TP should be emitted once.",
    )


async def scenario_same_exit_sl(tmp_dir: Path) -> str:
    state_path = tmp_dir / "same_exit_sl.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": exit_sl(exit_price=95.0)},
        {"BTCUSDT": exit_sl(exit_price=95.0)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "same_exit_sl_same_process",
        len(sender.sent),
        expected=1,
        note="Identical EXIT_SL should be emitted once.",
    )


async def scenario_state_machine_enter_duplicate(tmp_dir: Path) -> str:
    sm = SignalStateMachine()
    decisions = [
        sm.decide("BTCUSDT", enter(), 1000),
        sm.decide("BTCUSDT", enter(), 1001),
    ]
    return verdict_bool(
        "state_machine_enter_duplicate",
        [d.emit for d in decisions] == [True, False]
        and decisions[1].reason == "duplicate_enter",
        note="State machine should allow first ENTER and reject identical ENTER.",
    )


async def scenario_state_machine_tp_progression(tmp_dir: Path) -> str:
    sm = SignalStateMachine()
    decisions = [
        sm.decide("BTCUSDT", enter(), 1000),
        sm.decide("BTCUSDT", tp_hit(tp_index=1, tp_price=110.0), 1001),
        sm.decide("BTCUSDT", tp_hit(tp_index=1, tp_price=110.0), 1002),
        sm.decide("BTCUSDT", tp_hit(tp_index=2, tp_price=115.0, exit_price=115.0), 1003),
    ]
    return verdict_bool(
        "state_machine_tp_progression",
        [d.emit for d in decisions] == [True, True, False, True]
        and decisions[2].reason == "duplicate_tp_hit",
        note="State machine should block repeated TP1 and allow TP2.",
    )


async def scenario_state_machine_exit_duplicate(tmp_dir: Path) -> str:
    sm = SignalStateMachine()
    decisions = [
        sm.decide("BTCUSDT", enter(), 1000),
        sm.decide("BTCUSDT", exit_tp(exit_price=120.0), 1001),
        sm.decide("BTCUSDT", exit_tp(exit_price=120.0), 1002),
    ]
    return verdict_bool(
        "state_machine_exit_duplicate",
        [d.emit for d in decisions] == [True, True, False]
        and decisions[2].reason == "duplicate_exit_tp",
        note="State machine should reject identical close event.",
    )


async def scenario_state_machine_sl_cooldown(tmp_dir: Path) -> str:
    sm = SignalStateMachine(cooldown_after_sl_sec=900)
    decisions = [
        sm.decide("BTCUSDT", enter(), 1000),
        sm.decide("BTCUSDT", exit_sl(exit_price=95.0), 1010),
        sm.decide("BTCUSDT", enter(entry=101.0, stop=96.0, tp=112.0), 1020),
        sm.decide("BTCUSDT", enter(entry=101.0, stop=96.0, tp=112.0), 2000),
    ]
    return verdict_bool(
        "state_machine_sl_cooldown",
        [d.emit for d in decisions] == [True, True, False, True]
        and decisions[2].reason == "blocked_cooldown",
        note="State machine should block ENTER during SL cooldown and allow after it.",
    )


class FakeEffectiveMessage:
    def __init__(self):
        self.replies: List[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self):
        self.effective_message = FakeEffectiveMessage()


class FakeSettings:
    poll_interval_sec = 60


async def scenario_state_command_snapshot(tmp_dir: Path) -> str:
    sm = SignalStateMachine()
    sm.decide("BTCUSDT", enter(), 1000)
    sm.decide("BTCUSDT", tp_hit(tp_index=1, tp_price=110.0), 1001)

    handler = TelegramCommandHandlers(
        settings=FakeSettings(),
        core=None,
        engine=None,
        signal_reader=None,
        thread_registry=None,
        targets_provider=lambda: [],
        signal_state_machine=sm,
    )
    update = FakeUpdate()
    await handler.state(update, None)
    text = "\n".join(update.effective_message.replies)
    return verdict_bool(
        "state_command_snapshot",
        "BTCUSDT" in text
        and "state: TP_HIT" in text
        and "max_tp_index: 1" in text,
        note="/state should render current signal-state snapshot.",
    )


async def scenario_validator_blocks_premature_sl(tmp_dir: Path) -> str:
    state_path = tmp_dir / "validator_blocks_premature_sl.json"
    state_path.unlink(missing_ok=True)
    _, sender = await run_frames(
        [{"BTCUSDT": exit_sl_checked(stop_price=95.0, trigger_low=96.0)}],
        state_path=state_path,
    )
    return verdict(
        "validator_blocks_premature_sl",
        len(sender.sent),
        expected=0,
        note="EXIT_SL should be blocked when LONG candle low did not reach stop.",
    )


async def scenario_validator_blocks_premature_tp(tmp_dir: Path) -> str:
    state_path = tmp_dir / "validator_blocks_premature_tp.json"
    state_path.unlink(missing_ok=True)
    _, sender = await run_frames(
        [{"BTCUSDT": tp_hit_checked(tp_price=110.0, trigger_high=109.9)}],
        state_path=state_path,
    )
    return verdict(
        "validator_blocks_premature_tp",
        len(sender.sent),
        expected=0,
        note="TP_HIT should be blocked when LONG candle high did not reach TP.",
    )


async def scenario_validator_allows_confirmed_close(tmp_dir: Path) -> str:
    state_path = tmp_dir / "validator_allows_confirmed_close.json"
    state_path.unlink(missing_ok=True)
    frames = [
        {"BTCUSDT": tp_hit_checked(tp_index=1, tp_price=110.0, trigger_high=110.0)},
        {"ETHUSDT": exit_tp_checked(tp_price=120.0, trigger_high=121.0)},
        {"XRPUSDT": exit_sl_checked(stop_price=0.5, exit_price=0.5, trigger_low=0.49)},
    ]
    _, sender = await run_frames(frames, state_path=state_path)
    return verdict(
        "validator_allows_confirmed_close",
        len(sender.sent),
        expected=3,
        note="Validator should allow TP/SL when trigger high/low confirms threshold.",
    )


async def scenario_validator_unit_short_side(tmp_dir: Path) -> str:
    validator = SignalValidator()
    valid_sl = validator.validate(
        exit_sl_checked(
            side="SHORT",
            stop_price=105.0,
            exit_price=105.0,
            trigger_high=105.1,
            trigger_low=99.0,
        )
    )
    invalid_tp = validator.validate(
        tp_hit_checked(
            side="SHORT",
            tp_price=90.0,
            exit_price=90.0,
            trigger_high=101.0,
            trigger_low=90.1,
        )
    )
    return verdict_bool(
        "validator_unit_short_side",
        valid_sl.valid and not invalid_tp.valid and invalid_tp.reason == "tp_not_reached",
        note="Validator should use high for SHORT SL and low for SHORT TP.",
    )


def verdict(name: str, actual: int, *, expected: int, note: str) -> str:
    status = "PASS" if actual == expected else "FAIL"
    return f"{status} {name}: sent={actual}, expected={expected}. {note}"


def verdict_bool(name: str, ok: bool, *, note: str) -> str:
    status = "PASS" if ok else "FAIL"
    return f"{status} {name}: {note}"


SCENARIOS: Dict[str, Callable[[Path], Any]] = {
    "same-enter": scenario_same_enter,
    "symbol-alias": scenario_symbol_alias,
    "restart": scenario_restart_memory_loss,
    "duplicate-targets": scenario_duplicate_targets,
    "exit-enter": scenario_exit_then_enter_block,
    "same-tp-hit": scenario_same_tp_hit,
    "next-tp-hit": scenario_next_tp_hit_allowed,
    "tp-hit-price-variant": scenario_tp_hit_price_variant_blocked,
    "tp-hit-restart-variant": scenario_tp_hit_variant_after_restart_blocked,
    "same-exit-tp": scenario_same_exit_tp,
    "same-exit-sl": scenario_same_exit_sl,
    "sm-enter-duplicate": scenario_state_machine_enter_duplicate,
    "sm-tp-progression": scenario_state_machine_tp_progression,
    "sm-exit-duplicate": scenario_state_machine_exit_duplicate,
    "sm-sl-cooldown": scenario_state_machine_sl_cooldown,
    "state-command": scenario_state_command_snapshot,
    "validator-premature-sl": scenario_validator_blocks_premature_sl,
    "validator-premature-tp": scenario_validator_blocks_premature_tp,
    "validator-confirmed-close": scenario_validator_allows_confirmed_close,
    "validator-short-side": scenario_validator_unit_short_side,
}


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run fake-signal duplicate diagnostics without Telegram or exchange."
    )
    parser.add_argument(
        "--scenario",
        choices=["all", *SCENARIOS.keys()],
        default="all",
    )
    parser.add_argument(
        "--tmp-dir",
        default=str(ROOT / "cache" / "duplicate_sim"),
    )
    args = parser.parse_args()

    tmp_dir = Path(args.tmp_dir) / uuid.uuid4().hex
    tmp_dir.mkdir(parents=True, exist_ok=True)

    selected = (
        SCENARIOS.items()
        if args.scenario == "all"
        else [(args.scenario, SCENARIOS[args.scenario])]
    )

    failed = 0
    for _, fn in selected:
        line = await fn(tmp_dir)
        print(line)
        if line.startswith("FAIL "):
            failed += 1

    if failed:
        print(f"\nPotential duplicate path found: {failed} scenario(s) failed.")
        return 1

    print("\nNo duplicate paths reproduced by selected scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
