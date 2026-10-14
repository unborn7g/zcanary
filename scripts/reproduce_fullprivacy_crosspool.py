#!/usr/bin/env python3
"""Run the exact no-memo FullPrivacy Ironwood-to-Sapling policy fixture on regtest."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from zcanary.config import Endpoint
from zcanary.rpc import RpcError, Zallet, Zebra
from zcanary.tx import classify


def main() -> int:
    version = os.environ.get("ZCANARY_ZALLET_VERSION", "0.1.0-beta.3")
    if version not in ("0.1.0-beta.1", "0.1.0-beta.3"):
        print("unsupported Zallet policy-fixture version", file=sys.stderr)
        return 2
    expected = "accepted-crossing" if version.endswith("beta.1") else "rejected"
    context_path = Path(os.environ.get("ZCANARY_CONTEXT_FILE", "out/context.json"))
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
        sender = context["sender_address"]
        sapling = context["sapling_receiver"]
        sender_account = context["sender_account"]
    except (OSError, ValueError, KeyError, TypeError):
        print("regtest context missing required payment fields", file=sys.stderr)
        return 2

    zebra = Zebra(
        Endpoint(
            os.environ.get("ZCANARY_NODE_RPC", "http://127.0.0.1:29232"),
            os.environ.get("ZCANARY_NODE_USER", "zebra"),
            "ZCANARY_NODE_PASSWORD",
        ),
        timeout_s=30,
    )
    zallet = Zallet(
        Endpoint(
            os.environ.get("ZCANARY_ZALLET_RPC", "http://127.0.0.1:8181"),
            os.environ.get("ZCANARY_ZALLET_USER", "zebra"),
            "ZCANARY_ZALLET_PASSWORD",
        ),
        timeout_s=30,
    )
    try:
        balances = zallet.call("z_getbalanceforaccount", sender_account, 1)
        pools = balances.get("pools", {}) if isinstance(balances, dict) else {}
        if int(pools.get("ironwood", {}).get("valueZat", 0)) <= 0:
            print("fixture precondition failed: sender has no confirmed Ironwood balance", file=sys.stderr)
            return 1

        # Exact policy fixture: Sapling receiver, 0.1 ZEC, no memo, FullPrivacy.
        opid = zallet.call("z_sendmany", sender, [{"address": sapling, "amount": 0.1}], 1, None, "FullPrivacy")
    except RpcError as error:
        if expected == "rejected" and error.code == -8:
            print("FullPrivacy cross-pool request rejected as expected (policy error)")
            return 0
        print(f"FullPrivacy cross-pool request failed unexpectedly (RPC code {error.code})", file=sys.stderr)
        return 1

    if not isinstance(opid, str) or not opid:
        print("FullPrivacy request returned no operation id", file=sys.stderr)
        return 1
    if expected == "rejected":
        print("beta.3 accepted the FullPrivacy cross-pool request; expected a policy rejection", file=sys.stderr)
        return 1

    completed = None
    try:
        for _ in range(90):
            values = zallet.call("z_getoperationresult", [opid])
            if isinstance(values, list) and values:
                completed = values[0]
                break
            time.sleep(2)
        if not isinstance(completed, dict) or completed.get("status") != "success":
            print("beta.1 FullPrivacy operation did not complete successfully", file=sys.stderr)
            return 1
        txid = completed.get("result", {}).get("txid")
        if not isinstance(txid, str) or not txid:
            print("beta.1 operation did not return a transaction id", file=sys.stderr)
            return 1
        zebra.call("generate", 1)
        raw = zebra.rawtx(txid)
        summary = classify(raw)
    except (RpcError, ValueError, TypeError, KeyError):
        print("beta.1 transaction could not be mined or decoded", file=sys.stderr)
        return 1

    vb = {pool: summary.bundles[pool].value_balance_zat for pool in ("sapling", "orchard", "ironwood")}
    crossing = vb["sapling"] < 0 and vb["ironwood"] > 0 and not summary.transparent_in and not summary.transparent_out
    if not crossing:
        print("beta.1 request succeeded, but decoded transaction did not match the known crossing fixture", file=sys.stderr)
        return 1

    output = Path("out/fullprivacy-policy")
    output.mkdir(parents=True, exist_ok=True)
    evidence = {
        "zallet_version": version,
        "policy": "FullPrivacy",
        "amount_zec": "0.1",
        "direction": "ironwood_to_sapling",
        "txid": txid,
        "value_balance_zat": vb,
        "transparent_inputs": summary.transparent_in,
        "transparent_outputs": summary.transparent_out,
        "classification": "known-bad-cross-pool-accepted",
    }
    (output / "result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print("known-bad beta.1 fixture reproduced: FullPrivacy accepted an Ironwood-to-Sapling crossing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
