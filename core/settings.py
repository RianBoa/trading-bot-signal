from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    v = raw.strip().lower()
    return v in ("1", "true", "yes", "y", "on", "dev")


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw.replace(",", "."))
    except Exception:
        return float(default)


def _parse_ids(raw: str) -> List[int]:
    ids: List[int] = []
    for part in (raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            ids.append(int(p))
        except Exception:
            continue
    # dedup while preserving order
    out: List[int] = []
    seen = set()
    for x in ids:
        if x in seen:
            continue
        seen.add(x)
        out.append(int(x))
    return out


@dataclass(frozen=True)
class Settings:
    # --- Runtime ---
    mode: str

    # --- Exchange ---
    exchange: str
    exchange_type: str
    api_key: str
    api_secret: str
    min_volume_24h: float

    # --- Symbols ---
    max_symbols: int
    symbol_ascii_only: bool

    # --- OHLCV cache ---
    ohlcv_cache_ttl_sec: float
    ohlcv_cache_ttl_by_tf: Dict[str, float]
    ohlcv_cache_metrics_every: int

    # --- Telegram token selection ---
    telegram_use_dev_token: bool
    telegram_token: str

    # --- Telegram behavior ---
    poll_interval_sec: int
    enter_cooldown_sec: int
    exit_cooldown_sec: int
    telegram_emit_states: List[str]
    telegram_channel_chat_ids: List[int]
    telegram_group_chat_ids: List[int]
    telegram_extra_chat_ids: List[int]
    telegram_private_chat_id: Optional[int]

    bot_base_dir: Path
    bot_state_path: Path

    tg_connect_timeout: float
    tg_read_timeout: float
    tg_write_timeout: float
    tg_pool_timeout: float

    @staticmethod
    def from_env() -> "Settings":
        mode = (_env_str("MODE", "telegram") or "telegram").lower()

        # Exchange
        exchange = (_env_str("EXCHANGE", "bybit") or "bybit").lower()
        exchange_type = (_env_str("EXCHANGE_TYPE", "swap") or "swap").lower()
        api_key = _env_str("Bybit_API_KEY", "")
        api_secret = _env_str("Bybit_API_SECRET", "")
        min_volume_24h = _env_float("MIN_VOLUME_24H", 40_000_000.0)

        # Symbols
        max_symbols = _env_int("MAX_SYMBOLS", 10)
        symbol_ascii_only = _env_flag("SYMBOL_ASCII_ONLY", False)

        # OHLCV cache
        ohlcv_cache_ttl_sec = _env_float("OHLCV_CACHE_TTL_SEC", 30.0)
        ohlcv_cache_ttl_by_tf = {
            "5m": _env_float("OHLCV_CACHE_TTL_5M_SEC", 20.0),
            "15m": _env_float("OHLCV_CACHE_TTL_15M_SEC", 30.0),
            "1h": _env_float("OHLCV_CACHE_TTL_1H_SEC", 60.0),
            "4h": _env_float("OHLCV_CACHE_TTL_4H_SEC", 120.0),
            "1d": _env_float("OHLCV_CACHE_TTL_1D_SEC", 300.0),
        }
        ohlcv_cache_metrics_every = _env_int("OHLCV_CACHE_METRICS_EVERY", 10)

        # Telegram token (prod vs dev)
        telegram_use_dev_token = _env_flag("TELEGRAM_USE_DEV_TOKEN", False)
        telegram_token_prod = _env_str("TELEGRAM_TOKEN", "")
        telegram_token_dev = _env_str("TELEGRAM_DEV_TOKEN", "")
        telegram_token = telegram_token_dev if telegram_use_dev_token else telegram_token_prod

        # Telegram behavior
        poll_interval_sec = _env_int("POLL_INTERVAL_SEC", 60)
        enter_cooldown_sec = _env_int("ENTER_COOLDOWN_SEC", 5 * 60)
        exit_cooldown_sec = _env_int("EXIT_COOLDOWN_SEC", 60)

        telegram_emit_states = [
            x.strip().upper()
            for x in _env_str("TELEGRAM_EMIT_STATES", "ENTER,TP_HIT,EXIT_SL,EXIT_TP").split(",")
            if x.strip()
        ]

        telegram_channel_chat_ids = _parse_ids(_env_str("TELEGRAM_CHANNEL_CHAT_ID", ""))
        telegram_group_chat_ids = _parse_ids(_env_str("TELEGRAM_GROUP_CHAT_ID", ""))
        telegram_extra_chat_ids = _parse_ids(_env_str("TELEGRAM_EXTRA_CHAT_IDS", ""))

        telegram_private_chat_id: Optional[int] = None
        env_private = _env_str("TELEGRAM_CHAT_ID", "")
        if env_private:
            try:
                telegram_private_chat_id = int(env_private)
            except Exception:
                telegram_private_chat_id = None

        bot_base_dir = Path(_env_str("BOT_BASE_DIR", "/home/bot/trading_bot"))
        bot_state_path = Path(_env_str("BOT_STATE_PATH", str(bot_base_dir / "ai_data" / "bot_state.json")))

        tg_connect_timeout = _env_float("TG_CONNECT_TIMEOUT", 10.0)
        tg_read_timeout = _env_float("TG_READ_TIMEOUT", 30.0)
        tg_write_timeout = _env_float("TG_WRITE_TIMEOUT", 30.0)
        tg_pool_timeout = _env_float("TG_POOL_TIMEOUT", 10.0)

        return Settings(
            mode=mode,
            exchange=exchange,
            exchange_type=exchange_type,
            api_key=api_key,
            api_secret=api_secret,
            min_volume_24h=min_volume_24h,
            max_symbols=max_symbols,
            symbol_ascii_only=symbol_ascii_only,
            ohlcv_cache_ttl_sec=ohlcv_cache_ttl_sec,
            ohlcv_cache_ttl_by_tf=ohlcv_cache_ttl_by_tf,
            ohlcv_cache_metrics_every=ohlcv_cache_metrics_every,
            telegram_use_dev_token=telegram_use_dev_token,
            telegram_token=telegram_token,
            poll_interval_sec=poll_interval_sec,
            enter_cooldown_sec=enter_cooldown_sec,
            exit_cooldown_sec=exit_cooldown_sec,
            telegram_emit_states=telegram_emit_states,
            telegram_channel_chat_ids=telegram_channel_chat_ids,
            telegram_group_chat_ids=telegram_group_chat_ids,
            telegram_extra_chat_ids=telegram_extra_chat_ids,
            telegram_private_chat_id=telegram_private_chat_id,
            bot_base_dir=bot_base_dir,
            bot_state_path=bot_state_path,
            tg_connect_timeout=tg_connect_timeout,
            tg_read_timeout=tg_read_timeout,
            tg_write_timeout=tg_write_timeout,
            tg_pool_timeout=tg_pool_timeout,
        )

