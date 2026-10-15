from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BUSYBOX_REF = "busybox:1.36.1@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"


def _integration_module():
    path = ROOT / "tests" / "integration" / "test_sample_apps.py"
    spec = importlib.util.spec_from_file_location("sample_app_integration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinned_busybox_is_pulled_and_tagged_before_both_initializers():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    lines = script.splitlines()
    pull_lines = [i for i, line in enumerate(lines) if "docker pull" in line]
    tag_lines = [i for i, line in enumerate(lines) if "docker tag " in line]
    init_lines = [i for i, line in enumerate(lines) if "./scripts/regtest-init.sh" in line and not line.lstrip().startswith("#")]

    assert BUSYBOX_REF in script
    assert len(pull_lines) == 1
    assert len(tag_lines) == 2
    assert len(init_lines) == 2
    assert pull_lines[0] < min(init_lines)
    assert max(tag_lines) < min(init_lines)
    for index in [*pull_lines, *init_lines]:
        assert "2>&1" not in lines[index]
        assert ">/dev/null" not in lines[index]


def test_busybox_tags_are_created_from_the_digest_reference():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")

    assert 'docker tag "$BUSYBOX_REF" busybox:1.36.1' in script
    assert 'docker tag "$BUSYBOX_REF" busybox:latest' in script
    assert not re.search(r"\bdocker\s+tag\s+busybox:1\.36\.1(?:\s|$)", script)


def test_wallet_startup_timeout_collects_redacted_compose_diagnostics():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")

    assert re.search(r"\bWAIT_ATTEMPTS=120\b", script)
    assert "wallet_accounts_ready" in script
    assert "getwalletinfo" not in script
    assert "WALLET_RPC_READY=1" in script
    assert "capture_wallet_startup_diagnostics" in script
    assert "wallet RPC ready after" in script
    assert "wallet RPC not ready after" in script
    assert script.count("restart rpc-router") == 1
    assert 'docker compose --env-file .env.regtest ps -a' in script
    assert 'docker compose --env-file .env.regtest logs --no-color --tail 40' in script
    assert "for service in zebra zallet rpc-router" in script
    assert "append_redacted_tail" in script


def test_wallet_accounts_readiness_requires_null_error_and_list_result(tmp_path):
    helper = ROOT / "scripts" / "lib" / "wallet_rpc.sh"
    fake_rpc = tmp_path / "fake-rpc.sh"
    fake_rpc.write_text(
        """#!/usr/bin/env bash
printf '%s' "$FAKE_RPC_BODY"
""",
        encoding="utf-8",
    )
    fake_rpc.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "RPC": str(fake_rpc),
            "ROUTER": "http://127.0.0.1:8181",
            "ZCANARY_ZALLET_USER": "unit-user",
            "ZCANARY_ZALLET_PASSWORD": "unit-test-password-not-a-secret",
        }
    )
    command = ["bash", "-c", 'source "$1"; wallet_accounts_ready', "bash", str(helper)]

    env["FAKE_RPC_BODY"] = '{"error":null,"result":[]}'
    assert subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode == 0
    env["FAKE_RPC_BODY"] = '{"error":{"code":-1},"result":[]}'
    assert subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode == 1
    env["FAKE_RPC_BODY"] = '{"error":null,"result":{}}'
    assert subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode == 1
    env["FAKE_RPC_BODY"] = "not-json"
    assert subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode == 1


def test_rpc_script_rejects_non_200_even_with_a_ready_json_body():
    class CreatedHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = b'{"error":null,"result":[]}'
            self.send_response(201)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), CreatedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env.update({"RPC_USER": "unit-user", "RPC_PASS": "unit-test-password-not-a-secret"})
    try:
        proc = subprocess.run(
            ["bash", str(ROOT / "scripts" / "rpc.sh"), f"http://127.0.0.1:{server.server_port}", "z_listaccounts"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert proc.returncode == 22
    assert proc.stdout == ""
    assert "expected HTTP 200" in proc.stderr


def test_rpc_wallet_http_error_is_readable_and_skips_json_parser(tmp_path):
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert 'source "$ROOT/scripts/lib/wallet_rpc.sh"' in script
    assert "rpc_wallet_j" in script
    assert not re.search(r"(?m)\brpc_wallet\s+[^\n]*\|\s*j\b", script)
    helper = ROOT / "scripts" / "lib" / "wallet_rpc.sh"
    assert helper.is_file(), "wallet RPC helper is missing"
    fake_rpc = tmp_path / "fake-rpc.sh"
    fake_rpc.write_text(
        "printf 'curl: (22) The requested URL returned error: 502\\n' >&2\n"
        "exit 22\n",
        encoding="utf-8",
    )
    fake_rpc.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "RPC": str(fake_rpc),
            "ROUTER": "http://127.0.0.1:8181",
            "ZCANARY_ZALLET_USER": "unit-user",
            "ZCANARY_ZALLET_PASSWORD": "unit-test-password-not-a-secret",
            "WALLET_RPC_READY": "1",
            "DIAG_TRACE": str(tmp_path / "diagnostic-calls.txt"),
        }
    )
    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; capture_wallet_startup_diagnostics() { printf "captured\\n" >> "$DIAG_TRACE"; }; j() { echo JSON_PARSER_CALLED >&2; return 99; }; rpc_wallet_j ignored getwalletinfo',
         "bash", str(helper)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 22
    assert "wallet RPC getwalletinfo failed: HTTP error (curl exit 22)" in proc.stderr
    assert "curl: (22)" in proc.stderr
    assert "JSON_PARSER_CALLED" not in proc.stderr
    assert env["ZCANARY_ZALLET_PASSWORD"] not in proc.stderr
    assert (tmp_path / "diagnostic-calls.txt").read_text(encoding="utf-8") == "captured\n"


def test_append_redacted_tail_sanitizes_addresses_hashes_and_keeps_40_lines(tmp_path):
    helper = ROOT / "scripts" / "lib" / "diagnostics.sh"
    fake = tmp_path / "fake-container-logs.sh"
    address = "uregtest1abcdefghijklmnopqrstuvwxyz0123456789"
    auth_hash = "0123456789abcdef0123456789abcdef$" + "a" * 64
    fake.write_text(
        f"""#!/usr/bin/env bash
for i in {{1..45}}; do echo "diagnostic-$(printf '%02d' "$i")"; done
echo "$ZCANARY_ZALLET_PASSWORD"
echo "{address}"
echo '{auth_hash}'
exit 3
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["ZCANARY_ZALLET_PASSWORD"] = "unit-test-password-not-a-secret"
    diagnostics = tmp_path / "wallet-startup.log"
    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; append_redacted_tail "$2" "fake container logs" "$3" bash "$4"',
         "bash", str(helper), str(diagnostics), str(tmp_path), str(fake)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    rendered = diagnostics.read_text(encoding="utf-8")
    for output in (proc.stderr, rendered):
        assert "fake container logs (exit 3)" in output
        assert "diagnostic-08" not in output and "diagnostic-09" in output and "diagnostic-45" in output
        assert env["ZCANARY_ZALLET_PASSWORD"] not in output
        assert address not in output and auth_hash not in output
        assert "<redacted>" in output and "<redacted-auth-hash>" in output
        assert f"{address[:12]}…{address[-6:]}" in output


def test_ci_uploads_wallet_startup_diagnostics_always():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Upload redacted wallet startup diagnostics" in workflow
    assert "out/diagnostics/wallet-startup.log" in workflow
    assert re.search(
        r"name: Upload redacted wallet startup diagnostics\n\s+if: always\(\)", workflow
    )
    assert "if-no-files-found: ignore" in workflow


def test_run_with_tail_redacts_secrets_and_shows_only_last_20_lines(tmp_path):
    helper = ROOT / "scripts" / "lib" / "diagnostics.sh"
    assert helper.is_file(), "spike diagnostics helper is missing"
    fake = tmp_path / "fake-failure.sh"
    fake.write_text(
        "for i in {1..25}; do printf 'output-%02d\\n' \"$i\"; done\n"
        "printf '%s\\n' \"$ZCANARY_ZALLET_PASSWORD\" \"$ZCANARY_MEMO\"\n"
        "exit 7\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["ZCANARY_ZALLET_PASSWORD"] = "unit-test-password-not-a-secret"
    env["ZCANARY_MEMO"] = "ZC-M-0123456789ABCDEF01234567"
    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; run_with_tail "fixture command" bash "$2"',
         "bash", str(helper), str(fake)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 7
    assert "fixture command failed (exit 7)" in proc.stderr
    assert "output-01" not in proc.stderr and "output-08" in proc.stderr and "output-25" in proc.stderr
    assert env["ZCANARY_ZALLET_PASSWORD"] not in proc.stderr
    assert env["ZCANARY_MEMO"] not in proc.stderr
    assert "<redacted>" in proc.stderr and "<canary>" in proc.stderr


def test_integration_assertion_diagnostic_redacts_and_limits_output():
    module = _integration_module()
    password = "unit-test-node-password-not-a-secret"
    canary = "ZC-L-ABCDEF0123456789ABCDEF01"
    streams = "\n".join(f"status-line-{i:02d}" for i in range(1, 26))
    proc = SimpleNamespace(
        returncode=3,
        stdout=streams + "\n" + password + "\n" + canary,
        stderr="captured error: " + password,
    )
    diagnostic = module._process_diagnostic(
        "zcanary CLI", proc, {"ZCANARY_ZEBRA_PASSWORD": password}
    )

    assert "zcanary CLI: exit=3" in diagnostic
    assert "status-line-01" not in diagnostic and "status-line-25" in diagnostic
    assert password not in diagnostic and canary not in diagnostic
    assert "<redacted>" in diagnostic and "<canary>" in diagnostic


def test_ci_jobs_use_pinned_ubuntu_2404_runner():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    runners = re.findall(r"^\s*runs-on:\s*(\S+)\s*$", workflow, flags=re.MULTILINE)
    assert runners == ["ubuntu-24.04", "ubuntu-24.04"]
