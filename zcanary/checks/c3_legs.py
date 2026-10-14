"""C3: no transparent inputs or outputs (coinbase transactions are out of scope)."""
from __future__ import annotations

from ..tx import TxSummary
from . import FAIL, NOT_RUN, PASS, CheckResult, not_run


def check(tx: TxSummary | None) -> CheckResult:
    cid = "C3"
    if tx is None:
        return not_run(cid, "no decoded transaction")
    ev = {"txid": tx.txid, "transparent_in": tx.transparent_in, "transparent_out": tx.transparent_out,
          "sprout_joinsplits": tx.sprout_joinsplits, "coinbase": tx.coinbase,
          "unknown_keys": tx.unknown_keys}
    if tx.unknown_keys:
        return CheckResult(
            cid,
            FAIL,
            "unrecognised transaction format; cannot verify unknown fields: " + ", ".join(tx.unknown_keys),
            ev,
            "Update the transaction parser for these fields before trusting the privacy checks.",
        )
    if tx.coinbase:
        return CheckResult(cid, NOT_RUN, "coinbase transaction: not an app payment", ev,
                           "Point ZCanary at the app's payment txid, not a coinbase.")
    if tx.transparent_in or tx.transparent_out or tx.sprout_joinsplits:
        return CheckResult(cid, FAIL,
                           f"transparent legs present: {tx.transparent_in} in, {tx.transparent_out} out"
                           + (f", {tx.sprout_joinsplits} sprout joinsplits" if tx.sprout_joinsplits else ""), ev,
                           "Spend only shielded notes and pay only shielded receivers (privacy policy FullPrivacy).")
    return CheckResult(cid, PASS, "no transparent inputs or outputs", ev)
