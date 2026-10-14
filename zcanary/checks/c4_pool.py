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
          "unknown_keys": tx.unknown_keys,
          **_block_note(tx, block_deltas)}
    if tx.unknown_keys:
        return CheckResult(
            cid,
            FAIL,
            "unrecognised transaction format; cannot verify unknown fields: " + ", ".join(tx.unknown_keys),
            ev,
            "Update the transaction parser for these fields before trusting the privacy checks.",
        )
    if not used:
        return CheckResult(cid, FAIL, "no shielded bundle in the transaction", ev, "Send a shielded payment.")
    if expect_pool == "any-shielded":
        if len(used) == 1:
            return CheckResult(cid, PASS, f"single shielded pool used: {used[0]}", ev)
        return CheckResult(cid, FAIL, f"multiple shielded pools used: {used}", ev,
                           "Keep sender funds and recipient receiver in the same pool.")
    if used == [expect_pool]:
        return CheckResult(cid, PASS, f"pool {expect_pool} used as expected", ev)
    if expect_pool == "orchard" and "ironwood" in used:
        ev.update({"orchard_actions": tx.bundles["orchard"].actions, "ironwood_actions": tx.bundles["ironwood"].actions,
                   "reference": "docs/pool-behavior.md"})
        ev.setdefault("block_deltas", None)
        return CheckResult(cid, FAIL,
            "expected pool orchard, but on these versions (Zebra 6.2.3 / Zallet with NU6.3 active) Orchard receivers "
            f"deliver into the Ironwood pool. Evidence: tx has {tx.bundles['orchard'].actions} orchard actions and "
            f"{tx.bundles['ironwood'].actions} ironwood actions (ironwood valueBalance "
            f"{tx.bundles['ironwood'].value_balance_zat} zat); block valuePools deltas: {ev.get('block_deltas')}", ev,
            "Set expect.pool = \"ironwood\" (or \"any-shielded\") for this stack; see docs/pool-behavior.md.")
    return CheckResult(cid, FAIL, f"expected pool {expect_pool}, transaction used {used}", ev,
                       "Set expect.pool to the pool your wallet stack actually uses, or fix note selection.")


def zip317_fee(tx: TxSummary) -> int:
    """ZIP-317 conventional fee: 5000 zat * max(2, logical actions)."""
    sap = tx.bundles["sapling"]
    logical = (max(tx.transparent_in, tx.transparent_out) + max(sap.spends, sap.outputs)
               + tx.bundles["orchard"].actions + tx.bundles["ironwood"].actions)
    return 5000 * max(2, logical)


def check_crossing(tx: TxSummary | None, block_deltas: dict[str, int] | None = None) -> CheckResult:
    """Check per-transaction pool effects against the transaction-derived ZIP-317 fee.

    Positive `valueBalanceZat` means value leaves a shielded pool; negative means it
    enters one. A negative recipient-pool valueBalance is a shielded-pool crossing only
    when positive valueBalance identifies a shielded source pool. Block `valueDeltaZat`
    has the opposite sign and is retained as a cross-check, not used for attribution.
    """
    cid = "C4b"
    if tx is None:
        return not_run(cid, "no decoded transaction")
    vb = {p: tx.bundles[p].value_balance_zat for p in SHIELDED_POOLS}
    total = sum(vb.values())
    fee = None if tx.transparent_in_zat is None else tx.transparent_in_zat - tx.transparent_out_zat + total
    z = zip317_fee(tx)
    neg = {p: v for p, v in vb.items() if v < 0}
    pos = {p: v for p, v in vb.items() if v > 0}
    ev = {"value_balance_zat": vb, "fee_zat": fee, "zip317_fee_zat": z,
          "transparent_in_zat": tx.transparent_in_zat, "transparent_out_zat": tx.transparent_out_zat,
          "negative_pools": neg, "positive_pools": pos, "unknown_keys": tx.unknown_keys,
          **_block_note(tx, block_deltas)}
    if tx.unknown_keys:
        return CheckResult(
            cid,
            FAIL,
            "unrecognised transaction format; cannot verify unknown fields: " + ", ".join(tx.unknown_keys),
            ev,
            "Update the transaction parser for these fields before trusting the privacy checks.",
        )
    if neg and pos:
        crossed = min(sum(-v for v in neg.values()), sum(pos.values()))
        ev["crossed_zat"] = crossed
        return CheckResult(cid, FAIL, f"negative shielded valueBalance {neg}: {crossed} zat entered "
                                      f"{sorted(neg)} from positive source pool(s) {sorted(pos)}; amount is public", ev,
                           "Pay a receiver in the same pool as the sender's notes, or migrate funds deliberately.")
    if len(pos) > 1:
        return CheckResult(cid, FAIL, f"value left multiple shielded pools: {pos}", ev, "Spend from a single pool.")
    if tx.transparent_in or tx.transparent_out:
        return CheckResult(cid, FAIL, f"transparent legs present: {total} zat left {sorted(pos)}, "
                                      f"fee {fee} zat, transparent out {tx.transparent_out_zat} zat (see C3)", ev,
                           "Do not pay or spend transparent outputs.")
    if total != z:
        return CheckResult(cid, FAIL, f"shielded valueBalances sum to {total} zat but the ZIP-317 fee for this tx "
                                      f"is {z} zat, with no transparent legs", ev, "Inspect the transaction manually.")
    return CheckResult(cid, PASS, f"no cross-pool movement: only the fee ({fee} zat) left {sorted(pos)}", ev)
