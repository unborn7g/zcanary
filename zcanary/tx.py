"""Classify a decoded transaction (getrawtransaction verbosity 1, Zebra 6.2.x shape)."""
from __future__ import annotations

from dataclasses import dataclass, field

SHIELDED_POOLS = ("sapling", "orchard", "ironwood")


@dataclass
class Bundle:
    pool: str
    spends: int = 0
    outputs: int = 0
    actions: int = 0
    value_balance_zat: int = 0

    @property
    def used(self) -> bool:
        return bool(self.spends or self.outputs or self.actions or self.value_balance_zat)


@dataclass
class TxSummary:
    txid: str
    version: int
    coinbase: bool
    transparent_in: int
    transparent_out: int
    sprout_joinsplits: int
    bundles: dict[str, Bundle] = field(default_factory=dict)
    unknown_keys: list[str] = field(default_factory=list)

    @property
    def pools_used(self) -> list[str]:
        return [p for p in SHIELDED_POOLS if self.bundles[p].used]

    def evidence(self) -> dict:
        return {
            "txid": self.txid, "version": self.version, "coinbase": self.coinbase,
            "transparent_in": self.transparent_in, "transparent_out": self.transparent_out,
            "sprout_joinsplits": self.sprout_joinsplits,
            "bundles": {p: vars(b) for p, b in self.bundles.items()},
        }


_KNOWN = {"hex", "height", "confirmations", "in_active_chain", "vin", "vout", "vShieldedSpend", "vShieldedOutput",
          "vjoinsplit", "orchard", "ironwood", "valueBalance", "valueBalanceZat", "size", "time", "txid",
          "authdigest", "overwintered", "version", "versiongroupid", "locktime", "expiryheight", "blockhash",
          "blocktime", "bindingSig", "joinSplitPubKey", "joinSplitSig"}


def classify(tx: dict) -> TxSummary:
    vin = tx.get("vin") or []
    coinbase = any("coinbase" in i for i in vin)
    sap = Bundle("sapling", spends=len(tx.get("vShieldedSpend") or []), outputs=len(tx.get("vShieldedOutput") or []),
                 value_balance_zat=int(tx.get("valueBalanceZat") or 0))
    bundles = {"sapling": sap}
    for p in ("orchard", "ironwood"):
        b = tx.get(p) or {}
        bundles[p] = Bundle(p, actions=len(b.get("actions") or []), value_balance_zat=int(b.get("valueBalanceZat") or 0))
    return TxSummary(
        txid=tx.get("txid", ""), version=int(tx.get("version", 0)), coinbase=coinbase,
        transparent_in=0 if coinbase else len(vin), transparent_out=len(tx.get("vout") or []),
        sprout_joinsplits=len(tx.get("vjoinsplit") or []), bundles=bundles,
        unknown_keys=sorted(set(tx) - _KNOWN),
    )
