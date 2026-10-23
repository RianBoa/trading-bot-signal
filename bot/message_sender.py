from __future__ import annotations

from typing import Optional

from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes


class TelegramMessageSender:
    async def safe_send(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        allow_reply_fallback: bool = True,
        **kwargs,
    ):
        try:
            if kwargs.get("reply_to_message_id") is None:
                kwargs.pop("reply_to_message_id", None)
            return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter as e:
            try:
                await _sleep(float(getattr(e, "retry_after", 1.0) or 1.0))
                if kwargs.get("reply_to_message_id") is None:
                    kwargs.pop("reply_to_message_id", None)
                return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
            except Exception as e2:
                print(f"[TG] send retryafter failed chat_id={chat_id}: {type(e2).__name__}: {e2}")
                return None
        except (TimedOut, NetworkError) as e:
            try:
                await _sleep(1.0)
                if kwargs.get("reply_to_message_id") is None:
                    kwargs.pop("reply_to_message_id", None)
                return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
            except Exception as e2:
                print(f"[TG] send network retry failed chat_id={chat_id}: {type(e2).__name__}: {e2}")
                return None
        except Exception as e:
            if "reply_to_message_id" in kwargs:
                if not allow_reply_fallback:
                    print(
                        f"[TG] send reply_to failed chat_id={chat_id}: "
                        f"{type(e).__name__}: {e}"
                    )
                    return None
                try:
                    kwargs.pop("reply_to_message_id", None)
                    return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                except Exception as e2:
                    print(f"[TG] send exception chat_id={chat_id}: {type(e2).__name__}: {e2}")
                    return None
            print(f"[TG] send exception chat_id={chat_id}: {type(e).__name__}: {e}")
            return None


async def _sleep(sec: float) -> None:
    import asyncio as _asyncio

    await _asyncio.sleep(sec)
