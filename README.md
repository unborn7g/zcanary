# ZCanary

A regtest-only privacy smoke test for Zcash apps. ZCanary starts a disposable Z3 stack, runs an app adapter (`start`, `pay`, `logs`, `stop`), and checks the resulting transaction plus explicitly captured outputs.

Checks include:

- **C1:** expected network, the node chain and known genesis hash, recipient network prefix, and the app-reported network agree.
- **C2:** strict receiver-set policy. A Unified Address with a transparent receiver fails this address-policy check; that alone does **not** prove the payment used it.
- **C3:** the decoded transaction itself has no transparent inputs or outputs.
- **C4/C4b:** C4 inspects decoded pool bundles; C4b fails on negative shielded valueBalances, value leaving multiple shielded pools, or transparent legs. It reports the derived transaction fee and warns—but does not fail—if it differs from conventional ZIP-317.
- **MEMO:** the recipient wallet decrypts the unique per-run memo marker.
- **C6:** unique memo, label, and test-id markers are searched in configured captured logs/artifacts. Reports include labels, counts, and source names only.

The canary scan is bounded to sources the run actually captures. It is not a guarantee against other leaks, and each report includes a **NOT COVERED** section. Pool behavior is version-specific; see [`docs/version-matrix.md`](docs/version-matrix.md). The default remains the historically runtime-tested Zallet beta.1 baseline until beta.3 passes the complete Docker integration suite.

## Safety

Use only the included disposable **regtest** configuration. Do not point the harness at mainnet, public testnet, production wallets, real keys, or real funds. The stack uses isolated regtest wallet data; never paste RPC passwords, seeds, or keys into chat or reports. Password values are read from environment variables and are not stored in TOML. The default GitHub Actions integration job expects private repository secrets named `ZCANARY_ZEBRA_PASSWORD` and `ZCANARY_ZALLET_PASSWORD`; it fails closed if they are absent.

## Run locally

```bash
python -m pip install -e '.[dev]'
# Set ZCANARY_ZEBRA_PASSWORD and ZCANARY_ZALLET_PASSWORD in your shell first.
# Do not place their values in this repository or on the command line.
zcanary run --config zcanary.toml
```

The six sample apps and isolated configs are under `examples/`. The normal `pytest` command runs unit tests; Docker-backed tests are selected with `ZCANARY_RUN_INTEGRATION=1 python -m pytest -m integration` and require Docker plus the environment-only test RPC credentials.

License: MIT
