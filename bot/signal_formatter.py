from __future__ import annotations

from typing import Any, List, Optional
from common.parsing import to_float

def fmt_price(x: Any) -> str:
    v = to_float(x)
    if v is None:
        return str(x)
    if abs(v) >= 1000:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if abs(v) >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".")


def chunk_text(text: str, size: int = 3800) -> List[str]:
    text = text or ""
    if len(text) <= size:
        return [text]
    out: List[str] = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out


class SignalFormatter:
    def format_signal(self, symbol: str, sig: dict) -> str:
        if not isinstance(sig, dict):
            return ""

        st = (sig.get("signal") or "UNKNOWN").upper()

        if st == "ENTER":
            side = (sig.get("side") or "").upper()
            entry = sig.get("entry_price")
            sl = sig.get("stop_price")

            icon = "🟢" if side == "LONG" else "🔴"
            lines = [
                "📢 Новый сигнал",
                f"{icon} {symbol} {side}".strip(),
                f"📍 Вход: {fmt_price(entry)}",
                f"🛑 SL: {fmt_price(sl)}",
            ]

            tps: List[float] = []
            tp_prices = sig.get("tp_prices")
            if isinstance(tp_prices, (list, tuple)):
                tps = [x for x in (to_float(v) for v in tp_prices) if x is not None]
            if not tps and sig.get("tp_price") is not None:
                v = to_float(sig.get("tp_price"))
                if v is not None:
                    tps = [v]

            for i, tp in enumerate(tps[:4], start=1):
                lines.append(f"🎯 TP{i}: {fmt_price(tp)}")

            lines.append("🧮 Риск на сделку 1% от депозита")
            return "\n".join(lines)

        if st == "TP_HIT":
            side = (sig.get("side") or "").upper()
            xp = sig.get("exit_price")
            tp_i = sig.get("tp_index")
            tp_p = sig.get("tp_price")
            hits = sig.get("tp_hits")

            lines = [
                "🎯 Реализован тейк-профит",
                f"✅ {symbol} {side}".strip(),
            ]

            be_warn = False
            if isinstance(hits, list) and hits:
                for h in hits:
                    if not isinstance(h, dict):
                        continue
                    i = h.get("tp_index")
                    p = h.get("tp_price")
                    if i is not None and p is not None:
                        lines.append(f"✅ TP{i}: {fmt_price(p)}")
                        try:
                            if int(i) == 2:
                                be_warn = True
                        except Exception:
                            pass
            else:
                if tp_i:
                    lines.append(f"🧾 TP{tp_i} выполнен")
                    try:
                        if int(tp_i) == 2:
                            be_warn = True
                    except Exception:
                        pass
                else:
                    lines.append("🧾 TP выполнен")

                if tp_p is not None:
                    lines.append(f"🎯 Цель: {fmt_price(tp_p)}")

            if be_warn:
                lines.append("⚠️ Переставить стоп в безубыток")

            if xp is not None:
                lines.append(f"💰 Цена: {fmt_price(xp)}")

            return "\n".join(lines)

        if st in ("EXIT_SL", "EXIT_TP"):
            side = (sig.get("side") or "").upper()
            xp = sig.get("exit_price")

            tp_hit = sig.get("tp_hit_count") or sig.get("tp_hit") or sig.get("hit") or sig.get("tp_index") or 0
            tp_total = sig.get("tp_total") or sig.get("tp_count") or sig.get("total") or sig.get("tp_n") or 0
            try:
                tp_hit_i = int(tp_hit) if tp_hit is not None else 0
            except Exception:
                tp_hit_i = 0
            try:
                tp_total_i = int(tp_total) if tp_total is not None else 0
            except Exception:
                tp_total_i = 0

            close_text = "Сделка закрыта по стоп-приказу" if st == "EXIT_SL" else "Сделка закрыта по тейк-профиту"
            if tp_hit_i:
                close_text += f" (взято TP: {tp_hit_i}/{tp_total_i})" if tp_total_i else f" (взято TP: {tp_hit_i})"

            icon = "❌" if st == "EXIT_SL" else "✅"
            lines = [
                "🏁 Результат сделки",
                f"{icon} {symbol} {side}".strip(),
                f"🧾 {close_text}",
            ]
            if xp is not None:
                lines.append(f"💰 Цена закрытия: {fmt_price(xp)}")
            return "\n".join(lines)

        # default: show state for monitoring (/signal)
        vc = sig.get("vc")
        why = sig.get("why")
        lines = [symbol, f"ℹ️ {st}"]
        if vc:
            lines.append(f"📦 VC: {vc}")
        if why and (not vc or str(why) not in str(vc)):
            lines.append(f"🧠 {why}")
        return "\n".join(lines)

