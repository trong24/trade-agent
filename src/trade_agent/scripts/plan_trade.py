"""CLI: generate trade plan from latest market facts.

Usage:
    plan-trade                         # Rich table
    plan-trade --json                  # raw JSON (pipe to LLM)
    plan-trade --explain               # with evidence lines
    plan-trade --json --explain        # JSON with evidence embedded
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from trade_agent.analysis.explainer import explain_plan
from trade_agent.analysis.plan_builder import build_plan
from trade_agent.db import connect, init_db, read_latest_facts

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate trade plan from latest market facts",
    )
    p.add_argument("--db",      default="data/trade.duckdb")
    p.add_argument("--symbol",  default="BTCUSDT")
    p.add_argument("--version", default="v1")
    p.add_argument("--json",    action="store_true", dest="json_mode",
                   help="Output raw JSON")
    p.add_argument("--explain", action="store_true",
                   help="Include evidence lines")
    p.add_argument("--atr-stop-mult", type=float, default=1.5)
    p.add_argument("--min-rr",        type=float, default=2.0)
    p.add_argument("--time-stop",     type=int,   default=20)
    return p


def main() -> None:
    args = build_parser().parse_args()

    con = connect(args.db)
    init_db(con)

    facts = read_latest_facts(con, args.symbol, "ALL", version=args.version)
    con.close()

    if facts is None:
        msg = f"No facts for {args.symbol}. Run: analyze-market first."
        if args.json_mode:
            json.dump({"error": msg}, sys.stdout)
        else:
            console.print(f"[red]{msg}[/]")
        sys.exit(1)

    risk_params = {
        "atr_stop_mult":  args.atr_stop_mult,
        "min_rr":         args.min_rr,
        "time_stop_bars": args.time_stop,
    }
    plan = build_plan(facts, risk_params=risk_params)

    # Add evidence if requested
    evidence: list[str] = []
    if args.explain:
        evidence = explain_plan(facts, plan)
        plan["evidence"] = evidence

    # ── JSON mode ──────────────────────────────────────────────────────────
    if args.json_mode:
        json.dump(plan, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    # ── Rich table mode ────────────────────────────────────────────────────
    console.print(
        f"\n[bold cyan]{plan['symbol']}[/] Trade Plan — "
        f"{plan.get('as_of', '?')}"
    )
    console.print(
        f"Price: [bold]{plan['current_price']:,.2f}[/]  "
        f"Regime: [bold]{plan['regime']}[/]  "
        f"Bias: [bold]{plan['primary_bias']}[/]  "
        f"Score: [bold]{plan.get('plan_score', '?')}[/]/100"
    )
    if plan.get("no_trade_flag"):
        console.print("  [bold red]⛔ NO TRADE — score too low, skip this setup[/]")


    # Scenarios
    tbl = Table(title="Scenarios", show_header=True)
    tbl.add_column("Scenario", style="cyan", width=6)
    tbl.add_column("Condition", width=40)
    tbl.add_column("Target", width=12)
    tbl.add_column("Prob", width=8)
    for name, sc in plan.get("scenarios", {}).items():
        color = {"bull": "green", "bear": "red"}.get(name, "yellow")
        tbl.add_row(
            f"[{color}]{name}[/]",
            sc.get("condition", ""),
            f"{sc.get('target', 0):,.2f}",
            sc.get("probability", "?"),
        )
    console.print(tbl)

    # Entry rules
    for e in plan.get("entry_rules", []):
        color = "green" if e["type"] == "long" else "red" if e["type"] == "short" else "yellow"
        console.print(
            f"  [{color}]ENTRY ({e['type']})[/]: {e['trigger']}"
        )
        console.print(f"    Condition: {e['condition']}")

    # Stop
    stop = plan.get("stop", {})
    console.print(
        f"  [red]STOP[/]: {stop.get('price', '?'):,} "
        f"({stop.get('method', '?')}, distance={stop.get('distance', '?')})"
    )

    # Targets
    targets = plan.get("targets", [])
    if targets:
        tbl = Table(title="Targets", show_header=True)
        tbl.add_column("TP", width=4)
        tbl.add_column("Price", width=12)
        tbl.add_column("R:R", width=6)
        tbl.add_column("Source", width=6)
        for t in targets:
            tbl.add_row(
                str(t["tp"]),
                f"{t['price']:,.2f}",
                f"{t.get('rr', 0):.1f}",
                t.get("source", "?"),
            )
        console.print(tbl)

    # No-trade warnings
    for nt in plan.get("no_trade", []):
        console.print(f"  [yellow]⚠️ {nt}[/]")

    # Evidence
    if evidence:
        console.print()
        console.print(Panel(
            "\n".join(evidence),
            title="Evidence",
            border_style="dim",
        ))


if __name__ == "__main__":
    main()
