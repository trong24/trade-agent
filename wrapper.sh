#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# trade-agent wrapper — thin runner for all trade-agent CLI commands
# Usage:  ./wrapper.sh <command> [args...]
#
# Commands:
#   sync-klines      Sync klines from Binance → DuckDB
#   analyze-market   Compute trend/SR facts
#   get-latest-facts Get latest facts JSON
#   plan-trade       Generate trade plan
#   backtest-facts   Run backtest
#   run-experiments  Grid search experiments
#   walk-forward     Walk-forward stability analysis
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
VENV_DIR="$ROOT_DIR/.venv"
PIP_BIN="$VENV_DIR/bin/pip"
PY_BIN="$VENV_DIR/bin/python"

COMMANDS=(
  sync-klines
  analyze-market
  get-latest-facts
  plan-trade
  backtest-facts
  run-experiments
  walk-forward
)

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args...]

Commands:
  sync-klines        Sync klines from Binance into DuckDB
  analyze-market     Compute trend + S/R facts (versioned)
  get-latest-facts   Output latest facts JSON for LLM
  plan-trade         Generate trade plan with evidence
  backtest-facts     Run vectorized or plan-based backtest
  run-experiments    Grid search param combos
  walk-forward       Rolling train/test stability analysis

Examples:
  $(basename "$0") sync-klines --start 2024-01-01
  $(basename "$0") analyze-market --intervals 1h,4h,1d,1w
  $(basename "$0") get-latest-facts --json
  $(basename "$0") plan-trade --explain
  $(basename "$0") backtest-facts --start 2025-01-01 --fee-bps 2.0
  $(basename "$0") run-experiments --start 2025-01-01 --interval 1h
  $(basename "$0") walk-forward --start 2024-06-01

Options:
  -h, --help         Show this help
  --force-install    Force reinstall editable package
EOF
}

# ── Check args ─────────────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

COMMAND="$1"
shift

if [[ "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
  usage
  exit 0
fi

# Handle --force-install anywhere in args
FORCE_INSTALL=0
REMAINING_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--force-install" ]]; then
    FORCE_INSTALL=1
  else
    REMAINING_ARGS+=("$arg")
  fi
done

# ── Validate command ───────────────────────────────────────────────────────
VALID=0
for cmd in "${COMMANDS[@]}"; do
  if [[ "$COMMAND" == "$cmd" ]]; then
    VALID=1
    break
  fi
done

if [[ "$VALID" -eq 0 ]]; then
  echo "Error: unknown command '$COMMAND'" >&2
  echo ""
  usage
  exit 1
fi

# ── Bootstrap venv ─────────────────────────────────────────────────────────
if [[ ! -x "$PY_BIN" ]]; then
  echo "[trade-agent wrapper] Creating virtualenv..."
  python3 -m venv "$VENV_DIR"
fi

CMD_BIN="$VENV_DIR/bin/$COMMAND"

if [[ ! -x "$CMD_BIN" || "$FORCE_INSTALL" -eq 1 ]]; then
  echo "[trade-agent wrapper] Installing package (editable)..."
  "$PIP_BIN" install -e "$ROOT_DIR" -q
fi

# ── Run ────────────────────────────────────────────────────────────────────
cd "$ROOT_DIR"
exec "$CMD_BIN" ${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}
