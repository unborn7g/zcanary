#!/usr/bin/env bash
# Wallet RPC wrapper that preserves curl's status and avoids parsing failed bodies.
rpc_wallet() {
  local method="${1:-unknown}" response status
  if response=$(RPC_USER="$ZCANARY_ZALLET_USER" RPC_PASS="$ZCANARY_ZALLET_PASSWORD" "$RPC" "$ROUTER" "$@"); then
    printf '%s\n' "$response"
  else
    status=$?
    if [ "$status" -eq 22 ]; then
      printf 'wallet RPC %s failed: HTTP error (curl exit 22)\n' "$method" >&2
    else
      printf 'wallet RPC %s failed (exit %s)\n' "$method" "$status" >&2
    fi
    if [ "${WALLET_RPC_READY:-0}" = 1 ] && declare -F capture_wallet_startup_diagnostics >/dev/null; then
      capture_wallet_startup_diagnostics
    fi
    return "$status"
  fi
}

rpc_wallet_j() {
  local expression="$1" response status
  shift
  if response=$(rpc_wallet "$@"); then
    printf '%s\n' "$response" | j "$expression"
  else
    status=$?
    return "$status"
  fi
}

wallet_accounts_ready() {
  local response status
  if response=$(rpc_wallet z_listaccounts); then
    printf '%s\n' "$response" | python3 -c 'import json,sys
try:
    payload=json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    sys.exit(1)
if not isinstance(payload, dict) or "error" not in payload or payload["error"] is not None or not isinstance(payload.get("result"), list):
    sys.exit(1)'
  else
    status=$?
    return "$status"
  fi
}
