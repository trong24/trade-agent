# SKILL: trade-agent

## Purpose
Run fast, repeatable backtests with local `trade-agent/` and generate a standardized Markdown report.

## Included Files
- `skills/trade-agent/wrapper.sh` — thin runner around `trade-agent` CLI
- `skills/trade-agent/reports/report-template.md` — report template with placeholders

## When To Use
- User asks to backtest a strategy from OHLCV CSV
- Need a consistent params/results summary
- Need a shareable artifact (`.md` report)

## Input Contract
**Required**
- `--csv <path>`: path to OHLCV CSV (absolute or workspace-relative)

**Optional**
- `--short <int>` (default: `20`)
- `--long <int>` (default: `50`)
- `--risk <float>` (default: `0.2`)
- `--initial-cash <float>` (default: `10000`)
- `--fee-bps <float>` (default: `6`)
- `--report-out <path>`: output report path
- `--notes <text>`: freeform notes included in report
- `--force-install`: force reinstall editable package

## Command
```bash
./skills/trade-agent/wrapper.sh \
  --csv trade-agent/data/sample_ohlcv.csv \
  --short 20 --long 50 --risk 0.2 \
  --initial-cash 10000 --fee-bps 6 \
  --report-out skills/trade-agent/reports/latest.md
```

## Output Contract
1. Prints raw backtest summary to stdout (from `trade-agent` CLI)
2. If `--report-out` is provided, writes a Markdown report rendered from template

## Guardrails
- **Research / paper only** (no live order execution)
- Validate `short < long`
- CSV header must be:
  `timestamp,open,high,low,close,volume`

## Notes for ZeroClaw
- Wrapper auto-bootstraps `.venv` if missing
- Uses local project at `trade-agent/`
- Keep report concise and reproducible (always show params + metrics)
