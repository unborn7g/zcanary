from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
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


def test_ci_integration_is_bounded_live_and_report_upload_is_scan_gated():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "timeout-minutes: 45" in workflow
    assert 'python -u -m pytest -v -s -x -m integration --durations=0' in workflow
    assert "id: artifact_scan" in workflow
    assert "if: always() && steps.artifact_scan.outcome == 'success'" in workflow
    assert "Capture redacted Compose diagnostics after failure" in workflow
    assert "if: failure()" in workflow
    assert "scripts/capture-regtest-diagnostics.sh" in workflow
    assert "Upload redacted wallet startup diagnostics" in workflow
    diagnostics_step = workflow.split("name: Upload redacted wallet startup diagnostics", 1)[1]
    assert "if: always()" in diagnostics_step.split("uses:", 1)[0]


def test_integration_cli_and_prepare_subprocesses_are_bounded_at_900_seconds():
    module = _integration_module()
    source = (ROOT / "tests" / "integration" / "test_sample_apps.py").read_text(encoding="utf-8")
    assert module.CLI_SUBPROCESS_TIMEOUT_S == 900
    assert module.PREPARE_SUBPROCESS_TIMEOUT_S == 900
    assert "timeout=1800" not in source
    assert source.count("timeout_s=CLI_SUBPROCESS_TIMEOUT_S") == 1
    assert source.count("timeout_s=PREPARE_SUBPROCESS_TIMEOUT_S") == 2


def test_integration_subprocess_timeout_is_redacted_with_elapsed_seconds(tmp_path, capsys):
    module = _integration_module()
    password = "timeout-unit-password-not-a-secret"
    env = os.environ.copy()
    env["ZCANARY_ZALLET_PASSWORD"] = password
    diagnostics = tmp_path / "timeout-wallet-startup.log"
    env["ZCANARY_DIAGNOSTICS_FILE"] = str(diagnostics)
    command = [
        sys.executable,
        "-c",
        "import os,sys,time; "
        "print('[2030-01-01T00:00:00Z] [ZCANARY-PROGRESS] phase=wait', flush=True); "
        "print('password='+os.environ['ZCANARY_ZALLET_PASSWORD'], file=sys.stderr, flush=True); "
        "time.sleep(5)",
    ]

    with pytest.raises(pytest.fail.Exception) as exc:
        module._run_process_with_timeout(
            "unit timeout",
            command,
            cwd=ROOT,
            env=env,
            timeout_s=0.1,
            progress_only=True,
        )
    message = str(exc.value)
    assert "timed out after" in message and "seconds" in message
    assert "phase=wait" in message
    assert "<redacted>" in message and password not in message
    assert password not in diagnostics.read_text(encoding="utf-8")
    diagnostic_artifact = diagnostics.read_text(encoding="utf-8")
    assert "docker compose ps -a" in diagnostic_artifact
    for service in ("zebra", "zallet", "rpc-router"):
        assert f"--tail 80 {service}" in diagnostic_artifact
    assert "phase=wait" in capsys.readouterr().out


def test_spike_has_timestamped_progress_for_slow_phases():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert "[ZCANARY-PROGRESS]" in script and "date -u" in script
    assert 'progress "phase=start name=$ACTIVE_PHASE"' in script
    assert 'progress "phase=complete name=$completed_phase"' in script
    for phase in (
        "z3-checkout",
        "stack-reset-prepare",
        "wallet-rpc-wait",
        "accounts",
        "mine-shielded-block",
        "mine-maturity-blocks",
        "send-payment",
        "wait-operation-result",
    ):
        assert f'begin_phase "{phase}"' in script
        if phase in ("wallet-rpc-wait", "wait-operation-result"):
            assert f"phase=complete name={phase}" in script
        else:
            assert f'complete_phase "{phase}"' in script
    assert "phase=start name=z_sendmany-submission" in script
    assert "phase=complete name=z_sendmany-submission" in script


def test_wallet_startup_timeout_collects_redacted_compose_diagnostics():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")

    assert 'WAIT_BUDGET=${WALLET_RPC_WAIT_BUDGET:-360}' in script
    assert "wallet_accounts_ready" in script
    assert "getwalletinfo" not in script
    assert "WALLET_RPC_READY=1" in script
    assert script.count("capture_wallet_startup_diagnostics") >= 2
    assert "trap on_exit EXIT" in script
    assert 'progress "phase=failed name=${ACTIVE_PHASE:-unknown} exit=$status"' in script
    timeout_branch = script.split('if [ "$WAIT_READY" -eq 1 ]; then', 1)[1].split("# --- 2. accounts", 1)[0]
    assert "capture_wallet_startup_diagnostics" in timeout_branch
    assert "exit 1" in timeout_branch
    assert "wallet RPC ready after" in script
    assert "wallet RPC not ready after" in script
    assert script.count("restart rpc-router") == 1
    collector = ROOT / "scripts" / "capture-regtest-diagnostics.sh"
    assert collector.is_file()
    collector_text = collector.read_text(encoding="utf-8")
    assert 'docker compose --env-file .env.regtest ps -a' in collector_text
    assert 'docker compose --env-file .env.regtest logs --no-color --tail 80' in collector_text
    assert "for service in zebra zallet rpc-router" in collector_text
    assert "append_redacted_tail" in collector_text


def test_rpc_script_bounds_connect_and_total_time_with_overridable_max_time():
    script = (ROOT / "scripts" / "rpc.sh").read_text(encoding="utf-8")
    assert "RPC_MAX_TIME=${RPC_MAX_TIME:-20}" in script
    assert "--connect-timeout 5" in script
    assert '--max-time "$RPC_MAX_TIME"' in script
    assert "--config <(printf" in script, "keep credentials in curl config, not argv"
    assert not re.search(r"curl[^\n]*(?:--user|(?:^|\s)-u\s)", script)


def test_rpc_rejects_an_unbounded_zero_max_time():
    env = os.environ.copy()
    env.update({"RPC_USER": "unit-user", "RPC_PASS": "unit-test-password-not-a-secret", "RPC_MAX_TIME": "0"})
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "rpc.sh"), "http://127.0.0.1:1", "z_listaccounts"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2
    assert "RPC_MAX_TIME must be a positive integer" in proc.stderr


def test_wallet_readiness_wait_uses_wall_clock_and_reports_last_rpc_status():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert 'WAIT_BUDGET=${WALLET_RPC_WAIT_BUDGET:-360}' in script
    assert 'WAIT_REPORT_INTERVAL=${WALLET_RPC_WAIT_REPORT_INTERVAL:-30}' in script
    assert '[ "$WAIT_ELAPSED" -lt "$WAIT_BUDGET" ]' in script
    assert 'wallet RPC attempt {attempt}: ' in script
    assert 'f"curl exit {curl_exit} ({reason}); HTTP status {http_status}; elapsed {elapsed}s"' in script
    assert 'WALLET_RPC_LAST_CURL_EXIT' in script


def test_first_failed_readiness_probe_snapshots_zallet_logs_before_timeout():
    script = (ROOT / "scripts" / "spike.sh").read_text(encoding="utf-8")
    assert "capture_first_failed_wallet_rpc_zallet_logs" in script
    assert 'docker compose --env-file .env.regtest logs --no-color --tail 80 zallet' in script
    assert "FIRST_READINESS_FAILURE_CAPTURED" in script
    first_probe = script.split("if wallet_accounts_ready", 1)[1].split("done", 1)[0]
    assert "capture_first_failed_wallet_rpc_zallet_logs" in first_probe


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


def test_wallet_readiness_tracks_http_status_and_actual_curl_exit(tmp_path):
    helper = ROOT / "scripts" / "lib" / "wallet_rpc.sh"
    fake_rpc = tmp_path / "fake-rpc.sh"
    fake_rpc.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$FAKE_RPC_ERROR" >&2
exit 22
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
            "FAKE_RPC_ERROR": "RPC HTTP request expected HTTP 200 (received 201; curl exit 0)",
        }
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; if wallet_accounts_ready; then exit 99; fi; '
            'printf "%s|%s|%s\\n" "$WALLET_RPC_LAST_CURL_EXIT" '
            '"$WALLET_RPC_LAST_HTTP_STATUS" "$WALLET_RPC_LAST_REASON"',
            "bash",
            str(helper),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stdout == "0|201|http-error\n"


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
    assert "received 201" in proc.stderr and "curl exit 0" in proc.stderr


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


def test_append_redacted_tail_sanitizes_addresses_hashes_and_keeps_80_lines(tmp_path):
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
        assert "diagnostic-01" in output and "diagnostic-45" in output
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


def test_failure_diagnostics_collector_runs_all_compose_commands_and_redacts(tmp_path):
    collector = ROOT / "scripts" / "capture-regtest-diagnostics.sh"
    assert collector.is_file(), "shared regtest diagnostics collector is missing"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'fake docker args: %s\\n' \"$*\"\n"
        "printf 'wallet password: %s\\n' \"$ZCANARY_ZALLET_PASSWORD\"\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    compose_dir = tmp_path / "z3"
    compose_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["ZCANARY_ZALLET_PASSWORD"] = "collector-unit-password-not-a-secret"
    diagnostics = tmp_path / "wallet-startup.log"

    proc = subprocess.run(
        ["bash", str(collector), str(diagnostics), str(compose_dir)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    rendered = diagnostics.read_text(encoding="utf-8")

    assert proc.returncode == 0
    assert "docker compose ps -a" in rendered
    assert "fake docker args: compose --env-file .env.regtest ps -a" in rendered
    for service in ("zebra", "zallet", "rpc-router"):
        assert f"docker compose --env-file .env.regtest logs --no-color --tail 80 {service}" in rendered
        assert f"fake docker args: compose --env-file .env.regtest logs --no-color --tail 80 {service}" in rendered
    assert "fake docker args: compose --env-file .env.regtest ps -a" in rendered
    assert env["ZCANARY_ZALLET_PASSWORD"] not in rendered
    assert "<redacted>" in rendered


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
    assert runners == ["ubuntu-24.04", "ubuntu-24.04", "ubuntu-24.04"]
