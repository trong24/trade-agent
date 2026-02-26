"""CLI: compute trend + S/R for BTCUSDT and persist to market_facts in DuckDB.

Usage:
    analyze-market --start 2025-01-01
    analyze-market --intervals 1h,4h,1d,1w --lookback 500
    python -m trade_agent.scripts.analyze_market
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from trade_agent.analysis.payload import build_payload
from trade_agent.analysis.sr import compute_sr
from trade_agent.analysis.trend import compute_trend
from trade_agent.db import (
    connect,
    init_db,
    read_candles,
    upsert_market_facts,
)

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)
log = logging.getLogger(__name__)

_DEFAULT_INTERVALS = "1h,4h,1d,1w"
_DEFAULT_LOOKBACK = 1000


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Analyze BTCUSDT market structure and persist facts to DuckDB",
    )
    p.add_argument("--db",        default="data/trade.duckdb")
    p.add_argument("--symbol",    default="BTCUSDT")
    p.add_argument("--intervals", default=_DEFAULT_INTERVALS,
                   help="Comma-separated TFs to analyze (default: 1h,4h,1d,1w)")
    p.add_argument("--lookback",  type=int, default=_DEFAULT_LOOKBACK,
                   help="Max candles to use per TF (default: 1000)")
    p.add_argument("--as-of",     default=None,
                   help="ISO datetime for analysis snapshot (default: now UTC)")
    p.add_argument("--version",   default="v1")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    intervals = [iv.strip() for iv in args.intervals.split(",") if iv.strip()]
    as_of = (
        datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
        if args.as_of
        else datetime.now(timezone.utc)
    )

    con = connect(args.db)
    init_db(con)

    per_tf_facts: dict[str, dict] = {}
    summary_rows: list[tuple] = []

    for interval in intervals:
        df = read_candles(con, args.symbol, interval)
        if df.empty:
            console.print(f"[yellow]No data for {interval} — run sync-klines first[/]")
            continue

        # Take last N bars
        df = df.iloc[-args.lookback:]

        trend_facts = compute_trend(df)
        sr_facts = compute_sr(df)

        per_tf_facts[interval] = {
            "trend": trend_facts,
            "sr":    sr_facts,
        }

        # Persist per-TF facts
        upsert_market_facts(
            con, args.symbol, as_of, interval,
            per_tf_facts[interval], version=args.version,
        )

        levels_count = len(sr_facts.get("levels", []))
        summary_rows.append((
            interval,
            trend_facts["trend_dir"],
            f"{trend_facts['trend_strength']:.3f}",
            f"{trend_facts['atr_pct']:.2f}%",
            str(len(df)),
            str(levels_count),
        ))

    # Build + persist ALL-timeframe payload
    if per_tf_facts:
        payload = build_payload(args.symbol, as_of, per_tf_facts)
        upsert_market_facts(
            con, args.symbol, as_of, "ALL", payload, version=args.version,
        )

    con.close()

    # Print summary
    tbl = Table(
        title=f"Market Analysis — {args.symbol}  as_of: {as_of.strftime('%Y-%m-%d %H:%M')} UTC",
        show_header=True,
    )
    for col in ("TF", "Trend", "Strength", "ATR%", "Bars", "S/R Levels"):
        tbl.add_column(col, style="cyan" if col == "TF" else "white")
    for row in summary_rows:
        tbl.add_row(*row)

    if per_tf_facts:
        payload = build_payload(args.symbol, as_of, per_tf_facts)
        inv = payload.get("invalidation", {})
        tbl.caption = (
            f"Support below: {inv.get('support_below')}  |  "
            f"Resistance above: {inv.get('resistance_above')}"
        )

    console.print(tbl)
    console.print(f"[green]Facts saved to DB (version={args.version}, interval=ALL + per TF)[/]")


if __name__ == "__main__":
    main()
