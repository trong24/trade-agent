"""CLI: sync BTC/USDT klines incrementally from Binance Futures.

Usage:
    sync-klines --symbol BTCUSDT --interval 1m --start 2024-01-01
    python -m trade_agent.scripts.sync_klines --symbol BTCUSDT --interval 1m
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from trade_agent.data.binance_client import BinanceClient, _ts_ms
from trade_agent.data.klines_store import KlinesStore

console = Console()
logging.basicConfig(level=logging.WARNING, handlers=[RichHandler(console=console, show_path=False)])
log = logging.getLogger(__name__)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Incrementally sync klines from Binance USD-M Futures")
    p.add_argument("--symbol",   default="BTCUSDT",    help="Trading pair, e.g. BTCUSDT")
    p.add_argument("--interval", default="1m",          help="Candle interval, e.g. 1m 5m 1h")
    p.add_argument("--start",    default="2020-01-01",  help="Start date (ISO), ignored if store already has data")
    p.add_argument("--end",      default=None,           help="End date (ISO), default: now")
    p.add_argument("--data-dir", default="data/raw",    help="Path to data store root")
    p.add_argument("--force",    action="store_true",   help="Ignore local last_open_time and re-sync from --start")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    store  = KlinesStore(args.data_dir)
    client = BinanceClient()

    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)

    # Incremental: start from last stored candle + 1ms
    if not args.force:
        last = store.get_last_open_time(args.symbol, args.interval)
        start_dt = last.replace(tzinfo=timezone.utc) if last else _parse_dt(args.start)
        if last:
            console.print(f"[cyan]Resuming from[/] {start_dt.isoformat()}")
    else:
        start_dt = _parse_dt(args.start)
        console.print(f"[yellow]Force sync from[/] {start_dt.isoformat()}")

    start_ms = _ts_ms(start_dt)
    end_ms   = _ts_ms(end_dt)

    if start_ms >= end_ms:
        console.print("[green]Already up-to-date ✓[/]")
        return

    total_new = 0
    total_batches = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("{task.completed} batches"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(
            f"[cyan]Syncing {args.symbol} {args.interval}[/]", total=None
        )

        for batch in client.get_klines_paginated(args.symbol, args.interval, start_ms, end_ms):
            new = store.append(args.symbol, args.interval, batch)
            total_new += new
            total_batches += 1
            progress.advance(task)

    cov = store.coverage(args.symbol, args.interval)
    console.print(
        f"[green]Done![/] {total_new:,} new candles across {total_batches} batches.\n"
        f"Coverage: {cov['first']} → {cov['last']}  ({cov['candles']:,} total)"
    )


if __name__ == "__main__":
    main()
