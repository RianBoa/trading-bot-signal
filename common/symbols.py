from __future__ import annotations

import re


ASCII_SYMBOL_RE = re.compile(r"^[A-Z0-9_]+$")


def _is_ascii_symbol(sym: str) -> bool:
    if not sym:
        return False
    if not sym.isascii():
        return False
    return bool(ASCII_SYMBOL_RE.match(sym))


def _normalize_symbol(sym: str) -> str:
    if sym == "PEPEUSDT":
        return "1000PEPEUSDT"
    return sym