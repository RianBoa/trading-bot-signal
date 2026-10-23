# core/fractals.py

from typing import List, Dict, Literal, Optional
import pandas as pd

Side = Literal["LONG", "SHORT"]


def detect_williams_fractals(df: pd.DataFrame) -> List[Dict]:
    """
    Williams Fractals (2-2):

    high-фрактал: high[i] > high[i-1], high[i-2], high[i+1], high[i+2]
    low-фрактал:  low[i]  < low[i-1], low[i-2], low[i+1], low[i+2]

    Возвращает список:
    [{"index": i, "type": "high"/"low", "price": float}, ...]
    """
    fractals: List[Dict] = []

    if len(df) < 5:
        return fractals

    highs = df["high"].values
    lows = df["low"].values

    for i in range(2, len(df) - 2):
        hi = highs[i]
        lo = lows[i]

        # high fractal
        if (
            hi > highs[i - 1] and hi > highs[i - 2] and
            hi > highs[i + 1] and hi > highs[i + 2]
        ):
            fractals.append({"index": i, "type": "high", "price": float(hi)})

        # low fractal
        if (
            lo < lows[i - 1] and lo < lows[i - 2] and
            lo < lows[i + 1] and lo < lows[i + 2]
        ):
            fractals.append({"index": i, "type": "low", "price": float(lo)})

    return fractals


def find_stop_by_second_fractal(fractals: List[Dict], side: Side) -> Optional[float]:
    """
    Берём второй противоположный фрактал от конца.
    LONG  -> стоп по high-фракталам
    SHORT -> стоп по low-фракталам
    """
    if side == "LONG":
        opp = [f for f in fractals if f["type"] == "high"]
    else:
        opp = [f for f in fractals if f["type"] == "low"]

    if not opp:
        return None

    # сортировка от последнего к первому по индексу
    opp_sorted = sorted(opp, key=lambda x: x["index"], reverse=True)

    if len(opp_sorted) >= 2:
        # второй от текущего
        return float(opp_sorted[1]["price"])

    # если только один – fallback: он же
    return float(opp_sorted[0]["price"])
