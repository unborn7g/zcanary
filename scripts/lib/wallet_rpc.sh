#!/usr/bin/env bash
# Wallet RPC wrapper that preserves curl's status and avoids parsing failed bodies.
rpc_wallet() {
  local method="${1:-unknown}" response status
  if response=$(RPC_USER="$ZCANARY_ZALLET_USER" RPC_PASS="$ZCANARY_ZALLET_PASSWORD" \
    RPC_MAX_TIME="${WALLET_RPC_MAX_TIME:-${RPC_MAX_TIME:-20}}" "$RPC" "$ROUTER" "$@"); then
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
  WALLET_RPC_LAST_CURL_EXIT=0
  WALLET_RPC_LAST_HTTP_STATUS=unknown
  WALLET_RPC_LAST_REASON=rpc-error
  if response=$(rpc_wallet z_listaccounts 2>&1); then
    WALLET_RPC_LAST_CURL_EXIT=0
    WALLET_RPC_LAST_HTTP_STATUS=200
    if printf '%s\n' "$response" | python3 -c 'import json,sys
try:
    payload=json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    sys.exit(1)
if not isinstance(payload, dict) or payload.get("error") is not None or not isinstance(payload.get("result"), list):
    sys.exit(1)'; then
      WALLET_RPC_LAST_REASON=ready
      return 0
    else
      status=$?
      WALLET_RPC_LAST_REASON=response-not-ready
      return "$status"
    fi
  else
    status=$?
    WALLET_RPC_LAST_CURL_EXIT=$status
    if [[ "$response" =~ curl[[:space:]]exit[[:space:]]([0-9]+) ]]; then
      WALLET_RPC_LAST_CURL_EXIT=${BASH_REMATCH[1]}
    fi
    if [[ "$response" =~ received[[:space:]]([0-9]{3}) ]]; then
      WALLET_RPC_LAST_HTTP_STATUS=${BASH_REMATCH[1]}
    fi
    case "$status" in
      28) WALLET_RPC_LAST_REASON=timeout ;;
      22) WALLET_RPC_LAST_REASON=http-error ;;
      6) WALLET_RPC_LAST_REASON=dns-error ;;
      7) WALLET_RPC_LAST_REASON=connect-error ;;
      *) WALLET_RPC_LAST_REASON=rpc-error ;;
    esac
    return "$status"
  fi
}
