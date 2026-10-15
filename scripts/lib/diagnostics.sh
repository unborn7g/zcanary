#!/usr/bin/env bash
# Capture setup output and show only a redacted tail when a command fails.
redact_diagnostic() {
  python3 -c 'import base64,os,re,sys
text=sys.stdin.read()
for name,secret in os.environ.items():
    upper=name.upper()
    if secret and any(part in upper for part in ("PASSWORD", "PASS", "TOKEN", "SECRET", "SEED", "PRIVATE_KEY", "MEMO", "AUTH_HASH", "PWHASH")):
        replacement="<canary>" if "MEMO" in upper else "<redacted>"
        encoded=secret.encode("utf-8")
        for value in (secret, encoded.hex(), encoded.hex().upper(), base64.b64encode(encoded).decode("ascii")):
            text=text.replace(value, replacement)
text=re.sub(r"(?i)(authorization\s*:\s*basic\s+)[A-Za-z0-9+/=]+", r"\1<redacted>", text)
text=re.sub(r"(?i)\b[0-9a-f]{32}\$[0-9a-f]{64}\b", "<redacted-auth-hash>", text)
text=re.sub(r"ZC-[MLT]-[A-F0-9]{24}", "<canary>", text, flags=re.IGNORECASE)
text=re.sub(r"5a432d(?:4d|4c|54)2d(?:3[0-9]|4[1-6]){24}", "<canary>", text, flags=re.IGNORECASE)
address_pattern=r"\b(?:u1|utest1|uregtest1|zs1|ztestsapling1|zregtestsapling1|t1|t3|tm|t2)[0-9a-z]{20,}\b"
def shorten_address(match):
    address=match.group(0)
    return f"{address[:12]}…{address[-6:]}"
text=re.sub(address_pattern, shorten_address, text, flags=re.IGNORECASE)
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

append_redacted_tail() {
  local output_file="$1" label="$2" working_dir="$3"
  shift 3
  local log_file status
  mkdir -p "$(dirname "$output_file")"
  log_file=$(mktemp "${TMPDIR:-/tmp}/zcanary-diagnostics.XXXXXX") || return 0
  if (cd "$working_dir" && "$@") >"$log_file" 2>&1; then
    status=0
  else
    status=$?
  fi
  {
    printf '==> %s (exit %s); last 80 output lines follow\n' "$label" "$status"
    tail -n 80 -- "$log_file" | redact_diagnostic
  } | tee -a "$output_file" >&2 || true
  rm -f "$log_file"
  return 0
}
