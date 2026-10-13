"""C2: recipient receivers. Unified addresses are expanded with the node's z_listunifiedreceivers."""
from __future__ import annotations

from .. import address
from ..redact import addr as r
from . import FAIL, PASS, CheckResult, not_run

TRANSPARENT_KEYS = ("p2pkh", "p2sh")


def check(recipient: str | None, receivers: dict | None, expect_privacy: str = "fully-shielded",
          receivers_error: str | None = None) -> CheckResult:
    cid = "C2"
    if not recipient:
        return not_run(cid, "no recipient address")
    kind, _ = address.classify(recipient)
    ev = {"recipient": r(recipient), "kind": kind}
    strict = expect_privacy == "fully-shielded"
    if kind in ("transparent", "tex"):
        status = FAIL if strict else PASS
        return CheckResult(cid, status, f"recipient is a {kind} address (public recipient and amount)", ev,
                           "Pay a shielded address (unified with only shielded receivers, or Sapling).")
    if kind == "sprout":
        return CheckResult(cid, FAIL, "Sprout addresses are deprecated and cannot receive", ev, "Use a unified address.")
    if kind == "sapling":
        return CheckResult(cid, PASS, "legacy Sapling address: shielded, no transparent receiver", ev)
    if kind != "unified":
        return CheckResult(cid, FAIL, "unrecognised address format", ev, "Check the recipient address string.")
    if receivers is None:
        return not_run(cid, f"z_listunifiedreceivers unavailable ({receivers_error or 'no result'})")
    types = sorted(receivers)
    ev["receivers"] = types
    t = [k for k in types if k in TRANSPARENT_KEYS]
    if t and strict:
        return CheckResult(cid, FAIL, f"unified address contains transparent receiver(s): {t}", ev,
                           "Derive the UA with shielded receivers only, e.g. "
                           "z_getaddressforaccount <account> [\"orchard\",\"sapling\"].")
    if not any(k in ("orchard", "sapling", "ironwood") for k in types):
        return CheckResult(cid, FAIL, "unified address has no shielded receiver", ev, "Use a shielded receiver.")
    return CheckResult(cid, PASS, f"unified address receivers: {types}", ev)
