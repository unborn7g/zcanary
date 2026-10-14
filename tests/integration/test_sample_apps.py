"""Docker-backed, disposable regtest tests for the six authorized sample apps."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ("clean-shop", set()),
    ("transparent-fallback", {"C2", "C3", "C4b", "MEMO"}),
    ("wrong-network", {"C1"}),
    ("log-leaker", {"C6"}),
    ("memo-dropper", {"MEMO"}),
    ("cross-pool-payer", {"C4", "C4b"}),
]


def _process_diagnostic(label: str, proc: subprocess.CompletedProcess, env: dict[str, str]) -> str:
    """Return a compact, redacted status and output tail safe for test failures."""
    text = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
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
    tail = "\n".join(text.splitlines()[-20:]) or "(no captured output)"
    return f"{label}: exit={proc.returncode}\n{tail}"


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
    proc = subprocess.run(
        [sys.executable, "-m", "zcanary.cli", "run", "--config", str(cfg)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
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
    prepare = subprocess.run(
        ["bash", "scripts/prepare-regtest.sh"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=1800, check=False,
    )
    try:
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
    prepare = subprocess.run(
        ["bash", "scripts/prepare-regtest.sh"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=1800, check=False,
    )
    try:
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
