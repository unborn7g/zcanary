#!/usr/bin/env bash
# Capture a setup command's output and show only a redacted tail if it fails.
redact_diagnostic() {
  python3 -c 'import os,re,sys
text=sys.stdin.read()
for name,secret in os.environ.items():
    if secret and any(part in name.upper() for part in ("PASSWORD", "PASS", "TOKEN", "SECRET", "SEED", "PRIVATE_KEY", "MEMO")):
        replacement="<canary>" if "MEMO" in name.upper() else "<redacted>"
        text=text.replace(secret, replacement)
        encoded=secret.encode("utf-8").hex()
        text=text.replace(encoded, replacement).replace(encoded.upper(), replacement)
text=re.sub(r"ZC-[MLT]-[A-F0-9]{24}", "<canary>", text, flags=re.IGNORECASE)
text=re.sub(r"5a432d(?:4d|4c|54)2d(?:3[0-9]|4[1-6]){24}", "<canary>", text, flags=re.IGNORECASE)
sys.stdout.write(text)'
}

run_with_tail() {
  local label="$1"
  shift
  local log_file status
  log_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-output.XXXXXX") || return 1
  if "$@" >"$log_file" 2>&1; then
    rm -f "$log_file"
    return 0
  else
    status=$?
    printf '==> %s failed (exit %s); last 20 output lines follow\n' "$label" "$status" >&2
    tail -n 20 -- "$log_file" | redact_diagnostic >&2
    rm -f "$log_file"
    return "$status"
  fi
}
