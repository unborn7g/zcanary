# ZCanary

A privacy smoke test for Zcash apps. ZCanary starts a throwaway Zcash regtest network
(Zebra + Zallet via [Z3](https://github.com/ZcashFoundation/z3)), has your app send a shielded payment
carrying a canary memo, and then checks the result:

- C1: the node, the address, and the config all agree on the network
- C2: the recipient address has no transparent receivers
- C3: the transaction has no transparent inputs or outputs
- C4: the funds moved in the expected shielded pool (and C4b: no value crossed pools beyond the fee)
- the recipient can read the memo, and no canary shows up in public data or logs

Status: early work in progress (Zecathon 2026). Regtest only. Never use real keys.

```bash
pip install -e .[dev]
zcanary run --config zcanary.toml
```

License: MIT
