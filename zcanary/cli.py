"""zcanary run --config zcanary.toml"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, checks, pools, tx as txmod
from .canaries import CanarySet, marker_variants
from .config import Config, ConfigError, load
from .redact import text as redact_text
from .rpc import RpcError, Zallet, Zebra

EXIT_PASS, EXIT_FAIL, EXIT_NOT_RUN, EXIT_ERROR = 0, 1, 2, 3
_PROGRESS_LINE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[ZCANARY-PROGRESS\] ")
NOT_COVERED = [
    "Network timing, IP/transport metadata, and observers outside the captured app outputs.",
    "App databases, analytics, telemetry, crash dumps, or logs not explicitly configured for capture.",
    "Mainnet/production behavior, real funds, and production credentials; this workflow is regtest-only.",
    "Wallet/node combinations beyond those separately identified in docs/version-matrix.md.",
    "Future transaction fields and pools are not decoded; detected unknown transaction fields make C3/C4/C4b fail closed, but those formats remain unverified.",
    "C4b reports the transaction-derived fee and warns when it differs from conventional ZIP-317; it does not certify miner fee policy or infer hidden transparent input values.",
    "NU7-era behavior and public testnet behavior, which are outside this phase's scope.",
    "A complete privacy/security audit or a guarantee that no sensitive data can leak.",
]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _emit_progress(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{timestamp}] [ZCANARY-PROGRESS] {message}", flush=True)


def _redact_progress_line(line: str, env: dict[str, str]) -> str:
    for name, secret in env.items():
        upper = name.upper()
        if not secret or not any(
            marker in upper
            for marker in ("PASSWORD", "PASS", "TOKEN", "SECRET", "SEED", "PRIVATE_KEY", "MEMO", "AUTH_HASH", "PWHASH")
        ):
            continue
        replacement = "<canary>" if "MEMO" in upper else "<redacted>"
        encoded = secret.encode("utf-8")
        for value in (secret, encoded.hex(), encoded.hex().upper(), base64.b64encode(encoded).decode("ascii")):
            if value:
                line = line.replace(value, replacement)
    line = re.sub(r"(?i)(authorization\s*:\s*basic\s+)[A-Za-z0-9+/=]+", r"\1<redacted>", line)
    line = re.sub(r"(?i)\b[0-9a-f]{32}\$[0-9a-f]{64}\b", "<redacted-auth-hash>", line)
    line = re.sub(r"ZC-[MLT]-[A-F0-9]{24}", "<canary>", line, flags=re.IGNORECASE)
    line = re.sub(
        r"5a432d(?:4d|4c|54)2d(?:3[0-9]|4[1-6]){24}",
        "<canary>",
        line,
        flags=re.IGNORECASE,
    )
    return redact_text(line)


def _terminate_process(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        proc.kill()


def _run_network_start_command(
    name: str,
    command: str,
    base: Path,
    env: dict[str, str],
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Capture network-start output while allowing only sanitized progress through."""
    started = time.monotonic()
    _emit_progress("phase=start name=network-start")
    captured = bytearray()
    pending = bytearray()
    timed_out = False
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=base,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        elapsed = round(time.monotonic() - started, 2)
        stdout, stderr = "", f"command launch error: {exc.__class__.__name__}"
        step = {"status": "error", "exit_code": None, "seconds": elapsed}
        _emit_progress(f"phase=complete name=network-start status=error elapsed_s={elapsed:.2f}")
        step["captured_bytes"] = 0
        print(f"[zcanary] {name}: {step['status']} ({step['seconds']}s)")
        return step, {f"{name}.stdout": stdout, f"{name}.stderr": stderr}

    if proc.stdout is None:
        _terminate_process(proc)
        proc.wait()
        raise OSError("network start output pipe was unavailable")

    def stream_progress(raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
        if _PROGRESS_LINE_RE.match(line):
            print(_redact_progress_line(line, env), flush=True)

    deadline = started + timeout_s
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            now = time.monotonic()
            if not timed_out and now >= deadline and selector.get_map():
                timed_out = True
                _terminate_process(proc)
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
    elapsed = round(time.monotonic() - started, 2)
    stdout = captured.decode("utf-8", errors="replace")
    status = "timeout" if timed_out else "ok" if returncode == 0 else "failed"
    rc = None if timed_out else returncode
    step = {"status": status, "exit_code": rc, "seconds": elapsed}
    step["captured_bytes"] = len(captured)
    _emit_progress(f"phase=complete name=network-start status={status} elapsed_s={elapsed:.2f}")
    print(f"[zcanary] {name}: {step['status']} ({step['seconds']}s)")
    return step, {f"{name}.stdout": stdout, f"{name}.stderr": ""}


def _run_command(
    name: str,
    command: str,
    base: Path,
    env: dict[str, str],
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Run a configured lifecycle/log command without printing raw captured output."""
    if name == "network.start":
        return _run_network_start_command(name, command, base, env, timeout_s)
    started = time.monotonic()
    try:
        p = subprocess.run(
            command,
            shell=True,
            cwd=base,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        stdout, stderr = p.stdout or "", p.stderr or ""
        rc = p.returncode
        status = "ok" if rc == 0 else "failed"
        step = {"status": status, "exit_code": rc, "seconds": round(time.monotonic() - started, 2)}
    except subprocess.TimeoutExpired as e:
        stdout, stderr = _text(e.stdout), _text(e.stderr)
        rc = None
        step = {"status": "timeout", "exit_code": None, "seconds": round(time.monotonic() - started, 2)}
    except OSError as e:
        stdout, stderr = "", f"command launch error: {e.__class__.__name__}"
        rc = None
        step = {"status": "error", "exit_code": None, "seconds": round(time.monotonic() - started, 2)}
    step["captured_bytes"] = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
    print(f"[zcanary] {name}: {step['status']} ({step['seconds']}s)")
    return step, {f"{name}.stdout": stdout, f"{name}.stderr": stderr}


def _try(fn, *args):
    try:
        return fn(*args), None
    except RpcError as e:
        return None, str(e)


def _expected_regtest_diagnostic_labels() -> tuple[str, ...]:
    return (
        "docker compose ps -a",
        *(
            f"docker compose --env-file .env.regtest logs --no-color --tail 80 {service}"
            for service in ("zebra", "zallet", "rpc-router")
        ),
    )


def _ensure_regtest_diagnostics(path: Path, env: dict[str, str]) -> None:
    """Collect bounded, redacted Compose status/logs if the child did not already."""
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = _expected_regtest_diagnostic_labels()
    try:
        existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except OSError:
        existing = ""
    if all(label in existing for label in expected):
        return

    repo_root = Path(__file__).resolve().parents[1]
    collector = repo_root / "scripts" / "capture-regtest-diagnostics.sh"
    try:
        subprocess.run(
            ["bash", str(collector), str(path), str(repo_root / "z3")],
            cwd=repo_root,
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
        rendered = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except OSError:
        rendered = ""
    missing = [label for label in expected if label not in rendered]
    if missing:
        with path.open("a", encoding="utf-8") as diagnostics:
            for label in missing:
                diagnostics.write(f"==> {label} (diagnostic collection unavailable)\n")


def _write_network_start_diagnostics(
    path: Path, step: dict[str, Any], env: dict[str, str]
) -> None:
    """Ensure failed network startup always leaves a safe artifact for CI."""
    _ensure_regtest_diagnostics(path, env)
    with path.open("a", encoding="utf-8") as diagnostics:
        diagnostics.write("\n==> zcanary network.start failed ==\n")
        diagnostics.write(
            f"status={step.get('status', 'unknown')} "
            f"exit_code={step.get('exit_code')} seconds={step.get('seconds')}\n"
        )


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, None, f"cannot read {path.name}: {e.__class__.__name__}"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, raw, f"{path.name} is not valid JSON"
    if not isinstance(data, dict):
        return None, raw, f"{path.name} must contain a JSON object"
    return data, raw, None


def _env_for_run(cfg: Config, canaries: CanarySet) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ZCANARY_NODE_RPC": cfg.zebra.url,
            "ZCANARY_NODE_USER": cfg.zebra.user,
            "ZCANARY_NODE_PASSWORD": cfg.zebra.password(),
            "ZCANARY_ZALLET_RPC": cfg.zallet.url,
            "ZCANARY_ZALLET_USER": cfg.zallet.user,
            "ZCANARY_ZALLET_PASSWORD": cfg.zallet.password(),
            "ZCANARY_NETWORK": cfg.network,
            "ZCANARY_AMOUNT": cfg.amount,
            "ZCANARY_RESULT_FILE": str((cfg.base_dir / cfg.result_file).resolve()),
            "ZCANARY_LOG_FILE": str((cfg.base_dir / cfg.log_file).resolve()) if cfg.log_file else "",
            "ZCANARY_CONTEXT_FILE": str((cfg.base_dir / cfg.context_file).resolve()) if cfg.context_file else "",
        }
    )
    env.update(canaries.as_env())
    return env


def _context_env(env: dict[str, str], context: dict[str, Any] | None) -> None:
    if not context:
        return
    fields = {
        "sender_address": "ZCANARY_SENDER_ADDRESS",
        "sender_account": "ZCANARY_SENDER_ACCOUNT",
        "recipient_address": "ZCANARY_RECIPIENT_ADDRESS",
        "recipient_account": "ZCANARY_RECIPIENT_ACCOUNT",
        "transparent_receiver": "ZCANARY_TRANSPARENT_RECEIVER",
        "sapling_receiver": "ZCANARY_SAPLING_RECEIVER",
    }
    for key, env_key in fields.items():
        value = context.get(key)
        if value is not None:
            env[env_key] = str(value)


def _markdown(report: dict[str, Any]) -> str:
    rows = [
        "# ZCanary regtest report",
        "",
        f"**Overall:** {report['overall'].upper()}",
        f"**Network:** {report['network']}",
        f"**ZCanary:** {report['zcanary']}",
        f"**Elapsed:** {report['seconds']} seconds",
        "",
        "## Checks",
        "",
        "| Check | Status | Finding |",
        "|---|---|---|",
    ]
    for result in report["checks"]:
        message = str(result["message"]).replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {result['id']} | {result['status'].upper()} | {message} |")
    rows.extend(["", "## Steps", ""])
    for name, step in report["steps"].items():
        rows.append(f"- **{name}:** {step.get('status', 'unknown')} ({step.get('seconds', 0)} s)")
    if report.get("execution_errors"):
        rows.extend(["", "## Execution issues", ""])
        for issue in report["execution_errors"]:
            rows.append(f"- {issue}")
    rows.extend(["", "## NOT COVERED", ""])
    rows.extend(f"- {item}" for item in report["not_covered"])
    rows.extend(
        [
            "",
            "Canary scanning is limited to the sources listed in the JSON evidence; it is not a general leak-prevention guarantee.",
            "",
        ]
    )
    return "\n".join(rows)


def _contains_marker(text: str, markers: dict[str, str]) -> bool:
    return any(
        variant in text
        for marker in markers.values()
        for variant in marker_variants(marker)
    )


def _configured_sensitive_markers(cfg: Config) -> dict[str, str]:
    # Very short values have a high false-positive rate. RPC passwords are nevertheless
    # kept out of all displayed output; longer configured values are also checked.
    markers: dict[str, str] = {}
    for label, endpoint in (("credential:node", cfg.zebra), ("credential:zallet", cfg.zallet)):
        value = endpoint.password()
        if len(value) >= 8:
            markers[label] = value
    return markers


def _overall(results: list[checks.CheckResult], execution_errors: list[str]) -> str:
    if execution_errors:
        return "error"
    statuses = [r.status for r in results]
    if checks.FAIL in statuses:
        return "fail"
    if checks.NOT_RUN in statuses:
        return "not_run"
    return "pass"


def run(cfg_path: str) -> int:
    try:
        cfg = load(cfg_path)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR

    base = cfg.base_dir
    report_path = (base / cfg.report_path).resolve()
    markdown_path = (base / cfg.markdown_path).resolve()
    result_path = (base / cfg.result_file).resolve()
    startup_diagnostics_path = result_path.parent / "diagnostics" / "wallet-startup.log"
    context_path = (base / cfg.context_file).resolve() if cfg.context_file else None
    report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    # Fresh markers exist only in process memory and child-process environments.
    canaries = CanarySet.generate()
    env = _env_for_run(cfg, canaries)
    steps: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    execution_errors: list[str] = []
    capture_ok = True
    context: dict[str, Any] | None = None
    result: dict[str, Any] = {}
    result_error: str | None = None

    # Remove stale hand-off/result files so a failed app cannot reuse a previous run's values.
    for stale in (context_path if cfg.start_command else None, result_path, startup_diagnostics_path):
        if stale is not None:
            try:
                stale.unlink(missing_ok=True)
            except OSError as e:
                execution_errors.append(f"could not clear configured output {stale.name}: {e.__class__.__name__}")

    network_started = False
    network_start_attempted = bool(cfg.start_command)
    app_start_attempted = False
    pay_attempted = False
    t0 = time.monotonic()
    try:
        if cfg.start_command:
            steps["network.start"], out = _run_command(
                "network.start", cfg.start_command, base, env, cfg.command_timeout_s
            )
            sources.update(out)
            network_started = steps["network.start"]["status"] == "ok"
            if not network_started:
                execution_errors.append("network start command did not complete successfully")
                try:
                    _write_network_start_diagnostics(
                        startup_diagnostics_path, steps["network.start"], env
                    )
                except OSError:
                    execution_errors.append("could not write wallet startup diagnostics artifact")
        else:
            steps["network.start"] = {"status": "skipped", "reason": "no command configured"}
            network_started = True  # caller may provide an already-running disposable regtest

        if network_started and context_path is not None:
            context, raw, context_error = _read_json(context_path)
            if raw is not None:
                sources[f"artifact:{cfg.context_file}"] = raw
            if context_error:
                result_error = context_error
                if cfg.start_command:
                    execution_errors.append(context_error)
            _context_env(env, context)

        if network_started:
            if cfg.app_start_command:
                app_start_attempted = True
                steps["app.start"], out = _run_command(
                    "app.start", cfg.app_start_command, base, env, cfg.command_timeout_s
                )
                sources.update(out)
                if steps["app.start"]["status"] != "ok":
                    execution_errors.append("app start command did not complete successfully")
            else:
                steps["app.start"] = {"status": "skipped", "reason": "no command configured"}

            app_ready = steps["app.start"]["status"] in ("ok", "skipped")
            if app_ready:
                pay_attempted = True
                steps["app.pay"], out = _run_command(
                    "app.pay", cfg.pay_command, base, env, cfg.command_timeout_s
                )
                sources.update(out)
                if steps["app.pay"]["status"] != "ok":
                    execution_errors.append("app pay command did not complete successfully")
            else:
                steps["app.pay"] = {"status": "skipped", "reason": "app start failed"}
        else:
            steps["app.start"] = {"status": "skipped", "reason": "network start failed"}
            steps["app.pay"] = {"status": "skipped", "reason": "network start failed"}

        # Capture logs before stopping the app. Output remains in memory and is never printed raw.
        for index, command in enumerate(cfg.log_commands, start=1):
            name = f"app.logs[{index}]"
            step, out = _run_command(name, command, base, env, cfg.command_timeout_s)
            steps[name] = step
            sources.update(out)
            if step["status"] != "ok":
                capture_ok = False

        for filename in cfg.scan_files:
            file_path = base / filename
            try:
                sources[f"artifact:{filename}"] = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                capture_ok = False

        # Keep the app result artifact in the scan set and in memory; it must not contain the canaries.
        try:
            if result_path.exists():
                raw_result = result_path.read_text(encoding="utf-8", errors="replace")
                sources[f"artifact:{cfg.result_file}"] = raw_result
                parsed = json.loads(raw_result)
                if isinstance(parsed, dict):
                    result = parsed
                else:
                    result_error = "app result file must contain a JSON object"
            elif pay_attempted and steps.get("app.pay", {}).get("status") == "ok":
                result_error = f"app did not create result file {cfg.result_file}"
        except (OSError, ValueError) as e:
            result_error = f"cannot parse app result file {cfg.result_file}: {e.__class__.__name__}"

    finally:
        # Attempt cleanup even after a start/pay/capture failure. Never display child output.
        if cfg.app_stop_command and (app_start_attempted or pay_attempted):
            steps["app.stop"], out = _run_command(
                "app.stop", cfg.app_stop_command, base, env, cfg.command_timeout_s
            )
            sources.update(out)
            if steps["app.stop"]["status"] != "ok":
                execution_errors.append("app stop command did not complete successfully")
        else:
            steps["app.stop"] = {"status": "skipped", "reason": "no running app to stop"}

        if execution_errors:
            try:
                _ensure_regtest_diagnostics(startup_diagnostics_path, env)
            except OSError:
                execution_errors.append("could not collect redacted regtest diagnostics")


    # The checks query the live node and wallet, so the disposable network is stopped
    # only after those queries (and any failure diagnostics) are done.
    try:
        txid = result.get("txid")
        if not isinstance(txid, str) or not txid:
            txid = None
        recipient = result.get("recipient_address") or (context or {}).get("recipient_address")
        if not isinstance(recipient, str) or not recipient:
            recipient = None
        recipient_account = result.get("recipient_account") or (context or {}).get("recipient_account")
        if not isinstance(recipient_account, str):
            recipient_account = None
        app_network = result.get("app_network")
        if not isinstance(app_network, str):
            app_network = None
        app_genesis_hash = result.get("app_genesis_hash")
        if not isinstance(app_genesis_hash, str):
            app_genesis_hash = None

        zebra, zallet = Zebra(cfg.zebra, cfg.timeout_s), Zallet(cfg.zallet, cfg.timeout_s)
        errors: dict[str, str] = {}
        chain, e = _try(zebra.chain)
        if e:
            errors.setdefault("getblockchaininfo", e)
        genesis, e = _try(zebra.genesis)
        if e:
            errors.setdefault("getblockhash", e)
        rcv = rcv_err = None
        if recipient and isinstance(recipient, str) and recipient.lower().startswith("u"):
            rcv, rcv_err = _try(zebra.receivers, recipient)
        raw = summary = block = deltas = view = None
        if isinstance(txid, str) and txid:
            raw, e = _try(zebra.rawtx, txid)
            if e:
                errors.setdefault("getrawtransaction", e)
            if raw:
                summary = txmod.classify(raw)
                if raw.get("blockhash"):
                    block, e = _try(zebra.block, raw["blockhash"])
                    if e:
                        errors.setdefault("getblock", e)
                    deltas = pools.block_deltas(block) if block else None
            view, e = _try(zallet.view, txid)
            if e:
                errors.setdefault("z_viewtransaction", e)

        if execution_errors or errors:
            try:
                _ensure_regtest_diagnostics(startup_diagnostics_path, env)
            except OSError:
                execution_errors.append("could not collect redacted regtest diagnostics")
    finally:
        if cfg.stop_command and network_start_attempted:
            steps["network.stop"], out = _run_command(
                "network.stop", cfg.stop_command, base, env, cfg.command_timeout_s
            )
            sources.update(out)
            if steps["network.stop"]["status"] != "ok":
                execution_errors.append("network stop command did not complete successfully")
        else:
            steps["network.stop"] = {"status": "skipped", "reason": "no command configured or network not started"}

    results = [
        checks.c1(cfg.network, chain, recipient, genesis, app_network, app_genesis_hash),
        checks.c2(recipient, rcv, cfg.expect_privacy, rcv_err),
        checks.c3(summary),
        checks.c4(summary, cfg.expect_pool, deltas),
        checks.c4b(summary, deltas),
        checks.memo(view, canaries.memo, recipient_account),
        checks.c6(
            sources,
            canaries.by_label(),
            additional_sensitive=_configured_sensitive_markers(cfg),
            capture_ok=capture_ok,
            log_capture_configured=bool(cfg.log_commands or cfg.scan_files),
        ),
    ]
    overall = _overall(results, execution_errors)
    report = {
        "zcanary": __version__,
        "overall": overall,
        "network": cfg.network,
        "expect": {"privacy": cfg.expect_privacy, "pool": cfg.expect_pool, "amount": cfg.amount},
        "txid": txid,
        "steps": steps,
        "errors": {k: redact_text(v) for k, v in errors.items()},
        "result_error": redact_text(result_error) if result_error else None,
        "execution_errors": execution_errors,
        "seconds": round(time.monotonic() - t0, 2),
        "checks": [r.to_dict() for r in results],
        "not_covered": NOT_COVERED,
        "canary_scan_scope": "captured app command output, configured app logs, configured scan files, and generated reports only",
    }
    md = _markdown(report)
    planned_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    sensitive_markers = {**canaries.by_label(), **_configured_sensitive_markers(cfg)}
    if _contains_marker(planned_json, sensitive_markers) or _contains_marker(md, sensitive_markers):
        print("[zcanary] report self-scan found a canary marker; refusing to write reports", file=sys.stderr)
        return EXIT_ERROR

    report_path.write_text(planned_json, encoding="utf-8")
    markdown_path.write_text(md, encoding="utf-8")
    for r in results:
        print(f"  {r.id:5} {r.status.upper():8} {redact_text(r.message)}")
    print(f"[zcanary] overall: {overall.upper()}  reports: {report_path}, {markdown_path}")
    if overall == "pass":
        return EXIT_PASS
    if overall == "fail":
        return EXIT_FAIL
    if overall == "not_run":
        return EXIT_NOT_RUN
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zcanary", description="Zcash privacy smoke test (regtest)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the test network/app, pay, capture logs, and run checks")
    r.add_argument("--config", default="zcanary.toml")
    a = p.parse_args(argv)
    if a.cmd == "run":
        return run(a.config)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
