"""CLI: grid-search param combos and rank by metrics.

Usage:
    run-experiments --start 2025-01-01 --interval 1h
    run-experiments --start 2025-01-01 --interval 1h --top 10
"""

from __future__ import annotations

import argparse
import itertools
import json
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

# ── Default parameter grid ─────────────────────────────────────────────────
DEFAULT_GRID = {
    "zone_mult": [0.5, 1.0, 1.5, 2.0, 3.0],
    "fee_bps": [1.0, 2.0, 4.0],
}


def _grid_combos(grid: dict) -> list[dict]:
    """Expand a dict of {param: [values]} into a list of param dicts."""
    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grid-search param combos and rank by Sharpe/return",
    )
    p.add_argument("--db", default="data/trade.duckdb")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=None)
    p.add_argument("--facts-version", default="v1")
    p.add_argument("--strategy", default="rsi_inertia")
    p.add_argument("--top", type=int, default=20, help="Show top N results (default: 20)")
    p.add_argument("--save", action="store_true", help="Save all runs to backtest_runs")
    p.add_argument("--json", action="store_true", dest="json_mode")
    return p


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main() -> None:
    args = build_parser().parse_args()

    start_dt = _parse_dt(args.start)
    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)

    con = connect(args.db)
    init_db(con)

    df = read_candles(con, args.symbol, args.interval, start=start_dt, end=end_dt)
    if df.empty:
        console.print("[red]No candles. Run sync-klines first.[/]")
        raise SystemExit(1)

    facts = read_latest_facts(con, args.symbol, "ALL", version=args.facts_version)
    if facts is None:
        console.print("[red]No facts. Run analyze-market first.[/]")
        raise SystemExit(1)

    combos = _grid_combos(DEFAULT_GRID)
    console.print(
        f"[cyan]Running {len(combos)} experiment(s)[/] on "
        f"{args.symbol} {args.interval} "
        f"({len(df)} bars, {start_dt.date()} → {end_dt.date()})"
    )

    results: list[dict] = []

    for combo in combos:
        zm = combo.get("zone_mult", 1.5)
        fb = combo.get("fee_bps", 2.0)

        signals = generate_signals(
            df,
            facts,
            interval=args.interval,
            params={"zone_mult": zm},
        )
        result = run_vectorized_backtest(df, signals, fee_bps=fb)
        metrics = result["metrics"]
        metrics["zone_mult"] = zm
        metrics["fee_bps"] = fb
        results.append(metrics)

        if args.save:
            run_id = str(uuid.uuid4())
            insert_backtest_run(
                con,
                run_id=run_id,
                symbol=args.symbol,
                interval=args.interval,
                start_time=start_dt,
                end_time=end_dt,
                strategy_id=args.strategy,
                params=combo,
                facts_version=args.facts_version,
                metrics=metrics,
            )

    con.close()

    # Sort by Sharpe descending
    results.sort(key=lambda r: r.get("sharpe", 0), reverse=True)
    top = results[: args.top]

    if args.json_mode:
        import sys

        json.dump(top, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    # Rich table
    tbl = Table(
        title=f"Experiment Results — {args.symbol} {args.interval} (top {args.top})",
        show_header=True,
    )
    tbl.add_column("#", width=3)
    tbl.add_column("zone_mult", width=10)
    tbl.add_column("fee_bps", width=8)
    tbl.add_column("Return%", width=10)
    tbl.add_column("MaxDD%", width=10)
    tbl.add_column("Sharpe", width=8)
    tbl.add_column("Trades", width=7)

    for i, r in enumerate(top, 1):
        ret = r.get("total_return_pct", 0)
        color = "green" if ret > 0 else "red"
        tbl.add_row(
            str(i),
            f"{r.get('zone_mult', 0):.1f}",
            f"{r.get('fee_bps', 0):.1f}",
            f"[{color}]{ret:.2f}%[/]",
            f"{r.get('max_drawdown_pct', 0):.2f}%",
            f"{r.get('sharpe', 0):.3f}",
            str(r.get("trades", 0)),
        )

    console.print(tbl)


if __name__ == "__main__":
    main()
