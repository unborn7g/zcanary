# Pool behavior on the historical beta.1 regtest (Zebra 6.2.3, Zallet v0.1.0-beta.1, Z3 e84ce9fd; NU5 through NU6.3 active at height 2)

Setup: the sender's spendable Orchard-receiver notes were in the **Ironwood** pool. Spending was always from the sender's Orchard receiver. Each payment is 0.1 ZEC (10000000 zat), uses privacy policy `FullPrivacy`, and is mined alone in its own block (nTx=2: coinbase + payment). These are version-specific historical observations, not beta.3 results.
Reproduce using the beta.1 profile in `fixtures/version-matrix/beta1-known-bad.env`, `scripts/prepare-regtest.sh`, then `scripts/pool_probe.sh`. For the exact FullPrivacy-to-Sapling call, use `scripts/reproduce_fullprivacy_crosspool.py`. Raw evidence is in `fixtures/pools/`: `*_sendmany_response.json`, `*_op.json`, `*_rawtx.json` (getrawtransaction verbose) and `*_block.json` (getblock 1).

| case | tx bundles (count, valueBalanceZat) | block valuePools valueDeltaZat (non-zero) |
|---|---|---|
| a_orchard_only | transparent vin=0 vout=0; sapling spends=0 outputs=0 vb=0; orchard actions=0 vb=0; ironwood actions=2 vb=10000 | {'transparent': 625010000, 'ironwood': -10000} (block 106, nTx=2) |
| b_unified | transparent vin=0 vout=0; sapling spends=0 outputs=0 vb=0; orchard actions=0 vb=0; ironwood actions=2 vb=10000 | {'transparent': 625010000, 'ironwood': -10000} (block 107, nTx=2) |
| c_sapling | transparent vin=0 vout=0; sapling spends=0 outputs=2 vb=-10000000; orchard actions=0 vb=0; ironwood actions=2 vb=10020000 | {'transparent': 625020000, 'sapling': 10000000, 'ironwood': -10020000} (block 108, nTx=2) |

The block's transparent delta always includes the coinbase (6.25 ZEC subsidy = 625000000 zat) plus the miner's fee.

## What the data shows
- (a) Orchard-only receiver: value moved through the **ironwood** bundle only. The `orchard` bundle was empty. The ironwood pool fell by exactly the fee (10000).
- (b) Unified address (orchard + sapling + p2pkh receivers): same as (a). Only ironwood was used, and Zallet picked the shielded receiver matching the source pool.
- (c) Sapling receiver: 2 Sapling outputs and 2 ironwood actions. The ironwood valueBalance was +10020000 and the Sapling valueBalance was −10000000. The block deltas agree: sapling +10000000, ironwood −10020000. **0.1 ZEC crossed from ironwood to sapling**, and the amount is publicly visible in the valueBalance fields. Zallet accepted this under `FullPrivacy`.
- In every case, a pool's change in the block's `valuePools.valueDeltaZat` equals minus that bundle's `valueBalanceZat` from the decoded tx.
- The `orchard` pool chain value stayed 0 throughout. No Orchard-pool value can be produced with these versions, because Orchard receivers receive into ironwood.

## Not tested / not shown by this data
- Sapling-funded senders (sapling to sapling, sapling to UA).
- Any spend that actually uses the Orchard pool.
- Behavior before NU6.3 activation.

## Signed pool-delta convention and implications for checks

For decoded transaction `valueBalanceZat`, **positive means value leaves that pool** and **negative means value enters that pool**. Zebra block `valueDeltaZat` uses the opposite sign for the transaction contribution (and also includes other transactions such as coinbase), so block values are a cross-check rather than attribution by themselves. A same-pool send can have one positive valueBalance equal to the fee. A cross-pool send can still have all-pool valueBalances summing to the fee; aggregate sum alone cannot identify the crossing.

- C4 must classify by **decoded bundles** (non-empty bundle / non-zero valueBalance), not by address type. In this version/config an Orchard receiver yields Ironwood.
- C4b derives the transaction fee from transparent in/out amounts and shielded valueBalances, compares it with ZIP-317, and examines **each pool's signed transaction `valueBalanceZat`**. A negative transaction valueBalance indicates value entered that pool; the corresponding positive valueBalance identifies a shielded source pool. The block's opposite-signed valueDelta alone is not used to infer a crossing. Same-pool fee-only controls are required to avoid treating the ordinary fee as a crossing.
