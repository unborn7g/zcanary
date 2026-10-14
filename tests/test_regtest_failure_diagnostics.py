from __future__ import annotations

import importlib.util
import os
import re
import subprocess
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
