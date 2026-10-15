"""Docker-backed, disposable regtest tests for the six authorized sample apps."""
from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI_SUBPROCESS_TIMEOUT_S = 900
PREPARE_SUBPROCESS_TIMEOUT_S = 900
_PROGRESS_LINE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[ZCANARY-PROGRESS\] ")
CASES = [
    ("clean-shop", set()),
    ("transparent-fallback", {"C2", "C3", "C4b", "MEMO"}),
    ("wrong-network", {"C1"}),
    ("log-leaker", {"C6"}),
    ("memo-dropper", {"MEMO"}),
    ("cross-pool-payer", {"C4", "C4b"}),
]


def _redact_process_output(text: str, env: dict[str, str]) -> str:
    """Apply the same secret/canary redactions to live progress and failure tails."""
    for name, secret in env.items():
        if secret and any(
            marker in name.upper()
            for marker in ("PASSWORD", "PASS", "TOKEN", "SECRET", "SEED", "PRIVATE_KEY", "MEMO")
        ):
            replacement = "<canary>" if "MEMO" in name.upper() else "<redacted>"
            text = text.replace(secret, replacement)
            encoded = secret.encode("utf-8").hex()
            text = text.replace(encoded, replacement).replace(encoded.upper(), replacement)
    text = re.sub(r"ZC-[MLT]-[A-F0-9]{24}", "<canary>", text, flags=re.IGNORECASE)
    text = re.sub(
        r"5a432d(?:4d|4c|54)2d(?:3[0-9]|4[1-6]){24}",
        "<canary>",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _process_diagnostic(label: str, proc: subprocess.CompletedProcess, env: dict[str, str]) -> str:
    """Return a compact, redacted status and output tail safe for test failures."""
    text = _redact_process_output(
        "\n".join(part for part in (proc.stdout, proc.stderr) if part), env
    )
    tail = "\n".join(text.splitlines()[-20:]) or "(no captured output)"
    return f"{label}: exit={proc.returncode}\n{tail}"


def _process_descendants(root_pid: int) -> list[int]:
    """Find children across nested process groups on Linux CI runners."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []
    children: dict[int, list[int]] = {}
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                fields = stat[stat.rfind(")") + 1 :].split()
                parent_pid = int(fields[1])
                children.setdefault(parent_pid, []).append(int(entry.name))
            except (OSError, IndexError, ValueError):
                continue
    except OSError:
        return []
    descendants: list[int] = []
    pending = list(children.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if os.name != "posix":
        proc.kill()
        return
    for pid in reversed(_process_descendants(proc.pid)):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        proc.kill()


def _collect_timeout_diagnostics(env: dict[str, str]) -> None:
    """Preserve Compose diagnostics even if a timeout has to kill the CLI tree."""
    default_path = ROOT / "out" / "diagnostics" / "wallet-startup.log"
    output_path = Path(env.get("ZCANARY_DIAGNOSTICS_FILE") or default_path).resolve()
    expected = (
        "docker compose ps -a",
        *(
            f"docker compose --env-file .env.regtest logs --no-color --tail 80 {service}"
            for service in ("zebra", "zallet", "rpc-router")
        ),
    )
    try:
        existing = output_path.read_text(encoding="utf-8", errors="replace") if output_path.is_file() else ""
    except OSError:
        existing = ""
    if all(label in existing for label in expected):
        return
    collector = ROOT / "scripts" / "capture-regtest-diagnostics.sh"
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["bash", str(collector), str(output_path), str(ROOT / "z3")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        rendered = output_path.read_text(encoding="utf-8", errors="replace") if output_path.is_file() else ""
        missing = [label for label in expected if label not in rendered]
        if missing:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8") as diagnostics:
                for label in missing:
                    diagnostics.write(f"==> {label} (diagnostic collection unavailable)\n")
    except OSError:
        pass


def _run_process_with_timeout(
    label: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    progress_only: bool = False,
    stop_regtest_on_timeout: bool = False,
) -> subprocess.CompletedProcess:
    """Capture all output, optionally stream only timestamped sanitized progress lines."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        pytest.fail(f"{label}: subprocess could not start ({exc.__class__.__name__})", pytrace=False)
    if proc.stdout is None:
        proc.kill()
        pytest.fail(f"{label}: subprocess output pipe was unavailable", pytrace=False)

    captured = bytearray()
    pending = bytearray()
    timed_out = False
    deadline = started + timeout_s

    def stream_progress(raw_line: bytes) -> None:
        if not progress_only:
            return
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
        if _PROGRESS_LINE_RE.match(line):
            print(_redact_process_output(line, env), flush=True)

    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            now = time.monotonic()
            if not timed_out and now >= deadline and selector.get_map():
                timed_out = True
                _kill_process_tree(proc)
            wait_s = 0.1 if timed_out or proc.poll() is not None else max(0.0, min(0.1, deadline - now))
            for key, _ in selector.select(wait_s):
                try:
                    chunk = os.read(key.fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    if pending:
                        stream_progress(bytes(pending))
                        pending.clear()
                    continue
                captured.extend(chunk)
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    stream_progress(line)

    returncode = proc.wait()
    elapsed = time.monotonic() - started
    stdout = captured.decode("utf-8", errors="replace")
    completed = subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")
    if timed_out:
        _collect_timeout_diagnostics(env)
        if stop_regtest_on_timeout:
            try:
                subprocess.run(
                    ["bash", "scripts/stop-regtest.sh"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=300,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        detail = _process_diagnostic(label, completed, env)
        pytest.fail(
            f"{label} timed out after {elapsed:.2f} seconds "
            f"(limit {timeout_s:g} seconds)\n{detail}",
            pytrace=False,
        )
    return completed


@pytest.fixture(scope="module", autouse=True)
def integration_preflight():
    if os.environ.get("ZCANARY_RUN_INTEGRATION") != "1":
        pytest.skip("set ZCANARY_RUN_INTEGRATION=1 to run Docker regtest integration tests")
    missing = [
        name
        for name in ("ZCANARY_ZEBRA_PASSWORD", "ZCANARY_ZALLET_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.fail(
            "integration runner is missing environment-only RPC credential(s): " + ", ".join(missing),
            pytrace=False,
        )
    # The integration job starts from a clean workspace; the first compose pull is
    # timed once and retained while the six isolated cases reset Docker volumes.
    shutil.rmtree(ROOT / "out", ignore_errors=True)


@pytest.mark.integration
@pytest.mark.parametrize("name,expected_failures", CASES, ids=[case[0] for case in CASES])
def test_example_app_has_exact_expected_failed_checks(name: str, expected_failures: set[str]):
    cfg = ROOT / "examples" / "configs" / f"{name}.toml"
    env = os.environ.copy()
    proc = _run_process_with_timeout(
        f"{name} zcanary CLI",
        [sys.executable, "-m", "zcanary.cli", "run", "--config", str(cfg)],
        cwd=ROOT,
        env=env,
        timeout_s=CLI_SUBPROCESS_TIMEOUT_S,
        progress_only=True,
        stop_regtest_on_timeout=True,
    )
    diagnostic = _process_diagnostic(f"{name} zcanary CLI", proc, env)
    report_path = ROOT / "out" / "reports" / f"{name}.json"
    assert report_path.exists(), (
        f"{name}: zcanary did not produce a report (exit {proc.returncode})\n{diagnostic}"
    )
    expected_exit = 0 if not expected_failures else 1
    assert proc.returncode == expected_exit, (
        f"{name}: expected CLI exit {expected_exit}, got {proc.returncode}\n{diagnostic}"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures = {item["id"] for item in report["checks"] if item["status"] == "fail"}
    assert failures == expected_failures, (
        f"{name}: expected failed checks {sorted(expected_failures)}, got {sorted(failures)}\n"
        f"{diagnostic}"
    )
    assert report["not_covered"] and any(
        "guarantee" in item.lower() for item in report["not_covered"]
    )
    # Reports and CI artifacts may contain labels/counts, never runtime marker values.
    rendered = report_path.read_text(encoding="utf-8")
    assert not re.search(r"ZC-[MLT]-[A-F0-9]{24}", rendered)

    if name == "log-leaker":
        c6 = next(item for item in report["checks"] if item["id"] == "C6")
        findings = c6["evidence"]["findings_by_label"]
        for label in ("memo", "label", "test_id"):
            assert findings[label]["count"] == 1
            assert findings[label]["sources"] == ["app.logs[1].stdout"]


@pytest.mark.integration
def test_beta1_known_bad_fixture_accepts_exact_fullprivacy_cross_pool_call():
    env = os.environ.copy()
    fixture = ROOT / "fixtures" / "version-matrix" / "beta1-known-bad.env"
    for line in fixture.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            key, value = line[len("export "):].split("=", 1)
            env[key] = value.strip().strip("'\"")
    env.update(
        {
            "ZCANARY_NODE_RPC": "http://127.0.0.1:29232",
            "ZCANARY_NODE_USER": "zebra",
            "ZCANARY_NODE_PASSWORD": env["ZCANARY_ZEBRA_PASSWORD"],
            "ZCANARY_ZALLET_RPC": "http://127.0.0.1:8181",
            "ZCANARY_ZALLET_USER": "zebra",
            "ZCANARY_CONTEXT_FILE": str(ROOT / "out" / "context.json"),
        }
    )
    try:
        prepare = _run_process_with_timeout(
            "beta.1 prepare-regtest",
            ["bash", "scripts/prepare-regtest.sh"],
            cwd=ROOT,
            env=env,
            timeout_s=PREPARE_SUBPROCESS_TIMEOUT_S,
            progress_only=True,
        )
        assert prepare.returncode == 0, _process_diagnostic("beta.1 prepare-regtest", prepare, env)
        result = subprocess.run(
            [sys.executable, "scripts/reproduce_fullprivacy_crosspool.py"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
        )
        assert result.returncode == 0, "beta.1 exact FullPrivacy cross-pool fixture was not reproduced"
        assert "known-bad beta.1 fixture reproduced" in result.stdout
    finally:
        subprocess.run(
            ["bash", "scripts/stop-regtest.sh"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=300, check=False,
        )


@pytest.mark.integration
def test_beta3_rejects_exact_fullprivacy_ironwood_to_sapling_call():
    env = os.environ.copy()
    env.update(
        {
            "Z3_ZEBRA_IMAGE": "zfnd/zebra:6.3.0@sha256:52a67e543906c98a0ed1599e2ce3ee238fc05b40592ae26ee5914ddb6ede51e3",
            "Z3_ZALLET_IMAGE": "zodlinc/zallet:v0.1.0-beta.3@sha256:54d1a774317371b1e88c8a54712cdfa4aa7d8f20ae2a0205f9a190e502aef0f6",
            "ZCANARY_ZALLET_VERSION": "0.1.0-beta.3",
            "ZCANARY_NODE_RPC": "http://127.0.0.1:29232",
            "ZCANARY_NODE_USER": "zebra",
            "ZCANARY_NODE_PASSWORD": env["ZCANARY_ZEBRA_PASSWORD"],
            "ZCANARY_ZALLET_RPC": "http://127.0.0.1:8181",
            "ZCANARY_ZALLET_USER": "zebra",
            "ZCANARY_CONTEXT_FILE": str(ROOT / "out" / "context.json"),
        }
    )
    try:
        prepare = _run_process_with_timeout(
            "beta.3 prepare-regtest",
            ["bash", "scripts/prepare-regtest.sh"],
            cwd=ROOT,
            env=env,
            timeout_s=PREPARE_SUBPROCESS_TIMEOUT_S,
            progress_only=True,
        )
        assert prepare.returncode == 0, _process_diagnostic("beta.3 prepare-regtest", prepare, env)
        result = subprocess.run(
            [sys.executable, "scripts/reproduce_fullprivacy_crosspool.py"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
        )
        assert result.returncode == 0, "beta.3 FullPrivacy cross-pool call did not match the expected policy rejection"
    finally:
        subprocess.run(
            ["bash", "scripts/stop-regtest.sh"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=300, check=False,
        )
