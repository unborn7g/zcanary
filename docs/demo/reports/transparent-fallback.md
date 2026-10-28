# ZCanary regtest report

**Overall:** FAIL
**Network:** regtest
**ZCanary:** 0.0.1
**Elapsed:** 94.09 seconds

## Checks

| Check | Status | Finding |
|---|---|---|
| C1 | PASS | node chain 'test', node/app genesis hashes, app network, recipient prefix, and config all agree on regtest |
| C2 | FAIL | address-policy failure: unified address offers transparent receiver(s): ['p2pkh']; this does not prove the payment used one (see C3 transaction legs) |
| C3 | FAIL | transparent legs present: 0 in, 1 out |
| C4 | PASS | single shielded pool used: ironwood |
| C4b | FAIL | transparent legs present: 50025000 zat left ['ironwood'], fee 25000 zat, transparent out 50000000 zat (see C3) |
| MEMO | FAIL | canary memo not found in the recipient's decrypted outputs |
| C6 | PASS | No canary markers found in 10 captured source(s). |

## Steps

- **network.start:** ok (61.21 s)
- **app.start:** skipped (0 s)
- **app.pay:** ok (12.12 s)
- **app.logs[1]:** ok (0.0 s)
- **app.stop:** skipped (0 s)
- **network.stop:** ok (20.73 s)

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
