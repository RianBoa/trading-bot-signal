# run_backtest.py
import argparse
import pandas as pd

from backtest import (
    fetch_klines, backtest_symbol, compute_stats, plot_equity
)
from core.strategy_config import load_narrative_config
from core.strategy_narrative import NarrativeStrategy


def to_ms(dt: str) -> int:
    # dt: "2025-01-01"
    return int(pd.Timestamp(dt, tz="UTC").timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-29")
    ap.add_argument("--exit_mode", default="intrabar", choices=["intrabar", "core_1h_close"])
    ap.add_argument("--policy", default="conservative", choices=["conservative", "optimistic"])
    ap.add_argument("--warmup", type=int, default=600)
    args = ap.parse_args()

    start_ms = to_ms(args.start)
    end_ms = to_ms(args.end)

    print(f"Downloading {args.symbol} 5m from {args.start} to {args.end} ...")
    df5 = fetch_klines(args.symbol, "5m", start_ms, end_ms)
    if df5.empty:
        print("No data.")
        return

    strat = NarrativeStrategy(
        load_narrative_config()
    )

    trades = backtest_symbol(
        symbol=args.symbol,
        df5=df5,
        strategy=strat,
        warmup_bars=args.warmup,
        exit_mode=args.exit_mode,
        ambiguity_policy=args.policy
    )

    stats = compute_stats(trades)

    print("\n=== STATS ===")
    for k, v in stats.items():
        if k == "equity_curve":
            continue
        print(f"{k}: {v}")

    if trades:
        eq = stats["equity_curve"]
        plot_equity(eq, f"{args.symbol} | {args.exit_mode} | trades={stats['trades']}")

        # сохраним сделки в CSV
        out = pd.DataFrame([t.__dict__ for t in trades])
        out.to_csv(f"trades_{args.symbol}.csv", index=False)
        print(f"\nSaved: trades_{args.symbol}.csv")


if __name__ == "__main__":
    main()
