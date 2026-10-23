# run_backtest_portfolio.py
import argparse
from datetime import datetime, timezone

from backtest import (
    backtest_portfolio,
    compute_equity_stats_from_curve,
    compute_trade_stats,
    get_top_usdt_symbols_by_volume,
    save_outputs,
    save_equity_plot_png,
    CostModel,
)

def _to_ms(dt_str: str) -> int:
    # date like YYYY-MM-DD -> UTC midnight
    dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--symbols", type=str, default="", help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min_vol", type=float, default=50_000_000)

    p.add_argument("--start", type=str, required=True, help="YYYY-MM-DD")
    p.add_argument("--end", type=str, required=True, help="YYYY-MM-DD")

    p.add_argument("--market", type=str, default="um_futures", choices=["um_futures", "spot"])
    p.add_argument("--exit_mode", type=str, default="intrabar", choices=["intrabar", "core_1h_close"])
    p.add_argument("--policy", type=str, default="conservative", choices=["conservative", "optimistic"])

    p.add_argument("--warmup_bars", type=int, default=600)
    p.add_argument("--limit_bars_tf", type=int, default=300)

    p.add_argument("--start_equity", type=float, default=10000.0)
    p.add_argument("--max_total_risk", type=float, default=0.03)

    # costs
    p.add_argument("--taker_fee", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0002)
    p.add_argument("--spread", type=float, default=0.0000)

    # funding
    p.add_argument("--funding_rate_8h", type=float, default=0.0, help="e.g. 0.0001 = 0.01% per 8h")
    p.add_argument("--funding_policy", type=str, default="none", choices=["none", "adverse", "follow"])

    p.add_argument("--cache_dir", type=str, default="cache")
    p.add_argument("--out_dir", type=str, default=".")
    p.add_argument("--autosave_every", type=int, default=10000)

    return p.parse_args()

def main():
    args = parse_args()

    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = get_top_usdt_symbols_by_volume(
            min_volume_24h=args.min_vol,
            max_symbols=args.top,
            market=args.market
        )

    print(f"\nUniverse ({len(symbols)}): {symbols}\n")

    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end)

    costs = CostModel(
        taker_fee=args.taker_fee,
        slippage=args.slippage,
        spread=args.spread
    )

    trades, equity, per_symbol = backtest_portfolio(
        symbols=symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        warmup_bars=args.warmup_bars,
        exit_mode=args.exit_mode,
        ambiguity_policy=args.policy,
        limit_bars_tf=args.limit_bars_tf,
        start_equity=args.start_equity,
        max_total_risk=args.max_total_risk,
        market=args.market,
        cache_dir=args.cache_dir,
        costs=costs,
        funding_rate_8h=args.funding_rate_8h,
        funding_policy=args.funding_policy,
        autosave_every=args.autosave_every,
    )

    eq_stats = compute_equity_stats_from_curve(equity)
    tr_stats = compute_trade_stats(trades)

    print("\n=== PORTFOLIO EQUITY STATS ===")
    for k, v in eq_stats.items():
        print(f"{k}: {v}")

    print("\n=== PORTFOLIO TRADE STATS (by closed trades) ===")
    for k, v in tr_stats.items():
        print(f"{k}: {v}")

    eq_path, tr_path, ps_path = save_outputs(trades, equity, per_symbol, out_dir=args.out_dir)
    print(f"\nSaved: {eq_path}")
    print(f"Saved: {tr_path}")
    print(f"Saved: {ps_path}")

    png_path = f"{args.out_dir.rstrip('/\\\\')}/portfolio_equity.png"
    save_equity_plot_png(equity, path_png=png_path)
    print(f"Saved: {png_path}")

    print("\n=== PER SYMBOL ===")
    import pandas as pd
    df = pd.read_csv(ps_path)
    # красиво печатаем top-10
    print(df.head(10).to_string(index=False))

if __name__ == "__main__":
    main()
