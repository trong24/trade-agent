#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WRAPPER="$ROOT_DIR/skills/trade-agent/wrapper.sh"

"$WRAPPER" sync-klines --start 2024-01-01
"$WRAPPER" analyze-market --intervals 15m,1h,4h,1d,1w
"$WRAPPER" get-latest-facts --json
