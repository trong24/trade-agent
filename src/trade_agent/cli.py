from __future__ import annotations

import argparse
from pathlib import Path

from .engine.backtest import BacktestEngine
from .brokers.paper import PaperBroker
from .loaders.csv import load_candles_from_csv
from .risks.fixed_fraction import FixedFractionRisk
from .strategies.sma import SMACrossStrategy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal trade agent backtest")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV")
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--short", type=int, default=20, help="Short SMA window")
    parser.add_argument("--long", type=int, default=50, help="Long SMA window")
    parser.add_argument(
        "--risk", type=float, default=0.2, help="Max equity fraction per entry"
    )
    parser.add_argument("--fee-bps", type=float, default=6.0, help="Fee in bps")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    candles = load_candles_from_csv(Path(args.csv))
    strategy = SMACrossStrategy(short_window=args.short, long_window=args.long)
    broker = PaperBroker(initial_cash=args.initial_cash, fee_bps=args.fee_bps)
    risk = FixedFractionRisk(max_fraction=args.risk)

    result = BacktestEngine(candles, strategy, broker, risk).run()

    print("=== Backtest Result ===")
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
