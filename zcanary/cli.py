"""zcanary run --config zcanary.toml"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, checks, pools, tx as txmod
from .config import ConfigError, load
from .redact import text as redact_text
from .rpc import RpcError, Zallet, Zebra

EXIT_PASS, EXIT_FAIL, EXIT_NOT_RUN, EXIT_ERROR = 0, 1, 2, 3


def _sh(cmd: str, cwd: Path, log: Path) -> int:
    with log.open("a") as f:
        f.write(f"$ {cmd}\n"); f.flush()
        return subprocess.run(cmd, shell=True, cwd=cwd, stdout=f, stderr=subprocess.STDOUT).returncode


def _try(fn, *a):
    try:
        return fn(*a), None
    except RpcError as e:
        return None, str(e)


def run(cfg_path: str) -> int:
    try:
        cfg = load(cfg_path)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR
    base = cfg.base_dir
    report_path = base / cfg.report_path
    log = report_path.with_suffix(".log")
    log.write_text("")
    t0 = time.time()
    steps: dict[str, dict] = {}

    for name, cmd in (("start", cfg.start_command), ("pay", cfg.pay_command)):
        if not cmd:
            steps[name] = {"status": "skipped", "reason": "no command configured"}
            continue
        print(f"[zcanary] {name}: {cmd}")
        s = time.time()
        rc = _sh(cmd, base, log)
        steps[name] = {"cmd": cmd, "exit_code": rc, "seconds": round(time.time() - s, 1)}
        if rc != 0:
            print(f"[zcanary] {name} command failed (exit {rc}); see {log}")
            break

    result, result_err = None, None
    rf = base / cfg.result_file
    if steps.get("pay", {}).get("exit_code", 0) == 0:
        try:
            result = json.loads(rf.read_text())
        except (OSError, ValueError) as e:
            result_err = f"cannot read result file {cfg.result_file}: {e}"
    else:
        result_err = "pay command failed"
    result = result or {}
    txid, recipient = result.get("txid"), result.get("recipient_address")

    zebra, zallet = Zebra(cfg.zebra, cfg.timeout_s), Zallet(cfg.zallet, cfg.timeout_s)
    errors: dict[str, str] = {}
    chain, e = _try(zebra.chain); e and errors.setdefault("getblockchaininfo", e)
    rcv = rcv_err = None
    if recipient and recipient.lower().startswith("u"):
        rcv, rcv_err = _try(zebra.receivers, recipient)
    raw = summary = block = deltas = view = None
    if txid:
        raw, e = _try(zebra.rawtx, txid); e and errors.setdefault("getrawtransaction", e)
        if raw:
            summary = txmod.classify(raw)
            if raw.get("blockhash"):
                block, e = _try(zebra.block, raw["blockhash"]); e and errors.setdefault("getblock", e)
                deltas = pools.block_deltas(block) if block else None
        view, e = _try(zallet.view, txid); e and errors.setdefault("z_viewtransaction", e)

    results = [
        checks.c1(cfg.network, chain, recipient),
        checks.c2(recipient, rcv, cfg.expect_privacy, rcv_err),
        checks.c3(summary),
        checks.c4(summary, cfg.expect_pool, deltas),
        checks.c4b(summary, deltas),
        checks.memo(view, result.get("canary"), result.get("recipient_account")),
    ]
    statuses = [r.status for r in results]
    overall = "fail" if "fail" in statuses else ("not_run" if "not_run" in statuses else "pass")
    report = {
        "zcanary": __version__, "overall": overall, "network": cfg.network,
        "expect": {"privacy": cfg.expect_privacy, "pool": cfg.expect_pool},
        "txid": txid, "steps": steps, "errors": {k: redact_text(v) for k, v in errors.items()},
        "result_error": result_err, "seconds": round(time.time() - t0, 1),
        "checks": [r.to_dict() for r in results],
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for r in results:
        print(f"  {r.id:5} {r.status.upper():8} {redact_text(r.message)}")
    print(f"[zcanary] overall: {overall.upper()}  report: {report_path}")
    return {"pass": EXIT_PASS, "fail": EXIT_FAIL}.get(overall, EXIT_NOT_RUN)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zcanary", description="Zcash privacy smoke test (regtest)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start network, run the app payment, run checks")
    r.add_argument("--config", default="zcanary.toml")
    a = p.parse_args(argv)
    if a.cmd == "run":
        return run(a.config)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
