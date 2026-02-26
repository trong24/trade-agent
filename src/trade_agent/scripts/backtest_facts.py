"""CLI: run vectorized backtest consuming precomputed facts from DuckDB.

Usage:
    backtest-facts --start 2025-01-01 --end 2026-01-01
    backtest-facts --symbol BTCUSDT --interval 1h --facts-version v1 --fee-bps 2.0
    python -m trade_agent.scripts.backtest_facts
"""
from __future__ import annotations

import argparse
import logging
import uuid
from datetime import datetime, timezone

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from trade_agent.backtest.facts_strategy import generate_signals, run_vectorized_backtest
from trade_agent.db import (
    connect,
    init_db,
    insert_backtest_run,
    read_candles,
    read_latest_facts,
)

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)
log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Vectorized backtest consuming precomputed market_facts from DuckDB",
    )
    p.add_argument("--db",            default="data/trade.duckdb")
    p.add_argument("--symbol",        default="BTCUSDT")
    p.add_argument("--interval",      default="1h",
                   help="Candle interval to backtest on (default: 1h)")
    p.add_argument("--start",         required=True, help="Start date ISO e.g. 2025-01-01")
    p.add_argument("--end",           default=None,  help="End date ISO (default: now UTC)")
    p.add_argument("--facts-version", default="v1",  help="market_facts version to use")
    p.add_argument("--strategy-id",   default="sr_trend_v1")
    p.add_argument("--fee-bps",       type=float, default=2.0)
    p.add_argument("--zone-mult",     type=float, default=1.5,
                   help="Zone proximity multiplier (default: 1.5×zone_width)")
    p.add_argument("--save",          action="store_true",
                   help="Save backtest_run row to DB")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    start_dt = _parse_dt(args.start)
    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)

    con = connect(args.db)
    init_db(con)

    # ── Load candles ───────────────────────────────────────────────────────────
    df = read_candles(con, args.symbol, args.interval, start=start_dt, end=end_dt)
    if df.empty:
        console.print(
            f"[red]No candles for {args.symbol} {args.interval} "
            f"[{start_dt.date()} → {end_dt.date()}]. "
            f"Run sync-klines first.[/]"
        )
        raise SystemExit(1)

    # ── Load facts snapshot ────────────────────────────────────────────────────
    facts = read_latest_facts(
        con, args.symbol, "ALL",
        as_of_max=None,  # use latest snapshot
        version=args.facts_version,
    )
    if facts is None:
        console.print(
            f"[red]No market_facts found for {args.symbol} ALL "
            f"version={args.facts_version} as_of≤{end_dt.date()}. "
            f"Run analyze-market first.[/]"
        )
        raise SystemExit(1)

    # ── Generate signals ───────────────────────────────────────────────────────
    params = {"zone_mult": args.zone_mult}
    signals = generate_signals(df, facts, interval=args.interval, params=params)
    metrics = run_vectorized_backtest(df, signals, fee_bps=args.fee_bps)

    # ── Save run ───────────────────────────────────────────────────────────────
    run_id = str(uuid.uuid4())
    if args.save:
        run_params = {
            "fee_bps":   args.fee_bps,
            "zone_mult": args.zone_mult,
        }
        insert_backtest_run(
            con, run_id=run_id,
            symbol=args.symbol,
            interval=args.interval,
            start_time=start_dt,
            end_time=end_dt,
            strategy_id=args.strategy_id,
            params=run_params,
            facts_version=args.facts_version,
            metrics=metrics,
        )
        console.print(f"[dim]Run saved: {run_id}[/]")

    con.close()

    # ── Print results ──────────────────────────────────────────────────────────
    htf_bias = facts.get("htf_trend", {})
    htf_str = " | ".join(
        f"{tf}: {v.get('dir', '?')}" for tf, v in htf_bias.items()
    )

    tbl = Table(
        title=(
            f"Backtest: {args.symbol} {args.interval}  "
            f"{start_dt.date()} → {end_dt.date()}  "
            f"strategy={args.strategy_id}"
        ),
        show_header=False,
    )
    tbl.add_column("Metric", style="bold cyan", width=22)
    tbl.add_column("Value",  style="white")

    tbl.add_row("HTF Bias",       htf_str or "-")
    tbl.add_row("Bars",           str(metrics["bars"]))
    tbl.add_row("Trades",         str(metrics["trades"]))
    tbl.add_row("Total Return",   f"{metrics['total_return_pct']:.2f}%")
    tbl.add_row("Max Drawdown",   f"{metrics['max_drawdown_pct']:.2f}%")
    tbl.add_row("Sharpe (ann.)",  f"{metrics['sharpe']:.3f}")
    tbl.add_row("Fee (bps)",      str(args.fee_bps))
    tbl.add_row("Facts version",  args.facts_version)

    console.print(tbl)


if __name__ == "__main__":
    main()
