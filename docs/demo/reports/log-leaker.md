# ZCanary regtest report

**Overall:** FAIL
**Network:** regtest
**ZCanary:** 0.0.1
**Elapsed:** 95.78 seconds

## Checks

| Check | Status | Finding |
|---|---|---|
| C1 | PASS | node chain 'test', node/app genesis hashes, app network, recipient prefix, and config all agree on regtest |
| C2 | PASS | unified address receivers: ['orchard', 'sapling'] |
| C3 | PASS | no transparent inputs or outputs |
| C4 | PASS | single shielded pool used: ironwood |
| C4b | PASS | no negative shielded valueBalances; value left at most one pool (['ironwood']); no transparent legs; derived fee 25000 zat |
| MEMO | PASS | recipient wallet decrypted the canary memo |
| C6 | FAIL | Canary or configured sensitive marker(s) were found in captured output: label, memo, test_id. |

## Steps

- **network.start:** ok (60.81 s)
- **app.start:** skipped (0 s)
- **app.pay:** ok (14.14 s)
- **app.logs[1]:** ok (0.0 s)
- **app.stop:** skipped (0 s)
- **network.stop:** ok (20.8 s)

## NOT COVERED

- Network timing, IP/transport metadata, and observers outside the captured app outputs.
- App databases, analytics, telemetry, crash dumps, or logs not explicitly configured for capture.
- Mainnet/production behavior, real funds, and production credentials; this workflow is regtest-only.
- Wallet/node combinations beyond those separately identified in docs/version-matrix.md.
- Future transaction fields and pools are not decoded; detected unknown transaction fields make C3/C4/C4b fail closed, but those formats remain unverified.
- C4b reports the transaction-derived fee and warns when it differs from conventional ZIP-317; it does not certify miner fee policy or infer hidden transparent input values.
- NU7-era behavior and public testnet behavior, which are outside this phase's scope.
- A complete privacy/security audit or a guarantee that no sensitive data can leak.

Canary scanning is limited to the sources listed in the JSON evidence; it is not a general leak-prevention guarantee.
