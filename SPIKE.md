# ZCanary Day-1 spike (Gate 1)

**Result: Gate 1 PASS**, 2026-10-13 (Lagos time), on a Debian 13 x86_64 box with Docker 29.9.0 and Compose v5.6.0.
`scripts/spike.sh` runs the whole flow from a wiped regtest in about 100 s and exits 0 only when every check passes.

## Pinned versions
| Component | Version |
|---|---|
| Z3 (ZcashFoundation/z3) | `e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` (main, 2026-08-10) |
| Zebra | `zfnd/zebra:6.2.3` (`sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2`), zebrad 6.2.3 |
| Zallet | `zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` (`zallet-zaino`) |
| rpc-router | built from z3 `rpc-router/` at the commit above |
| Zaino | not used (`zingodevops/zainod:0.6.0-no-tls` is pinned behind the `indexer` profile) |

Regtest config: Canopy activates at height 1 and NU5 through **NU6.3** at height 2. In this NU6.3 regtest, an **Orchard receiver in a UA receives notes in the `ironwood` pool**. The wallet's spends and outputs all report `pool: "ironwood"`, and the `orchard` bundle stays empty.

Endpoints: rpc-router `127.0.0.1:8181` (merged Zebra + Zallet), Zebra `127.0.0.1:29232`, Zallet `127.0.0.1:50232`. Auth is `zebra`/`zebra` basic auth.

## Flow (what spike.sh does)
1. `z3/scripts/regtest-init.sh`, then `docker compose --env-file .env.regtest up -d`.
2. `z_getnewaccount sender` and `z_getnewaccount recipient`. The default UA has orchard, sapling and p2pkh receivers. **It has no separate ironwood receiver.**
3. Funding: `generatetoaddress [1, <sender orchard-only UA>]` (Zebra RPC) mines a **shielded coinbase** that lands in ironwood. Then 100 more blocks for coinbase maturity.
   The alternative path also works: mine to the sender's p2pkh, then `z_shieldcoinbase <account_uuid> <orchard UA> null 1`, which gives transparent vin plus ironwood outputs.
4. `z_sendmany <sender orchard UA> [{address: <recipient UA>, amount: 0.5, memo: hex("ZCANARY-xxxx")}] 1 null "FullPrivacy"`, then `z_getoperationresult`, then mine 1 block.
5. Zebra `getrawtransaction [txid,1]` and `getblock [hash,1]`. Zallet `z_viewtransaction`, `z_getbalanceforaccount` and `z_listunspent`.

## Evidence (fresh run)
```
tx 067cf3fc...01ae version 6 {transparent_vin:0, transparent_vout:0, sapling_spends:0, sapling_outputs:0,
   orchard_actions:0, ironwood_actions:5, sprout_js:0}
recipient outputs [('ironwood', 0.5, 'ZCANARY-bf01d8b5')]
block valuePools deltas {'transparent': 625025000, 'ironwood': -25000}   # transparent delta = coinbase
canary in public node data: none     (rawtx/block JSON grep, plain and hex)
GATE1 PASS
```
The canary also appears in 0 lines of the zebra, zallet and rpc-router container logs (`docker compose logs | grep`).
Trimmed RPC outputs are in `docs/spike-outputs/`. `saplingtx.json` and `shieldtx.json` come from the first manual chain, before the reset.

## What the decoded tx exposes (inputs for checks C3/C4)
`getrawtransaction verbose` from Zebra 6.2.3 on a v6 tx returns these top-level keys:
`hex, height, confirmations, in_active_chain, vin, vout, vShieldedSpend, vShieldedOutput, vjoinsplit, orchard, ironwood, valueBalance(Zat), size, time, txid, authdigest, overwintered, version, versiongroupid, locktime, expiryheight, blockhash, blocktime`.
- **Transparent:** `vin[]` gives txid, vout and scriptSig (asm/hex). `vout[]` is standard. Both are empty for the shielded send. The shield-coinbase tx shows `vin` length 1 with the signature and pubkey visible.
- **Sapling:** `vShieldedSpend[]` and `vShieldedOutput[]` (output keys: `cv, cmu, ephemeralKey, encCiphertext, outCiphertext, proof`). The top-level `valueBalance`/`valueBalanceZat` is the **Sapling** value balance.
- **Orchard:** `orchard.{actions[], valueBalance, valueBalanceZat}`. The key is present even when the bundle is empty.
- **Ironwood:** `ironwood.{actions[], valueBalance(Zat), flags{enableOutputs, enableSpends}, anchor, proof, bindingSig}`. Action keys: `cv, nullifier, rk, cmx, ephemeralKey, encCiphertext, spendAuthSig, outCiphertext`.
- **Not exposed:** memos, recipient addresses, per-output amounts (only net valueBalance per pool), and which actions are dummies or padding. The action count varies, for example 2 or 5 for a 1-recipient send. **C4 cannot check the memo from node data. It has to use the recipient wallet (`z_viewtransaction` returns `memo` hex plus `memoStr`) or decrypt with a viewing key.**
- The fee is not exposed directly. It equals the sum of pool valueBalances, e.g. `ironwood.valueBalanceZat = 10000`.
- `getblock [hash,1]` gives `valuePools[]` with `{id: transparent|sprout|sapling|orchard|lockbox|ironwood, chainValueZat, valueDeltaZat}` plus `trees.ironwood.size`, `finalsaplingroot` and `finalorchardroot`. `getblockchaininfo.valuePools` has the same shape without the deltas.
- `decoderawtransaction` exists on Zebra as an alternative for mempool txs.

Suggested C3: `len(vin)==len(vout)==0` and `vjoinsplit==[]`. Suggested C4 (pool): exactly one of sapling, orchard or ironwood is non-empty and matches the expected pool. Also require all other pools' valueBalance to be 0.

## Cross-pool observation
See `docs/finding-fullprivacy.md` (UNCONFIRMED research notes).

## z_listunifiedreceivers
It works. Zebra and Zallet both implement it, and the router answers with Zebra's version.
For the default UA it returns `{orchard, sapling, p2pkh}`.

## Problems hit and workarounds
1. Docker was not installed on the box. Installed it with get.docker.com (Debian trixie, Docker 29.9.0).
2. **Container-to-container traffic timed out.** Leftover iptables-legacy tables set `FORWARD DROP`, and Zallet couldn't reach `zebra:18232`. Fix: `sysctl net.bridge.bridge-nf-call-iptables=0` (spike.sh does this automatically). This is box-specific. Most dev machines won't need it.
3. The rpc-router wedged (connection reset) because it started while Zallet was down. Restarting it fixed this, and spike.sh retries.
4. A memo-less send stores an all-zero memo (`memoStr: ""`), not the `0xF6` no-memo marker. Wallet change outputs carry `0xF6`.
5. There is no `xxd` on the box. Memos are hex-encoded with Python.

## Fallbacks if Z3 breaks
- Pin Zebra one release back with `Z3_ZEBRA_IMAGE=zfnd/zebra:6.1.0`.
- Skip the router and use Zebra `:29232` plus Zallet `:50232` directly. All the steps above work against them.
- Drop the NU6.3 activation lines in `config/regtest/zebra.toml` to test a pure Orchard pool (not tried).
