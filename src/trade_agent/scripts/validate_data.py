"""CLI: validate klines data coverage and quality.

Usage:
    validate-data --symbol BTCUSDT --interval 1m
    python -m trade_agent.scripts.validate_data --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from trade_agent.data.klines_store import KlinesStore
from trade_agent.data.validator import validate

console = Console()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate klines data quality")
    p.add_argument("--symbol",        default="BTCUSDT", help="Symbol, e.g. BTCUSDT")
    p.add_argument("--interval",      default="1m",       help="Interval, e.g. 1m")
    p.add_argument("--start",         default="2000-01-01")
    p.add_argument("--end",           default=None)
    p.add_argument("--data-dir",      default="data/raw")
    p.add_argument("--min-score",     type=float, default=0.95, help="Fail if quality_score below this")
    p.add_argument("--gap-threshold", type=int,   default=5,    help="Report gaps with > N missing candles")
    return p


def main() -> None:
    args = build_parser().parse_args()
    store = KlinesStore(args.data_dir)

    df = store.read_range(args.symbol, args.interval, args.start, args.end)

    if df.empty:
        console.print(f"[red]No data found for {args.symbol} {args.interval}[/]")
        console.print(f"Run: sync-klines --symbol {args.symbol} --interval {args.interval}")
        sys.exit(1)

    report = validate(df, args.symbol, args.interval, gap_threshold=args.gap_threshold)

    # ── Summary table ─────────────────────────────────────────────────────────
    tbl = Table(title=f"Data Quality — {args.symbol} {args.interval}", show_header=False)
    tbl.add_column("Key",   style="bold cyan", width=18)
    tbl.add_column("Value", style="white")

    tbl.add_row("Period",    f"{report.start.date()} → {report.end.date()}")
    tbl.add_row("Candles",   f"{report.total_candles:,} / {report.expected_candles:,} expected")
    tbl.add_row("Gaps",      f"{len(report.missing_gaps)} ({sum(g.missing_candles for g in report.missing_gaps):,} missing)")
    tbl.add_row("Duplicates",str(report.duplicate_count))
    score_color = "green" if report.is_ok(args.min_score) else "red"
    tbl.add_row("Score",     f"[{score_color}]{report.quality_score:.4f}[/{score_color}]")

    if report.schema_errors:
        tbl.add_row("Schema errors", "; ".join(report.schema_errors))

    console.print(tbl)

    # ── Gaps detail ───────────────────────────────────────────────────────────
    if report.missing_gaps:
        gap_tbl = Table(title="Gaps (top 20)", show_header=True)
        gap_tbl.add_column("Gap start",       style="yellow")
        gap_tbl.add_column("Gap end",         style="yellow")
        gap_tbl.add_column("Missing candles", style="red")
        for g in report.missing_gaps[:20]:
            gap_tbl.add_row(str(g.gap_start), str(g.gap_end), str(g.missing_candles))
        console.print(gap_tbl)

    if not report.is_ok(args.min_score):
        console.print(f"[red]Quality score {report.quality_score:.4f} < {args.min_score} — FAIL[/]")
        sys.exit(1)

    console.print("[green]All checks passed ✓[/]")


if __name__ == "__main__":
    main()
