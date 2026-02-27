"""trade-agent CLI: run backtests from Binance Futures Parquet data."""

from __future__ import annotations

import argparse
import sys

from .brokers.paper import PaperBroker
from .engine.backtest import BacktestEngine
from .loaders.parquet import load_candles_from_store
from .risks.fixed_fraction import FixedFractionRisk
from .strategies.sma import SMACrossStrategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="trade-agent backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  trade-agent --symbol BTCUSDT --interval 1h
  trade-agent --symbol BTCUSDT --interval 4h --start 2025-01-01 --short 10 --long 30
""",
    )

    # ── Data source ────────────────────────────────────────────────────────────
    parser.add_argument("--symbol", required=True, metavar="BTCUSDT", help="Symbol to load")
    parser.add_argument("--interval", default="1h", help="Candle interval: 15m 1h 4h 1d")
    parser.add_argument("--start", default=None, help="Start date ISO (default: all stored)")
    parser.add_argument("--end", default=None, help="End date ISO (default: all stored)")
    parser.add_argument("--data-dir", default="data", help="Parquet store root (default: data/)")

    # ── Strategy ───────────────────────────────────────────────────────────────
    parser.add_argument("--short", type=int, default=20)
    parser.add_argument("--long", type=int, default=50)
    parser.add_argument("--risk", type=float, default=0.2)
    parser.add_argument("--fee-bps", type=float, default=6.0)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        candles = load_candles_from_store(
            symbol=args.symbol,
            interval=args.interval,
            start=args.start,
            end=args.end,
            data_dir=args.data_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Build backtest ─────────────────────────────────────────────────────────
    strategy = SMACrossStrategy(short_window=args.short, long_window=args.long)
    broker = PaperBroker(initial_cash=args.initial_cash, fee_bps=args.fee_bps)
    risk = FixedFractionRisk(max_fraction=args.risk)
    result = BacktestEngine(candles, strategy, broker, risk).run()

    # ── Report ─────────────────────────────────────────────────────────────────
    source = f"{args.symbol} {args.interval}" if args.symbol else args.csv
    print(f"\n=== Backtest Result — {source} ({len(candles):,} candles) ===")
    print(f"Initial Cash   : {result.initial_cash:>12,.2f}")
    print(f"Final Equity   : {result.final_equity:>12,.2f}")
    print(f"Return         : {result.total_return_pct:>11.2f}%")
    print(f"Max Drawdown   : {result.max_drawdown_pct:>11.2f}%")
    print(f"Total Fees     : {result.total_fees:>12,.2f}")
    print(f"Trades         : {result.num_trades:>12}")
    print(f"Wins / Losses  : {result.wins} / {result.losses}")
    print(f"Win Rate       : {result.win_rate:>11.1f}%")


if __name__ == "__main__":
    main()
