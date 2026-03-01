#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$SCRIPT_DIR"
VENV_DIR="$ROOT_DIR/.venv"
PY_BIN="$VENV_DIR/bin/python"
PIP_BIN="$VENV_DIR/bin/pip"

COMMANDS=(sync-klines analyze-market get-latest-facts plan-trade backtest-facts run-experiments walk-forward dashboard)

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args...]
Commands: ${COMMANDS[*]}
Examples:
  $(basename "$0") sync-klines --start 2024-01-01
  $(basename "$0") analyze-market --intervals 1h,4h,1d,1w
  $(basename "$0") get-latest-facts --json
  $(basename "$0") plan-trade --explain
  $(basename "$0") backtest-facts --start 2025-01-01
Options: -h, --help (show help), --force-install (reinstall)
EOF
}

[[ $# -eq 0 ]] && { usage; exit 1; }

COMMAND="$1"
shift

[[ "$COMMAND" == "-h" || "$COMMAND" == "--help" ]] && { usage; exit 0; }

FORCE_INSTALL=0
ARGS=()
for arg in "$@"; do
  [[ "$arg" == "--force-install" ]] && FORCE_INSTALL=1 || ARGS+=("$arg")
done

[[ " ${COMMANDS[*]} " == *" $COMMAND "* ]] || { echo "Error: unknown command '$COMMAND'" >&2; usage; exit 1; }

[[ ! -x "$PY_BIN" ]] && { echo "[wrapper] Creating venv..."; python3 -m venv "$VENV_DIR"; }

CMD_BIN="$VENV_DIR/bin/$COMMAND"
[[ ! -x "$CMD_BIN" || "$FORCE_INSTALL" -eq 1 ]] && { echo "[wrapper] Installing..."; "$PIP_BIN" install -e "$ROOT_DIR" -q; }

cd "$ROOT_DIR"
exec "$CMD_BIN" ${ARGS[@]+"${ARGS[@]}"}
