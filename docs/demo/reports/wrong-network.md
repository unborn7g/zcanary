# ZCanary regtest report

**Overall:** FAIL
**Network:** regtest
**ZCanary:** 0.0.1
**Elapsed:** 82.09 seconds

## Checks

| Check | Status | Finding |
|---|---|---|
| C1 | FAIL | app reports network 'testnet', config says 'regtest'; app wallet endpoint genesis hash is the testnet genesis, config says 'regtest'; app wallet endpoint genesis hash does not match the node genesis hash |
| C2 | PASS | unified address receivers: ['orchard', 'sapling'] |
| C3 | NOT_RUN | not run: no decoded transaction |
| C4 | NOT_RUN | not run: no decoded transaction |
| C4b | NOT_RUN | not run: no decoded transaction |
| MEMO | NOT_RUN | not run: need z_viewtransaction result and the canary string |
| C6 | PASS | No canary markers found in 10 captured source(s). |

## Steps

- **network.start:** ok (61.41 s)
- **app.start:** skipped (0 s)
- **app.pay:** ok (0.05 s)
- **app.logs[1]:** ok (0.0 s)
- **app.stop:** skipped (0 s)
- **network.stop:** ok (20.62 s)

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
