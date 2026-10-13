"""MEMO: recipient wallet decrypts the canary memo (Zallet z_viewtransaction)."""
from __future__ import annotations

from . import FAIL, PASS, CheckResult, not_run


def check(view: dict | None, canary: str | None, recipient_account: str | None = None) -> CheckResult:
    cid = "MEMO"
    if view is None or not canary:
        return not_run(cid, "need z_viewtransaction result and the canary string")
    outs = [o for o in view.get("outputs", []) if not o.get("walletInternal") and not o.get("outgoing")]
    if recipient_account:
        outs = [o for o in outs if o.get("account_uuid") == recipient_account]
    found = [o for o in outs if o.get("memoStr") == canary]
    ev = {"recipient_outputs": len(outs), "pools": sorted({o.get("pool", "?") for o in outs}),
          "memo_match": bool(found)}
    if found:
        return CheckResult(cid, PASS, "recipient wallet decrypted the canary memo", ev)
    return CheckResult(cid, FAIL, "canary memo not found in the recipient's decrypted outputs", ev,
                       "Make sure the app attaches the memo and pays a shielded receiver.")
