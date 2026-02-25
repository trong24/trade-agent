"""CLI: sync BTC/USDT klines for multiple timeframes from Binance Futures.

Usage:
    sync-klines                          # default: BTCUSDT, all intervals, 2021-01-01
    sync-klines --symbol BTCUSDT --intervals 1h,4h
    sync-klines --symbol BTCUSDT --start 2022-01-01 --force
    python -m trade_agent.scripts.sync_klines
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from trade_agent.data.binance_client import SUPPORTED_INTERVALS, BinanceClient, _ts_ms
from trade_agent.data.klines_store import KlinesStore

console = Console()
logging.basicConfig(level=logging.WARNING, handlers=[RichHandler(console=console, show_path=False)])
log = logging.getLogger(__name__)

_DEFAULT_START = "2021-01-01"
_DEFAULT_SYMBOL = "BTCUSDT"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Incrementally sync klines from Binance USD-M Futures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Supported intervals: {', '.join(SUPPORTED_INTERVALS)}",
    )
    p.add_argument("--symbol", default=_DEFAULT_SYMBOL, help="e.g. BTCUSDT, ETHUSDT")
    p.add_argument(
        "--intervals",
        default=",".join(SUPPORTED_INTERVALS),
        help=f"Comma-separated intervals (default: all). Choices: {', '.join(SUPPORTED_INTERVALS)}",
    )
    p.add_argument("--start", default=_DEFAULT_START, help="Start date ISO (default: 2021-01-01)")
    p.add_argument("--end", default=None, help="End date ISO (default: now)")
    p.add_argument("--data-dir", default="data", help="Data store root (default: data/)")
    p.add_argument(
        "--force", action="store_true", help="Re-sync from --start, ignore stored last_open_time"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _sync_one(
    client: BinanceClient,
    store: KlinesStore,
    symbol: str,
    interval: str,
    default_start: datetime,
    end_dt: datetime,
    force: bool,
) -> tuple[int, int]:
    """Sync one symbol+interval. Returns (new_candles, batch_count)."""
    if not force:
        last = store.get_last_open_time(symbol, interval)
        start_dt = last.replace(tzinfo=UTC) if last else default_start
    else:
        start_dt = default_start

    start_ms = _ts_ms(start_dt)
    end_ms = _ts_ms(end_dt)

    if start_ms >= end_ms:
        return 0, 0

    total_new = 0
    batches = 0
    for batch in client.get_klines_paginated(symbol, interval, start_ms, end_ms):
        total_new += store.append(symbol, interval, batch)
        batches += 1

    return total_new, batches


def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse + validate intervals
    requested = [iv.strip() for iv in args.intervals.split(",") if iv.strip()]
    invalid = [iv for iv in requested if iv not in SUPPORTED_INTERVALS]
    if invalid:
        console.print(f"[red]Unknown interval(s): {invalid}. Supported: {SUPPORTED_INTERVALS}[/]")
        raise SystemExit(1)

    store = KlinesStore(args.data_dir)
    client = BinanceClient()
    end_dt = _parse_dt(args.end) if args.end else datetime.now(UTC)
    start_dt = _parse_dt(args.start)

    console.print(
        f"[bold cyan]Syncing {args.symbol}[/]  intervals: {requested}  "
        f"start: {start_dt.date()}  end: {end_dt.date()}"
    )

    summary_rows: list[tuple] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description:<12}[/]"),
        BarColumn(bar_width=25),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("", total=len(requested))

        for interval in requested:
            progress.update(task, description=interval)
            new, batches = _sync_one(
                client, store, args.symbol, interval, start_dt, end_dt, args.force
            )
            cov = store.coverage(args.symbol, interval)
            summary_rows.append(
                (
                    interval,
                    str(new),
                    str(batches),
                    str(cov["candles"]),
                    cov["first"] or "-",
                    cov["last"] or "-",
                )
            )
            progress.advance(task)

    # ── Summary table ──────────────────────────────────────────────────────────
    tbl = Table(title=f"Sync Summary — {args.symbol}", show_header=True)
    for col in ("Interval", "New candles", "Batches", "Total stored", "First", "Last"):
        tbl.add_column(col, style="cyan" if col == "Interval" else "white")
    for row in summary_rows:
        tbl.add_row(*row)
    console.print(tbl)


if __name__ == "__main__":
    main()
