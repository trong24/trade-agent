"""CLI: run backtest with vectorized OR plan-based strategy.

Usage:
    backtest-facts --start 2025-01-01 --interval 1h --fee-bps 2.0
    backtest-facts --start 2025-01-01 --strategy plan_v1 --show-trades
    backtest-facts --start 2025-01-01 --strategy plan_v1 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from trade_agent.backtest.facts_strategy import generate_signals, run_vectorized_backtest
from trade_agent.backtest.plan_strategy import run_plan_backtest
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
        description="Backtest: vectorized (sr_trend_v1) or plan-based (plan_v1)",
    )
    p.add_argument("--db", default="data/trade.duckdb")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--start", required=True, help="Start date ISO")
    p.add_argument("--end", default=None, help="End date ISO")
    p.add_argument("--facts-version", default="v1")
    p.add_argument(
        "--strategy",
        default="sr_trend_v1",
        choices=["sr_trend_v1", "plan_v1"],
        help="Strategy: sr_trend_v1 (vectorized) | plan_v1 (plan rules)",
    )
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--zone-mult", type=float, default=1.5)
    p.add_argument("--atr-stop-mult", type=float, default=1.5)
    p.add_argument("--time-stop", type=int, default=20)
    p.add_argument(
        "--show-trades", action="store_true", help="Print detailed trade log (plan_v1 only)"
    )
    p.add_argument("--json", action="store_true", dest="json_mode")
    p.add_argument("--save", action="store_true")
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

    df = read_candles(con, args.symbol, args.interval, start=start_dt, end=end_dt)
    if df.empty:
        console.print(f"[red]No candles. Run sync-klines first.[/]")
        raise SystemExit(1)

    facts = read_latest_facts(con, args.symbol, "ALL", version=args.facts_version)
    if facts is None and args.strategy != "plan_v1":
        console.print(f"[red]No facts. Run analyze-market first.[/]")
        raise SystemExit(1)

    # ── Run strategy ───────────────────────────────────────────────────────
    if args.strategy == "plan_v1":
        risk_params = {
            "atr_stop_mult": args.atr_stop_mult,
            "time_stop_bars": args.time_stop,
        }
        # plan_v1: always recompute facts inline from candle data (no lookahead)
        # External facts from DB are computed from "today" → future bias
        result = run_plan_backtest(
            df,
            facts=None,
            risk_params=risk_params,
            fee_bps=args.fee_bps,
        )
        metrics = result["metrics"]
        trade_log = result.get("trade_log", [])

    else:
        params = {"zone_mult": args.zone_mult}
        signals = generate_signals(df, facts, interval=args.interval, params=params)
        metrics = run_vectorized_backtest(df, signals, fee_bps=args.fee_bps)
        trade_log = []

    # ── Save ───────────────────────────────────────────────────────────────
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
            params={"fee_bps": args.fee_bps, "zone_mult": args.zone_mult},
            facts_version=args.facts_version,
            metrics=metrics,
        )
        console.print(f"[dim]Run saved: {run_id}[/]")

    con.close()

    # ── JSON mode ──────────────────────────────────────────────────────────
    if args.json_mode:
        output = {"metrics": metrics}
        if trade_log:
            output["trade_log"] = trade_log
        json.dump(output, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    # ── Rich output ────────────────────────────────────────────────────────
    tbl = Table(
        title=(
            f"Backtest: {args.symbol} {args.interval}  "
            f"{start_dt.date()} → {end_dt.date()}  "
            f"strategy={args.strategy}"
        ),
        show_header=False,
    )
    tbl.add_column("Metric", style="bold cyan", width=22)
    tbl.add_column("Value", style="white")

    tbl.add_row("Bias", metrics.get("bias", "-"))
    tbl.add_row("Bars", str(metrics.get("bars", 0)))
    tbl.add_row("Trades", str(metrics.get("trades", 0)))
    tbl.add_row("Total Return", f"{metrics.get('total_return_pct', 0):.2f}%")
    tbl.add_row("Max Drawdown", f"{metrics.get('max_drawdown_pct', 0):.2f}%")

    if "sharpe" in metrics:
        tbl.add_row("Sharpe (ann.)", f"{metrics['sharpe']:.3f}")
    if "win_rate_pct" in metrics:
        tbl.add_row("Win Rate", f"{metrics['win_rate_pct']:.1f}%")
    if "profit_factor" in metrics:
        tbl.add_row("Profit Factor", f"{metrics['profit_factor']:.3f}")
    if "avg_win_pct" in metrics:
        tbl.add_row("Avg Win", f"{metrics['avg_win_pct']:.2f}%")
    if "avg_loss_pct" in metrics:
        tbl.add_row("Avg Loss", f"{metrics['avg_loss_pct']:.2f}%")

    tbl.add_row("Fee (bps)", str(args.fee_bps))
    tbl.add_row("Facts version", args.facts_version)
    console.print(tbl)

    # ── Trade log ──────────────────────────────────────────────────────────
    if args.show_trades and trade_log:
        tbl = Table(title="Trade Log", show_header=True)
        tbl.add_column("#", width=3)
        tbl.add_column("Side", width=6)
        tbl.add_column("Entry", width=12)
        tbl.add_column("Exit", width=12)
        tbl.add_column("PnL%", width=8)
        tbl.add_column("Reason", width=14)
        tbl.add_column("Bars", width=5)
        for i, t in enumerate(trade_log, 1):
            color = "green" if t["pnl_pct"] > 0 else "red"
            tbl.add_row(
                str(i),
                t["side"],
                f"{t['entry_price']:,.0f}",
                f"{t['exit_price']:,.0f}",
                f"[{color}]{t['pnl_pct']:.2f}%[/]",
                t["reason"],
                str(t["bars"]),
            )
        console.print(tbl)


if __name__ == "__main__":
    main()
