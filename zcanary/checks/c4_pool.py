"""C4: the shielded pool actually used (from decoded bundles, cross-checked with block valuePools deltas).
C4b: no value crossing between pools beyond the fee.

Driven by docs/pool-behavior.md: on Zebra 6.2.3 + Zallet v0.1.0-beta.1 with NU6.3 active, payments to
Orchard receivers move value through the *ironwood* bundle, so the pool is read from the tx, never
inferred from the address type."""
from __future__ import annotations

from ..tx import SHIELDED_POOLS, TxSummary
from . import FAIL, PASS, CheckResult, not_run


def _block_note(tx: TxSummary, deltas: dict[str, int] | None) -> dict:
    if deltas is None:
        return {"block_deltas": None}
    # per-block deltas include other txs (coinbase etc.); report shielded pools only
    return {"block_deltas": {p: deltas.get(p, 0) for p in SHIELDED_POOLS if deltas.get(p, 0)}}


def check(tx: TxSummary | None, expect_pool: str, block_deltas: dict[str, int] | None = None) -> CheckResult:
    cid = "C4"
    if tx is None:
        return not_run(cid, "no decoded transaction")
    used = tx.pools_used
    ev = {"expect": expect_pool, "pools_used": used,
          "value_balance_zat": {p: tx.bundles[p].value_balance_zat for p in SHIELDED_POOLS},
          **_block_note(tx, block_deltas)}
    if not used:
        return CheckResult(cid, FAIL, "no shielded bundle in the transaction", ev, "Send a shielded payment.")
    if expect_pool == "any-shielded":
        if len(used) == 1:
            return CheckResult(cid, PASS, f"single shielded pool used: {used[0]}", ev)
        return CheckResult(cid, FAIL, f"multiple shielded pools used: {used}", ev,
                           "Keep sender funds and recipient receiver in the same pool.")
    if used == [expect_pool]:
        return CheckResult(cid, PASS, f"pool {expect_pool} used as expected", ev)
    hint = ""
    if expect_pool == "orchard" and "ironwood" in used:
        hint = (" (with NU6.3 active, Orchard receivers receive into the ironwood pool; see docs/pool-behavior.md)")
    return CheckResult(cid, FAIL, f"expected pool {expect_pool}, transaction used {used}{hint}", ev,
                       "Set expect.pool to the pool your wallet stack actually uses, or fix note selection.")


def check_crossing(tx: TxSummary | None, block_deltas: dict[str, int] | None = None) -> CheckResult:
    """A single-pool shielded tx has exactly one non-zero valueBalance (= +fee). A negative valueBalance
    in any shielded pool means value entered that pool from elsewhere in the tx."""
    cid = "C4b"
    if tx is None:
        return not_run(cid, "no decoded transaction")
    vb = {p: tx.bundles[p].value_balance_zat for p in SHIELDED_POOLS}
    nz = {p: v for p, v in vb.items() if v}
    ev = {"value_balance_zat": vb, **_block_note(tx, block_deltas)}
    entering = {p: -v for p, v in nz.items() if v < 0}
    leaving = {p: v for p, v in nz.items() if v > 0}
    if entering and leaving:
        crossed = min(sum(entering.values()), sum(leaving.values()))
        ev["crossed_zat"] = crossed
        return CheckResult(cid, FAIL,
                           f"{crossed} zat moved from {sorted(leaving)} into {sorted(entering)}; amount is public", ev,
                           "Pay a receiver in the same pool as the sender's notes, or migrate funds deliberately.")
    if len(leaving) > 1:
        ev["note"] = "value left more than one shielded pool"
        return CheckResult(cid, FAIL, f"value left multiple shielded pools: {leaving}", ev,
                           "Spend from a single pool.")
    if entering and not tx.transparent_in:
        return CheckResult(cid, FAIL, f"value entered {sorted(entering)} with no visible source", ev,
                           "Inspect the transaction manually.")
    if entering:
        return CheckResult(cid, FAIL, f"transparent value shielded into {sorted(entering)} (see C3)", ev,
                           "Spend shielded funds only.")
    if leaving and tx.transparent_out:
        return CheckResult(cid, FAIL, f"value left {sorted(leaving)} to transparent outputs (see C3)", ev,
                           "Do not pay transparent outputs.")
    return CheckResult(cid, PASS, f"no cross-pool movement (fee only: {leaving or 0})", ev)
