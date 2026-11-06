#!/usr/bin/env bash
# End-to-end paper-trading demo: starts the service, submits orders, prints
# results, and cleans up.  Safe: uses the paper broker only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
PORT="${PORT:-8765}"
TMP="$(mktemp -d)"
CONFIG="$TMP/config.toml"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export ETS_CONFIG="$CONFIG"

echo "== init-config =="
"$PYTHON" -m easytrader_service init-config --output "$CONFIG"

# The demo may run outside market hours and is T+1; disable both for a
# self-contained demonstration while keeping the paper broker and fees.
sed -i 's/enforce_trading_session = true/enforce_trading_session = false/' "$CONFIG"
sed -i 's/t_plus_one = true/t_plus_one = false/' "$CONFIG"
sed -i "s/port = 8000/port = $PORT/" "$CONFIG"

echo
echo "== serve =="
"$PYTHON" -m easytrader_service serve --config "$CONFIG" --host 127.0.0.1 \
  >"$TMP/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 100); do
  if "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/v1/health", timeout=1)
PY
  then
    break
  fi
  sleep 0.1
done

echo
echo "== submit buy =="
"$PYTHON" -m easytrader_service --config "$CONFIG" submit \
  --symbol 000001 --side buy --price 10.50 --quantity 100 --client-order-id demo-buy-1

echo
echo "== submit same client_order_id again (idempotent) =="
"$PYTHON" -m easytrader_service --config "$CONFIG" submit \
  --symbol 000001 --side buy --price 10.50 --quantity 100 --client-order-id demo-buy-1

echo
echo "== submit sell =="
"$PYTHON" -m easytrader_service --config "$CONFIG" submit \
  --symbol 000001 --side sell --price 10.80 --quantity 100 --client-order-id demo-sell-1

echo
echo "== orders =="
"$PYTHON" -m easytrader_service --config "$CONFIG" orders --limit 10

echo
echo "== account =="
"$PYTHON" -m easytrader_service --config "$CONFIG" account

echo
echo "== status =="
"$PYTHON" -m easytrader_service --config "$CONFIG" status
