from __future__ import annotations

# Load .env if python-dotenv is installed (optional).
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

from core.settings import Settings
from core.trading_core import Core


if __name__ == "__main__":
    print("step 1: start")

    settings = Settings.from_env()
    print("step 2: settings loaded")

    mode = (settings.mode or "telegram").strip().lower()
    print("step 3: mode =", mode)

    core = Core(settings=settings)
    print("step 4: core created")

    if mode == "telegram":
        print("step 5: telegram branch entered")

        from core.engine import TradingEngine
        print("step 6: TradingEngine imported")

        if not (settings.telegram_token or "").strip():
            if settings.telegram_use_dev_token:
                raise RuntimeError(
                    "TELEGRAM_DEV_TOKEN is required when TELEGRAM_USE_DEV_TOKEN=1"
                )
            raise RuntimeError(
                "TELEGRAM_TOKEN is required for telegram mode"
            )

        print("step 7: token check passed")

        engine = TradingEngine(core)
        print("step 8: engine created")

        from bot.telegram_bot import TelegramBot
        print("step 9: TelegramBot imported")

        bot = TelegramBot(
            settings.telegram_token,
            core,
            engine=engine,
            settings=settings,
        )
        print("step 10: bot created")

        bot.run()

    else:
        import json

        print("step cli: before get_signals")
        print(json.dumps(core.get_signals(), ensure_ascii=False, indent=2, default=str))