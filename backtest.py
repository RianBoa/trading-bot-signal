# backtest.py
import os
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal, Dict, List, Tuple

import requests
import pandas as pd
import numpy as np

from core.strategy_config import load_narrative_config
from core.strategy_narrative import NarrativeStrategy

ExitMode = Literal["intrabar", "core_1h_close"]
AmbiguityPolicy = Literal["conservative", "optimistic"]  # если в одном баре задеты и TP и SL
Market = Literal["um_futures", "spot"]
FundingPolicy = Literal["none", "adverse", "follow"]


# ===== Binance endpoints fallback =====
BINANCE_BASES = [
    "https://fapi.binance.com",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

def _endpoint(market: Market, path: str) -> str:
    if market == "um_futures":
        # use futures endpoints if possible
        # example paths: /fapi/v1/klines, /fapi/v1/ticker/24hr
        return path
    # spot endpoints
    # example paths: /api/v3/klines, /api/v3/ticker/24hr
    return path


def make_session() -> requests.Session:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retries = Retry(
        total=8,
        connect=8,
        read=8,
        backoff_factor=0.8,
        status_forcelist=[418, 429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = make_session()


# ===== Costs model (per side rates) =====
@dataclass
class CostModel:
    taker_fee: float = 0.0005     # 0.05% per side
    slippage: float = 0.0002      # 0.02% per side
    spread: float = 0.0000        # 0.00% per side

    @property
    def per_side_rate(self) -> float:
        return float(self.taker_fee + self.slippage + self.spread)

    @property
    def round_trip_rate(self) -> float:
        return float(2.0 * self.per_side_rate)


@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    tp: float
    exit_time: pd.Timestamp
    exit_price: float
    outcome: str  # TP / SL
    notional: float
    pnl_usdt: float
    ret_pct: float
    r_multiple: float
    duration_bars_5m: int
    meta: dict


@dataclass
class ActivePos:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    tp: float
    notional: float
    stop_dist_pct: float
    meta: dict
    next_funding_time: Optional[pd.Timestamp] = None


def _try_get_json(path: str, params: dict, timeout: int = 30) -> list:
    last_err = None
    for base in BINANCE_BASES:
        try:
            r = SESSION.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last_err = RuntimeError(f"HTTP {r.status_code} from {base}: {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            last_err = e
    raise last_err if last_err is not None else RuntimeError("Unknown request error")


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    market: Market = "um_futures",
    limit: int = 1000
) -> pd.DataFrame:
    """
    Fetch OHLCV candles and return df with UTC datetime index.
    Uses futures by default: /fapi/v1/klines
    """
    if market == "um_futures":
        path = "/fapi/v1/klines"
    else:
        path = "/api/v3/klines"

    rows = []
    cur = start_ms

    while True:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ms,
            "limit": limit,
        }

        batch = _try_get_json(path, params=params, timeout=30)

        if not batch:
            break

        rows.extend(batch)

        if len(rows) % 20000 == 0:
            last_ts = pd.to_datetime(batch[-1][0], unit="ms", utc=True)
            print(f"[DL] {symbol} {interval}: {len(rows)} candles, last={last_ts}")

        last_open_time = batch[-1][0]
        cur = last_open_time + 1

        if len(batch) < limit:
            break

        time.sleep(0.05)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "num_trades", "taker_base", "taker_quote", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)

    return df[["open", "high", "low", "close", "volume"]].sort_index()


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    v = df["volume"].resample(rule).sum()
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna()


def _hit_test_intrabar(
    side: str,
    candle_high: float,
    candle_low: float,
    stop: float,
    tp: float,
    ambiguity_policy: AmbiguityPolicy
) -> Optional[str]:
    if side == "LONG":
        sl_hit = candle_low <= stop
        tp_hit = candle_high >= tp
    else:
        sl_hit = candle_high >= stop
        tp_hit = candle_low <= tp

    if sl_hit and tp_hit:
        return "SL" if ambiguity_policy == "conservative" else "TP"
    if tp_hit:
        return "TP"
    if sl_hit:
        return "SL"
    return None


def _calc_notional_from_risk(
    equity: float,
    risk_alloc: float,
    entry: float,
    stop: float
) -> Tuple[float, float]:
    """
    Position sizing:
    choose notional so that loss at stop ~= risk_alloc * equity
    stop_dist_pct = |entry-stop|/entry
    notional = (risk_alloc*equity) / stop_dist_pct
    """
    stop_dist_pct = abs(entry - stop) / entry if entry > 0 else 0.0
    if stop_dist_pct <= 0:
        return 0.0, 0.0
    notional = (risk_alloc * equity) / stop_dist_pct
    return float(notional), float(stop_dist_pct)


def _apply_costs_and_pnl_pct(
    side: str,
    entry: float,
    exit_px: float,
    notional: float,
    equity_at_exit: float,
    cost: CostModel
) -> Tuple[float, float]:
    """
    Returns (pnl_usdt_net, ret_pct_net)
    PnL from price move on notional - fees/slippage/spread approximated as per-side rates on notional.
    """
    if notional <= 0 or equity_at_exit <= 0 or entry <= 0:
        return 0.0, 0.0

    # gross pnl (no fees)
    if side == "LONG":
        pnl_gross = notional * ((exit_px - entry) / entry)
    else:
        pnl_gross = notional * ((entry - exit_px) / entry)

    # fees+slippage+spread cost on notional (entry+exit)
    fees = notional * cost.round_trip_rate

    pnl_net = pnl_gross - fees
    ret_pct = pnl_net / equity_at_exit
    return float(pnl_net), float(ret_pct)


def _next_funding_timestamp_utc(t: pd.Timestamp) -> pd.Timestamp:
    """
    Funding typically at 00:00, 08:00, 16:00 UTC.
    Return next timestamp >= t aligned to 8h.
    """
    t = t.floor("min")
    hour = t.hour
    # next funding hour in {0,8,16}
    if hour < 8:
        nxt = t.normalize() + pd.Timedelta(hours=8)
    elif hour < 16:
        nxt = t.normalize() + pd.Timedelta(hours=16)
    else:
        nxt = (t.normalize() + pd.Timedelta(days=1))
    return nxt.tz_convert("UTC")


def _apply_funding(
    equity: float,
    notional: float,
    side: str,
    funding_rate_8h: float,
    funding_policy: FundingPolicy
) -> float:
    """
    Conservative funding model:
    - none: no funding
    - adverse: always pay abs(rate) * notional
    - follow: long pays if rate>0 else receives; short opposite (rarely used without real rate series)
    """
    if funding_policy == "none" or funding_rate_8h == 0 or notional <= 0:
        return equity

    rate = float(funding_rate_8h)

    if funding_policy == "adverse":
        pay = abs(rate) * notional
        return max(0.0, equity - pay)

    # follow policy (sign matters)
    if side == "LONG":
        # if rate>0 long pays, else receives
        delta = -rate * notional
    else:
        # short receives when rate>0, pays when rate<0
        delta = rate * notional

    return max(0.0, equity + delta)


def compute_equity_stats_from_curve(equity: pd.Series) -> dict:
    if equity is None or equity.empty:
        return {"points": 0}

    arr = equity.values.astype(float)
    peak = np.maximum.accumulate(arr)
    dd = (arr / peak) - 1.0
    max_dd = float(dd.min())
    total_return = float(arr[-1] / arr[0] - 1.0) if arr[0] != 0 else float("nan")

    return {
        "points": int(len(arr)),
        "total_return_pct": total_return,
        "max_drawdown_pct": max_dd,
        "start_equity": float(arr[0]),
        "end_equity": float(arr[-1]),
    }


def compute_trade_stats(trades: List[TradeRecord]) -> dict:
    if not trades:
        return {"trades": 0}

    rets = np.array([t.ret_pct for t in trades], dtype=float)
    wins = rets[rets > 0]
    losses = rets[rets < 0]

    pf = float(wins.sum() / abs(losses.sum())) if losses.size else float("inf")
    winrate = float((rets > 0).mean())
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    expectancy = float(rets.mean())

    rs = np.array([t.r_multiple for t in trades], dtype=float)
    r_expectancy = float(rs.mean()) if rs.size else 0.0

    return {
        "trades": int(len(trades)),
        "winrate": winrate,
        "profit_factor": pf,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": expectancy,
        "r_expectancy": r_expectancy,
    }


def get_top_usdt_symbols_by_volume(
    min_volume_24h: float = 50_000_000,
    max_symbols: int = 10,
    market: Market = "um_futures"
) -> list[str]:
    stable_bases = {"USDC", "BUSD", "TUSD", "USDP", "PAX", "FDUSD"}

    if market == "um_futures":
        path = "/fapi/v1/ticker/24hr"
    else:
        path = "/api/v3/ticker/24hr"

    tickers = _try_get_json(path, params={}, timeout=30)

    candidates = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in stable_bases:
            continue
        try:
            vol = float(t.get("quoteVolume", 0) or 0.0)
        except Exception:
            continue
        if vol >= min_volume_24h:
            candidates.append((sym, vol))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in candidates[:max_symbols]]


def _cache_path(cache_dir: str, symbol: str, interval: str, start_ms: int, end_ms: int, market: Market) -> str:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return os.path.join(cache_dir, f"{market}_{symbol}_{interval}_{start_ms}_{end_ms}.parquet")


def load_or_fetch_klines_cached(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    market: Market = "um_futures",
    cache_dir: str = "cache"
) -> pd.DataFrame:
    path = _cache_path(cache_dir, symbol, interval, start_ms, end_ms, market)
    if os.path.exists(path):
        try:
            return pd.read_parquet(path)
        except Exception:
            pass

    df = fetch_klines(symbol, interval, start_ms, end_ms, market=market)
    if not df.empty:
        try:
            df.to_parquet(path)
        except Exception:
            pass
        print(f"[DONE] {symbol} {interval}: total={len(df)} candles")
    return df


def backtest_portfolio(
    symbols: list[str],
    start_ms: int,
    end_ms: int,
    warmup_bars: int = 600,
    exit_mode: ExitMode = "intrabar",
    ambiguity_policy: AmbiguityPolicy = "conservative",
    limit_bars_tf: int = 300,
    start_equity: float = 10000.0,
    max_total_risk: float = 0.03,
    market: Market = "um_futures",
    cache_dir: str = "cache",
    costs: Optional[CostModel] = None,
    funding_rate_8h: float = 0.0,
    funding_policy: FundingPolicy = "none",
    autosave_every: int = 10000,
) -> tuple[list[TradeRecord], pd.Series, dict[str, dict]]:
    """
    Portfolio backtest:
    - 1 active position per symbol
    - risk cap: max_total_risk (sum of risk_per_trade allocations)
    - realistic position sizing from stop distance
    - costs: taker+slippage+spread per side
    - funding: every 8h while position open
    """
    if costs is None:
        costs = CostModel()

    strategy = NarrativeStrategy(
        load_narrative_config()
    )
    risk_per_trade = float(strategy.risk_per_trade)  # 0.01 by default

    # Load data per symbol
    data: Dict[str, Dict[str, pd.DataFrame]] = {}
    start_times: List[pd.Timestamp] = []

    for sym in symbols:
        print(f"\n=== Loading {sym} 5m ===")
        df5 = load_or_fetch_klines_cached(sym, "5m", start_ms, end_ms, market=market, cache_dir=cache_dir)
        print(f"Loaded {sym}: {len(df5)} rows (cached={len(df5) > 0})")
        if df5.empty or len(df5) <= warmup_bars + 50:
            print(f"Skip {sym}: not enough data")
            continue

        df15 = resample_ohlcv(df5, "15min")
        df1h = resample_ohlcv(df5, "1h")
        df4h = resample_ohlcv(df5, "4h")
        dfd  = resample_ohlcv(df5, "1d")

        start_times.append(df5.index[warmup_bars])

        data[sym] = {"5M": df5, "15M": df15, "1H": df1h, "4H": df4h, "D": dfd}

    symbols_ok = list(data.keys())
    if not symbols_ok:
        return [], pd.Series(dtype=float), {}

    sim_start_time = max(start_times)
    all_times = sorted(set().union(*[set(data[s]["5M"].index) for s in symbols_ok]))
    times = [t for t in all_times if t >= sim_start_time]

    # State
    equity = float(start_equity)
    equity_curve: List[Tuple[pd.Timestamp, float]] = []

    active: Dict[str, ActivePos] = {}
    active_risk: Dict[str, float] = {}  # risk allocation per position (fraction of equity)
    entry_meta: Dict[str, dict] = {}

    all_trades: List[TradeRecord] = []

    for step_i, t in enumerate(times):
        if step_i % 5000 == 0:
            print(f"[SIM] {step_i}/{len(times)} steps | t={t} | equity={equity:.4f} | open={len(active)}")

        # === 0) funding charges (when time reached) ===
        if funding_policy != "none" and funding_rate_8h != 0.0 and active:
            for sym, pos in list(active.items()):
                if pos.next_funding_time is None:
                    pos.next_funding_time = _next_funding_timestamp_utc(pos.entry_time)

                # apply all funding events up to current time (rare, but safe)
                while pos.next_funding_time is not None and t >= pos.next_funding_time:
                    equity = _apply_funding(
                        equity=equity,
                        notional=pos.notional,
                        side=pos.side,
                        funding_rate_8h=funding_rate_8h,
                        funding_policy=funding_policy
                    )
                    pos.next_funding_time = pos.next_funding_time + pd.Timedelta(hours=8)

                active[sym] = pos

        # === 1) exits ===
        for sym in list(active.keys()):
            df5 = data[sym]["5M"]
            if t not in df5.index:
                continue

            pos = active[sym]
            candle = df5.loc[t]

            if exit_mode == "intrabar":
                hit = _hit_test_intrabar(
                    side=pos.side,
                    candle_high=float(candle["high"]),
                    candle_low=float(candle["low"]),
                    stop=float(pos.stop),
                    tp=float(pos.tp),
                    ambiguity_policy=ambiguity_policy
                )
                if hit is None:
                    continue

                exit_px = float(pos.tp if hit == "TP" else pos.stop)

            else:  # core_1h_close
                s1h = data[sym]["1H"].loc[:t]
                if s1h.empty:
                    continue
                last_price = float(s1h["close"].iloc[-1])

                if pos.side == "LONG":
                    hit = "SL" if last_price <= pos.stop else ("TP" if last_price >= pos.tp else None)
                else:
                    hit = "SL" if last_price >= pos.stop else ("TP" if last_price <= pos.tp else None)

                if hit is None:
                    continue

                exit_px = float(last_price)

            # R multiple (approx) based on intended RR (TP) or -1 (SL)
            r_mult = float(strategy.rr_min) if hit == "TP" else -1.0

            pnl_usdt, ret_pct = _apply_costs_and_pnl_pct(
                side=pos.side,
                entry=pos.entry_price,
                exit_px=exit_px,
                notional=pos.notional,
                equity_at_exit=equity,
                cost=costs
            )

            # apply equity change
            equity = max(0.0, equity * (1.0 + ret_pct))

            # duration in bars
            duration = int(df5.index.get_loc(t) - df5.index.get_loc(pos.entry_time))

            all_trades.append(TradeRecord(
                symbol=sym,
                side=pos.side,
                entry_time=pos.entry_time,
                entry_price=float(pos.entry_price),
                stop=float(pos.stop),
                tp=float(pos.tp),
                exit_time=t,
                exit_price=float(exit_px),
                outcome=hit,
                notional=float(pos.notional),
                pnl_usdt=float(pnl_usdt),
                ret_pct=float(ret_pct),
                r_multiple=float(r_mult),
                duration_bars_5m=duration,
                meta=pos.meta or {}
            ))

            # clear state
            del active[sym]
            active_risk.pop(sym, None)
            entry_meta.pop(sym, None)

        # === 2) entries ===
        total_open_risk = sum(active_risk.values()) if active_risk else 0.0

        for sym in symbols_ok:
            if sym in active:
                continue

            df5 = data[sym]["5M"]
            if t not in df5.index:
                continue

            # risk cap
            if total_open_risk + risk_per_trade > max_total_risk:
                continue

            # slices for strategy
            s5  = data[sym]["5M"].loc[:t].tail(limit_bars_tf)
            s15 = data[sym]["15M"].loc[:t].tail(limit_bars_tf)
            s1h = data[sym]["1H"].loc[:t].tail(limit_bars_tf)
            s4h = data[sym]["4H"].loc[:t].tail(limit_bars_tf)
            sd  = data[sym]["D"].loc[:t].tail(limit_bars_tf)

            if any(x is None or x.empty for x in (s5, s15, s1h, s4h)):
                continue

            sig = strategy.generate_signal({"D": sd, "4H": s4h, "1H": s1h, "15M": s15, "5M": s5})

            if sig.get("signal") != "ENTER":
                continue

            side = str(sig["side"])
            entry = float(sig["entry_price"])
            stop = float(sig["stop_price"])
            tp = float(sig["tp_price"])

            notional, stop_dist_pct = _calc_notional_from_risk(
                equity=equity,
                risk_alloc=risk_per_trade,
                entry=entry,
                stop=stop
            )
            if notional <= 0 or stop_dist_pct <= 0:
                continue

            meta = {
                "narrative": sig.get("narrative"),
                "vc": sig.get("vc"),
                "trigger_reason": sig.get("trigger_reason"),
                "rr": sig.get("rr"),
                "stop_dist_pct": stop_dist_pct,
                "cost_per_side": costs.per_side_rate,
                "funding_rate_8h": funding_rate_8h,
                "funding_policy": funding_policy,
                "market": market,
            }

            active[sym] = ActivePos(
                symbol=sym,
                side=side,
                entry_time=t,
                entry_price=entry,
                stop=stop,
                tp=tp,
                notional=notional,
                stop_dist_pct=stop_dist_pct,
                meta=meta,
                next_funding_time=_next_funding_timestamp_utc(t) if funding_policy != "none" and funding_rate_8h != 0.0 else None
            )
            active_risk[sym] = risk_per_trade
            total_open_risk += risk_per_trade

        equity_curve.append((t, equity))

        # autosave partial
        if autosave_every and (step_i > 0) and (step_i % autosave_every == 0):
            tmp = pd.Series([v for _, v in equity_curve], index=[ts for ts, _ in equity_curve], dtype=float)
            tmp.to_csv("portfolio_equity_partial.csv", header=["equity"])
            print("[SAVE] portfolio_equity_partial.csv")

    eq = pd.Series(
        data=[v for _, v in equity_curve],
        index=[ts for ts, _ in equity_curve],
        dtype=float
    )
    eq.name = "equity"

    # per-symbol stats on closed trades only
    per_symbol = {}
    for sym in symbols_ok:
        ts = [t for t in all_trades if t.symbol == sym]
        st = compute_trade_stats(ts)
        # simulate symbol-only equity from trade returns
        e = float(start_equity)
        peak = e
        max_dd = 0.0
        for tr in ts:
            e = max(0.0, e * (1.0 + tr.ret_pct))
            peak = max(peak, e)
            dd = (e / peak) - 1.0
            max_dd = min(max_dd, dd)
        per_symbol[sym] = {
            "symbol": sym,
            "trades": st.get("trades", 0),
            "winrate": st.get("winrate", 0.0),
            "profit_factor": st.get("profit_factor", 0.0),
            "expectancy_pct": st.get("expectancy_pct", 0.0),
            "r_expectancy": st.get("r_expectancy", 0.0),
            "end_equity": e,
            "total_return_pct": (e / start_equity - 1.0) if start_equity != 0 else float("nan"),
            "max_dd_pct": float(max_dd),
        }

    return all_trades, eq, per_symbol


def save_outputs(
    trades: List[TradeRecord],
    equity: pd.Series,
    per_symbol: dict,
    out_dir: str = "."
) -> Tuple[str, str, str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    eq_path = os.path.join(out_dir, "portfolio_equity.csv")
    tr_path = os.path.join(out_dir, "portfolio_trades.csv")
    ps_path = os.path.join(out_dir, "per_symbol_stats.csv")

    equity.to_csv(eq_path, header=["equity"])

    if trades:
        df_tr = pd.DataFrame([{
            "symbol": t.symbol,
            "side": t.side,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "stop": t.stop,
            "tp": t.tp,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "outcome": t.outcome,
            "notional": t.notional,
            "pnl_usdt": t.pnl_usdt,
            "ret_pct": t.ret_pct,
            "r_multiple": t.r_multiple,
            "duration_bars_5m": t.duration_bars_5m,
            "meta": t.meta,
        } for t in trades])
        df_tr.to_csv(tr_path, index=False)
    else:
        pd.DataFrame().to_csv(tr_path, index=False)

    df_ps = pd.DataFrame(list(per_symbol.values())).sort_values("end_equity", ascending=False)
    df_ps.to_csv(ps_path, index=False)

    return eq_path, tr_path, ps_path


def save_equity_plot_png(equity: pd.Series, path_png: str = "portfolio_equity.png"):
    # Без matplotlib.show — просто сохраняем png
    import matplotlib.pyplot as plt
    plt.figure()
    plt.plot(equity.index, equity.values)
    plt.title("Portfolio Equity")
    plt.xlabel("Time (UTC)")
    plt.ylabel("Equity")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(path_png, dpi=150)
    plt.close()
