"""CLI: walk-forward analysis with rolling train/test windows.

Performs rolling walk-forward:
  1. For each window: analyze candles in train period → compute facts
  2. Backtest on test period using those facts
  3. Report stability across all windows

Usage:
    walk-forward --start 2024-06-01 --end 2026-01-01
    walk-forward --start 2024-06-01 --train-days 180 --test-days 60 --step-days 30
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from trade_agent.analysis.payload import build_payload
from trade_agent.analysis.sr import compute_sr
from trade_agent.analysis.trend import compute_trend
from trade_agent.backtest.facts_strategy import generate_signals, run_vectorized_backtest
from trade_agent.db import connect, init_db, read_candles

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Walk-forward: rolling train→test windows for stability analysis",
    )
    p.add_argument("--db",            default="data/trade.duckdb")
    p.add_argument("--symbol",        default="BTCUSDT")
    p.add_argument("--interval",      default="1h")
    p.add_argument("--start",         required=True)
    p.add_argument("--end",           default=None)
    p.add_argument("--train-days",    type=int, default=180)
    p.add_argument("--test-days",     type=int, default=60)
    p.add_argument("--step-days",     type=int, default=30)
    p.add_argument("--zone-mult",     type=float, default=1.5)
    p.add_argument("--fee-bps",       type=float, default=2.0)
    p.add_argument("--lookback",      type=int, default=500,
                   help="Max bars for SR/trend analysis in train window")
    p.add_argument("--analyze-tfs",   default="1h,4h,1d",
                   help="TFs to analyze during each train window")
    return p


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _analyze_window(
    con, symbol: str, tfs: list[str],
    train_end: datetime, lookback: int,
) -> dict:
    """Run trend+SR analysis on train data up to train_end."""
    per_tf: dict = {}
    for tf in tfs:
        df = read_candles(con, symbol, tf)
        if df.empty:
            continue
        # Filter up to train_end and take last N bars
        df = df[df.index <= train_end].iloc[-lookback:]
        if len(df) < 20:
            continue
        per_tf[tf] = {
            "trend": compute_trend(df),
            "sr":    compute_sr(df),
        }
    if not per_tf:
        return {}
    return build_payload(symbol, train_end, per_tf)


def _classify_regime(facts: dict) -> str:
    """Extract regime from facts for regime-split reporting."""
    return facts.get("regime", "unknown")


def main() -> None:
    args = build_parser().parse_args()

    start_dt = _parse_dt(args.start)
    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)
    tfs = [t.strip() for t in args.analyze_tfs.split(",")]

    con = connect(args.db)
    init_db(con)

    # Generate windows
    windows: list[tuple[datetime, datetime, datetime, datetime]] = []
    cursor = start_dt
    while cursor + timedelta(days=args.train_days + args.test_days) <= end_dt:
        train_start = cursor
        train_end = cursor + timedelta(days=args.train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=args.test_days)
        windows.append((train_start, train_end, test_start, test_end))
        cursor += timedelta(days=args.step_days)

    if not windows:
        console.print("[red]Not enough data for even one window.[/]")
        raise SystemExit(1)

    console.print(
        f"[cyan]Walk-forward[/]: {len(windows)} windows  "
        f"train={args.train_days}d  test={args.test_days}d  "
        f"step={args.step_days}d\n"
    )

    results: list[dict] = []

    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows, 1):
        # 1. Analyze on train data
        facts = _analyze_window(con, args.symbol, tfs, tr_e, args.lookback)
        if not facts:
            results.append({
                "window": i, "train": f"{tr_s.date()}→{tr_e.date()}",
                "test": f"{te_s.date()}→{te_e.date()}",
                "return_pct": 0, "max_dd_pct": 0, "sharpe": 0,
                "trades": 0, "regime": "no_data",
            })
            continue

        # 2. Backtest on test data
        df_test = read_candles(
            con, args.symbol, args.interval,
            start=te_s, end=te_e,
        )
        if df_test.empty or len(df_test) < 5:
            results.append({
                "window": i, "train": f"{tr_s.date()}→{tr_e.date()}",
                "test": f"{te_s.date()}→{te_e.date()}",
                "return_pct": 0, "max_dd_pct": 0, "sharpe": 0,
                "trades": 0, "regime": "no_data",
            })
            continue

        signals = generate_signals(
            df_test, facts, interval=args.interval,
            params={"zone_mult": args.zone_mult},
        )
        metrics = run_vectorized_backtest(df_test, signals, fee_bps=args.fee_bps)
        regime = _classify_regime(facts)

        results.append({
            "window":     i,
            "train":      f"{tr_s.date()}→{tr_e.date()}",
            "test":       f"{te_s.date()}→{te_e.date()}",
            "return_pct": metrics["total_return_pct"],
            "max_dd_pct": metrics["max_drawdown_pct"],
            "sharpe":     metrics["sharpe"],
            "trades":     metrics["trades"],
            "regime":     regime,
        })

    con.close()

    # ── Per-window table ───────────────────────────────────────────────────
    tbl = Table(title="Walk-Forward Results", show_header=True)
    tbl.add_column("#", width=3)
    tbl.add_column("Train", width=24)
    tbl.add_column("Test", width=24)
    tbl.add_column("Regime", width=12)
    tbl.add_column("Return%", width=10)
    tbl.add_column("MaxDD%", width=10)
    tbl.add_column("Sharpe", width=8)
    tbl.add_column("Trades", width=7)

    for r in results:
        ret = r["return_pct"]
        color = "green" if ret > 0 else "red" if ret < 0 else "white"
        tbl.add_row(
            str(r["window"]),
            r["train"],
            r["test"],
            r["regime"],
            f"[{color}]{ret:.2f}%[/]",
            f"{r['max_dd_pct']:.2f}%",
            f"{r['sharpe']:.3f}",
            str(r["trades"]),
        )
    console.print(tbl)

    # ── Stability summary ──────────────────────────────────────────────────
    returns = [r["return_pct"] for r in results]
    dds = [r["max_dd_pct"] for r in results]
    sharpes = [r["sharpe"] for r in results]
    trades = [r["trades"] for r in results]

    profitable = sum(1 for r in returns if r > 0)
    total_w = len(results)

    console.print()
    stbl = Table(title="Stability Summary", show_header=False)
    stbl.add_column("Metric", style="bold cyan", width=24)
    stbl.add_column("Value", style="white")

    stbl.add_row("Windows",          str(total_w))
    stbl.add_row("Profitable",       f"{profitable}/{total_w} ({profitable/max(total_w,1)*100:.0f}%)")
    stbl.add_row("Median Return",    f"{sorted(returns)[len(returns)//2]:.2f}%")
    stbl.add_row("Mean Return",      f"{sum(returns)/max(len(returns),1):.2f}%")
    stbl.add_row("Worst Window",     f"{min(returns):.2f}%")
    stbl.add_row("Best Window",      f"{max(returns):.2f}%")
    stbl.add_row("Worst MaxDD",      f"{min(dds):.2f}%")
    stbl.add_row("Median Sharpe",    f"{sorted(sharpes)[len(sharpes)//2]:.3f}")
    stbl.add_row("Mean Trades/Win",  f"{sum(trades)/max(total_w,1):.1f}")
    console.print(stbl)

    # ── Regime split ───────────────────────────────────────────────────────
    regime_groups: dict[str, list[dict]] = {}
    for r in results:
        regime_groups.setdefault(r["regime"], []).append(r)

    if len(regime_groups) > 1:
        rtbl = Table(title="Performance by Regime", show_header=True)
        rtbl.add_column("Regime", style="cyan", width=14)
        rtbl.add_column("Windows", width=8)
        rtbl.add_column("Avg Return%", width=12)
        rtbl.add_column("Avg Sharpe", width=10)
        rtbl.add_column("Worst DD%", width=10)

        for regime, group in sorted(regime_groups.items()):
            avg_ret = sum(r["return_pct"] for r in group) / len(group)
            avg_sh = sum(r["sharpe"] for r in group) / len(group)
            worst = min(r["max_dd_pct"] for r in group)
            rtbl.add_row(
                regime,
                str(len(group)),
                f"{avg_ret:.2f}%",
                f"{avg_sh:.3f}",
                f"{worst:.2f}%",
            )
        console.print(rtbl)


if __name__ == "__main__":
    main()
