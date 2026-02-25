#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="$ROOT_DIR/trade-agent"
VENV_DIR="$PROJECT_DIR/.venv"
TRADE_BIN="$VENV_DIR/bin/trade-agent"
PIP_BIN="$VENV_DIR/bin/pip"
PY_BIN="$VENV_DIR/bin/python"
TEMPLATE_PATH="$SCRIPT_DIR/reports/report-template.md"

SHORT=20
LONG=50
RISK=0.2
INITIAL_CASH=10000
FEE_BPS=6
CSV_PATH=""
REPORT_OUT=""
NOTES=""
FORCE_INSTALL=0

usage() {
  cat <<EOF
Usage: $(basename "$0") --csv <path> [options]

Required:
  --csv <path>             Path to OHLCV CSV (absolute or workspace-relative)

Optional:
  --short <int>            Short SMA window (default: ${SHORT})
  --long <int>             Long SMA window (default: ${LONG})
  --risk <float>           Max equity fraction per entry (default: ${RISK})
  --initial-cash <float>   Initial cash (default: ${INITIAL_CASH})
  --fee-bps <float>        Fee in bps (default: ${FEE_BPS})
  --report-out <path>      Output markdown report path
  --notes <text>           Extra notes included in report
  --force-install          Force 'pip install -e trade-agent'
  -h, --help               Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --csv)
      CSV_PATH="${2:-}"
      shift 2
      ;;
    --short)
      SHORT="${2:-}"
      shift 2
      ;;
    --long)
      LONG="${2:-}"
      shift 2
      ;;
    --risk)
      RISK="${2:-}"
      shift 2
      ;;
    --initial-cash)
      INITIAL_CASH="${2:-}"
      shift 2
      ;;
    --fee-bps)
      FEE_BPS="${2:-}"
      shift 2
      ;;
    --report-out)
      REPORT_OUT="${2:-}"
      shift 2
      ;;
    --notes)
      NOTES="${2:-}"
      shift 2
      ;;
    --force-install)
      FORCE_INSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$CSV_PATH" ]]; then
  echo "Error: --csv is required" >&2
  usage
  exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Error: project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

if [[ "$CSV_PATH" != /* ]]; then
  CSV_PATH="$ROOT_DIR/$CSV_PATH"
fi

if [[ ! -f "$CSV_PATH" ]]; then
  echo "Error: CSV file not found: $CSV_PATH" >&2
  exit 1
fi

if (( SHORT >= LONG )); then
  echo "Error: --short must be smaller than --long" >&2
  exit 1
fi

if [[ ! -x "$PY_BIN" ]]; then
  echo "[trade-agent wrapper] Creating virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

if [[ ! -x "$TRADE_BIN" || "$FORCE_INSTALL" -eq 1 ]]; then
  echo "[trade-agent wrapper] Installing package (editable)..."
  "$PIP_BIN" install -e "$PROJECT_DIR" >/dev/null
fi

RAW_OUTPUT="$($TRADE_BIN \
  --csv "$CSV_PATH" \
  --short "$SHORT" \
  --long "$LONG" \
  --risk "$RISK" \
  --initial-cash "$INITIAL_CASH" \
  --fee-bps "$FEE_BPS")"

printf '%s\n' "$RAW_OUTPUT"

if [[ -n "$REPORT_OUT" ]]; then
  if [[ "$REPORT_OUT" != /* ]]; then
    REPORT_OUT="$ROOT_DIR/$REPORT_OUT"
  fi

  mkdir -p "$(dirname "$REPORT_OUT")"

  RAW_OUTPUT_ENV="$RAW_OUTPUT" python3 - "$TEMPLATE_PATH" "$REPORT_OUT" "$CSV_PATH" "$SHORT" "$LONG" "$RISK" "$INITIAL_CASH" "$FEE_BPS" "$NOTES" <<'PY'
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

template_path, report_out, csv_path, short_w, long_w, risk, initial_cash, fee_bps, notes = sys.argv[1:]
raw = os.environ.get("RAW_OUTPUT_ENV", "")
template = Path(template_path).read_text(encoding="utf-8")

def pick(pattern: str, default: str = "N/A") -> str:
    m = re.search(pattern, raw, flags=re.MULTILINE)
    return m.group(1).strip() if m else default

values = {
    "RUN_AT": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "CSV_PATH": csv_path,
    "SHORT_WINDOW": short_w,
    "LONG_WINDOW": long_w,
    "RISK": risk,
    "INITIAL_CASH": initial_cash,
    "FEE_BPS": fee_bps,
    "INITIAL_CASH_RESULT": pick(r"Initial Cash\s*:\s*(.+)"),
    "FINAL_EQUITY": pick(r"Final Equity\s*:\s*(.+)"),
    "RETURN_PCT": pick(r"Return\s*:\s*(.+)"),
    "MAX_DRAWDOWN_PCT": pick(r"Max Drawdown\s*:\s*(.+)"),
    "TOTAL_FEES": pick(r"Total Fees\s*:\s*(.+)"),
    "TRADES": pick(r"Trades\s*:\s*(.+)"),
    "WINS_LOSSES": pick(r"Wins / Losses\s*:\s*(.+)"),
    "WIN_RATE_PCT": pick(r"Win Rate\s*:\s*(.+)"),
    "RAW_OUTPUT": raw.strip(),
    "NOTES": notes.strip() if notes.strip() else "-",
}

for key, value in values.items():
    template = template.replace(f"{{{{{key}}}}}", str(value))

Path(report_out).write_text(template, encoding="utf-8")
PY

  echo
  echo "Report written: $REPORT_OUT"
fi
