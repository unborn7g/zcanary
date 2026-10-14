# Beta.1 runtime regression fixture: `FullPrivacy` crossed Ironwood → Sapling

**Status:** The beta.1 behavior below was reproduced at runtime on three separate regtest chains. It is retained as a known-bad beta.1 fixture, not as a general vulnerability classification. Beta.3's fix is supported by source/release notes only until the exact call is rerun against that image.

## Environment (pinned)
- Zallet `zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` (source tag `v0.1.0-beta.1`, commit `5be0f4861feedc47978102c627c6293dea2d7838`)
- Zebra `zfnd/zebra:6.2.3`, Z3 `e84ce9fd`, regtest with NU5 through NU6.3 active at height 2
- Sender account funds: ironwood pool only (`fixtures/finding-fullprivacy/sender_balance_before.json`)

## Observations
I made the same call 3 times on separate chains and in separate sessions (2026-10-13, Lagos). All 3 succeeded with the same pool pattern. The third run's evidence is in `fixtures/finding-fullprivacy/`; the second is `fixtures/pools/c_sapling_*`.

Request (`request.json`; addresses shortened here):
```json
{"method":"z_sendmany","params":["uregtest1…<sender orchard-only UA>",
  [{"address":"zregtestsapling1…<recipient sapling receiver>","amount":0.1}],1,null,"FullPrivacy"]}
```
Response: `{"result":"opid-4543e88d-…"}`. `z_getoperationresult` reports `status: "success"` with txid `9e533da10b88b21e4920bae561c04838f14ee3dcc746b5dcf0dd1d7dfe59e240`.

Decoded tx (`rawtx.json`) and block (`block.json`, height 208, nTx=2):
| source | sapling | orchard | ironwood | transparent |
|---|---|---|---|---|
| tx bundles | 0 spends, 2 outputs, valueBalanceZat −10000000 | 0 actions | 2 actions, valueBalanceZat +10020000 | vin 0 / vout 0 |
| block valuePools valueDeltaZat | +10000000 | 0 | −10020000 | +625020000 (coinbase 625000000 + fee 20000) |
| getblockchaininfo chainValue diff (before→after) | +10000000 | 0 | −10020000 | +625020000 |

`z_viewtransaction`: spends `[ironwood 143743750]`; outputs `[sapling 10000000 (recipient), ironwood 133723750 (change)]`.
The 0.1 ZEC moved between pools appears in the public `valueBalance` fields. `zcanary` check C4b reports: "10000000 zat moved from ['ironwood'] into ['sapling']; amount is public".

Zallet's own help text for this version (`help_z_sendmany_privacy_policy.txt`) says:
> `"FullPrivacy"`: Only allow fully-shielded transactions (involving a single shielded value pool).
> `"AllowRevealedAmounts"`: Allow funds to cross between shielded value pools, revealing the amount that crosses pools.

For comparison, a transparent recipient under `FullPrivacy` is rejected synchronously with error −8.

## Source reading (v0.1.0-beta.1, read-only)
- `zallet-core/src/components/json_rpc/payments.rs`, `enforce_privacy_policy`: the shielded-crossing branches only test `input_in_pool(ORCHARD) && has_sapling_recipient` and `input_in_pool(SAPLING) && has_orchard_recipient`. There is no branch for IRONWOOD inputs. If the inputs here are classified as Ironwood, no branch fires and the function returns Ok. **This is an inference from reading the code. I did not trace it at runtime.**
- `z_send_many.rs` pre-checks use `max_sapling_available = MAX_MONEY` placeholders (comment cites open issue zcash/zallet#257, "Fetch the real maximum per-pool balances to correctly detect incompatible privacy policies"). With those placeholders, a Sapling recipient passes the pre-check.

## Upstream tracker and history (read-only search)
- Commit `3da74f32ff` (2026-07-31, first released in **v0.1.0-beta.3**, 2026-08-26), "rpc: Enforce privacy policy across all shielded pool crossings". From its message: "so value crossing into any pool (including Ironwood) requires AllowRevealedAmounts".
- Commit `766e31528c`, "rpc: Unit test privacy-policy and action-limit pool coverage" (also in beta.3).
- Issue #693 (open): asks for test builders so privacy-policy enforcement can be unit tested. It references the refactor in #677.
- Issue #257 (open): per-pool maximums, cited above.
- Searches for "FullPrivacy sapling", "RevealingSaplingAmount", "privacy policy ironwood" and "ironwood sapling cross pool" turned up no issue that describes this exact observation.

## Not verified
- I did not run the newer images (v0.1.0-beta.2 / beta.3). Whether beta.3 rejects this call is untested; Z3 still pins beta.1.
- I did not trace which `enforce_privacy_policy` branch ran (no debug build or logging).
- I did not test the case where the sender holds Sapling funds and pays an Orchard receiver.
- I have not ruled out that intended semantics differ for Ironwood (for example, migration-related exceptions); I found no documentation saying so.
- Behavior without NU6.3 active is untested.

## Reproduce
The exact call is implemented in `scripts/reproduce_fullprivacy_crosspool.py`. For beta.1, source `fixtures/version-matrix/beta1-known-bad.env`, set the two regtest RPC password environment variables, run `scripts/prepare-regtest.sh`, run the Python reproducer, and stop with `scripts/stop-regtest.sh`. The beta.3 integration test uses the same call and expects a policy rejection. Both new Docker-backed runs are pending in this workspace.
