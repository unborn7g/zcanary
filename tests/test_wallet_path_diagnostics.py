from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_diagnostic_script_emits_seven_bounded_redacted_probes_and_ports(tmp_path):
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    assert script_path.is_file()
    script = script_path.read_text(encoding="utf-8")

    assert "wallet path probes" in script
    assert "--connect-timeout 5" in script
    assert "--max-time 10" in script
    assert "-sS" in script and "-o" in script
    assert "-w '%{http_code} %{time_total}'" in script
    for method in ("getblockchaininfo", "z_listaccounts"):
        assert method in script
    for endpoint in ("127.0.0.1:29232", "127.0.0.1:8181/health", "127.0.0.1:8181"):
        assert endpoint in script
    assert "docker compose --env-file .env.regtest port zallet 28232" in script
    assert "docker compose --env-file .env.regtest ps" in script
    assert "redact_diagnostic" in script
    assert "160" in script


def test_diagnostic_probes_use_actual_port_continue_after_timeout_and_redact_bodies(tmp_path):
    script_path = ROOT / "scripts" / "diagnose-wallet-path.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    compose_dir = tmp_path / "z3"
    compose_dir.mkdir()
    trace = tmp_path / "curl-trace.jsonl"
    output = tmp_path / "wallet-startup.log"
    full_address = "uregtest1abcdefghijklmnopqrstuvwxyz0123456789"
    node_password = "node-probe-password-not-a-secret"
    wallet_password = "wallet-probe-password-not-a-secret"
    router_password = "router-probe-password-not-a-secret"

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
url = args[-1]
output = Path(args[args.index("-o") + 1])
write_out = args[args.index("-w") + 1]
data = json.loads(args[args.index("-d") + 1]) if "-d" in args else None
method = data["method"] if data else "GET"
headers = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-H"]
record = {
    "url": url,
    "method": method,
    "auth": "--config" in args or "--user" in args or "-u" in args,
    "authorization_header": any(h.lower().startswith("authorization:") for h in headers),
    "connect_timeout": args[args.index("--connect-timeout") + 1],
    "max_time": args[args.index("--max-time") + 1],
    "write_out": write_out,
    "disable": args[0] == "--disable",
    "silent_show_error": "-sS" in args,
}
with open(os.environ["CURL_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(record) + "\\n")
body = (
    os.environ["FULL_ADDRESS"] + " " + os.environ["ZCANARY_NODE_PASSWORD"] + " "
    + os.environ["ZCANARY_ZALLET_PASSWORD"] + " "
    + os.environ["Z3_REGTEST_RPC_ROUTER_PASSWORD"] + " probe body"
)
output.write_text(body, encoding="utf-8")
if url == "http://127.0.0.1:8181" and method == "z_listaccounts" and record["auth"]:
    print("000 10.000", end="")
    sys.exit(28)
print("200 0.012", end="")
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if args[-3:] == ["port", "zallet", "28232"]:
    print("0.0.0.0:55032")
elif args[-1:] == ["ps"]:
    print("NAME SERVICE STATUS PORTS")
    print("z3-regtest-zallet-1 zallet Up 0.0.0.0:55032->28232/tcp")
else:
    sys.exit(2)
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CURL_TRACE": str(trace),
            "FULL_ADDRESS": full_address,
            "ZCANARY_DIAGNOSTICS_FILE": str(output),
            "ZCANARY_Z3_DIR": str(compose_dir),
            "ZCANARY_NODE_USER": "zebra",
            "ZCANARY_NODE_PASSWORD": node_password,
            "ZCANARY_ZALLET_USER": "zebra",
            "ZCANARY_ZALLET_PASSWORD": wallet_password,
            "Z3_REGTEST_RPC_ROUTER_USER": "router-user",
            "Z3_REGTEST_RPC_ROUTER_PASSWORD": router_password,
        }
    )
    proc = subprocess.run(
        ["bash", str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 7
    assert [record["url"] for record in records] == [
        "http://127.0.0.1:29232",
        "http://127.0.0.1:8181/health",
        "http://127.0.0.1:8181",
        "http://127.0.0.1:8181",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:55032",
        "http://127.0.0.1:8181",
    ]
    assert [record["auth"] for record in records] == [True, False, True, True, True, True, False]
    for record in records:
        assert record["connect_timeout"] == "5"
        assert record["max_time"] == "10"
        assert record["write_out"] == "%{http_code} %{time_total}"
        assert record["disable"] and record["silent_show_error"]
    assert not records[-1]["authorization_header"]

    rendered = proc.stdout + proc.stderr + output.read_text(encoding="utf-8")
    for secret in (node_password, wallet_password, router_password, full_address):
        assert secret not in rendered
    assert "wallet path probes" in rendered
    assert "docker compose --env-file .env.regtest ps" in rendered
    assert "55032->28232/tcp" in rendered
    assert re.search(r"Router -> Zallet.*curl exit=28.*seconds=10\.000", rendered)
    assert "uregtest1abc…456789" in rendered


def test_spike_calls_wallet_path_probe_once_in_first_failure_branch():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert script.count("diagnose-wallet-path.sh") == 1
    first_failure_match = re.search(
        r'if \[ "\$FIRST_READINESS_FAILURE_CAPTURED" -eq 0 \]; then(.*?)\n  fi\n  WAIT_ELAPSED=',
        script,
        re.DOTALL,
    )
    assert first_failure_match is not None
    assert "diagnose-wallet-path.sh" in first_failure_match.group(1)
    assert "FIRST_READINESS_FAILURE_CAPTURED=1" in first_failure_match.group(1)


def test_diagnose_run_generates_untracked_router_log_override():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert "docker-compose.regtest.override.yml" in script
    assert "RUST_LOG: info" in script
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.regtest.yml:docker-compose.regtest.override.yml" in script


def test_diagnose_workflow_is_manual_bounded_and_always_uploads_diagnostics():
    path = ROOT / ".github" / "workflows" / "diagnose.yml"
    assert path.is_file()
    workflow = path.read_text(encoding="utf-8")

    assert re.search(r"(?m)^on:\s*$", workflow)
    assert "workflow_dispatch:" in workflow
    assert not re.search(r"(?m)^\s*(?:push|pull_request):", workflow)
    assert "timeout-minutes: 12" in workflow
    assert "bash scripts/prepare-regtest.sh" in workflow
    assert "bash scripts/diagnose-wallet-path.sh" in workflow
    assert "secrets.ZCANARY_ZEBRA_PASSWORD" in workflow
    assert "secrets.ZCANARY_ZALLET_PASSWORD" in workflow
    assert re.search(r"(?m)^\s+Z3_ZALLET_RUST_LOG:\s*debug\s*$", workflow)
    assert "if: always()" in workflow
    assert "path: out/diagnostics" in workflow
