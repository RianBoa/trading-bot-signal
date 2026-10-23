from core.events import SignalEvent


class TelegramEmitter:
    def __init__(self, bot):
        self.bot = bot

    def emit(self, event):
        if isinstance(event, SignalEvent):
            try:
                self.bot.send_signal_event(event)
            except Exception as e:
                print(f"[TELEGRAM_EMITTER_ERROR] {e}")