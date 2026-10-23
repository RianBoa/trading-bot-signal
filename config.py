import os


def _env_flag(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on", "dev")


# Production bot token (server / main instance)
_TELEGRAM_TOKEN_PROD = (os.getenv("TELEGRAM_TOKEN", "") or "").strip()
# Second bot for local tests — avoids Conflict with server polling the same token
_TELEGRAM_TOKEN_DEV = (os.getenv("TELEGRAM_DEV_TOKEN", "") or "").strip()

# TELEGRAM_USE_DEV_TOKEN=1  → use TELEGRAM_DEV_TOKEN (must be set)
TELEGRAM_USE_DEV_TOKEN = _env_flag("TELEGRAM_USE_DEV_TOKEN")
if TELEGRAM_USE_DEV_TOKEN:
    TELEGRAM_TOKEN = _TELEGRAM_TOKEN_DEV
else:
    TELEGRAM_TOKEN = _TELEGRAM_TOKEN_PROD
API_KEY = os.getenv("Bybit_API_KEY", "")
API_SECRET = os.getenv("Bybit_API_SECRET", "")
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "40000000"))
EXCHANGE = os.getenv("EXCHANGE", "bybit").lower()
EXCHANGE_TYPE = os.getenv("EXCHANGE_TYPE", "swap").lower()