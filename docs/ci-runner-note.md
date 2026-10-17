# Zallet beta.1 CI runner connection-pool note

## Working diagnosis

Zallet beta.1 sizes its default deadpool connection pool at **physical cores × 4**. On the constrained CI runner, that produces a four-connection pool. Four long-lived sync tasks each retain a wallet database connection, leaving no free connection for wallet database (wallet-DB) RPCs; those requests can wait indefinitely while basic RPCs still respond.

## Scoped workaround

ZCanary can make the Zallet container see an 8-core `/proc/cpuinfo` file. It contains eight processor records with one physical ID and core IDs 0 through 7, so the default pool is sized to 32 connections. The file is bind-mounted read-only at `/proc/cpuinfo` **only in the disposable Zallet Regtest container**; it does not alter the runner, Zebra, the router, or the pinned Zallet image.

Set `ZCANARY_ZALLET_PHYSICAL_CPU_WORKAROUND` to:

- `auto` (default): enable when fewer than four physical cores are detected.
- `on`: always enable.
- `off`: never enable; use this for the beta.1 control run.

Detection counts unique `physical id`/`core id` pairs in `/proc/cpuinfo`. If that yields no cores, it falls back to the `cpu cores` field, then assumes one core. When enabled, `scripts/spike.sh` writes `z3/zcanary-fake-cpuinfo`, adds the read-only Zallet mount to its generated Compose override, and prints the detected count and reason for enabling it. The wallet-path diagnostic reports the host count and mount status; its BusyBox helper counts processor records through `/proc/1/root/proc/cpuinfo` in Zallet's PID namespace without dumping the file.

The manual diagnostic workflow compares beta.1 with the workaround off and on, then beta.3 with it on. Artifact names retain the `zcanary-wallet-path-diagnostics-<variant>` pattern. The regular CI workflow and default Zallet image pin are unchanged; its `auto` default applies.

This is a diagnostic workaround for the disposable Regtest stack, **not a production fix**. A long-term fix should address the pool sizing or the long-lived connection usage in Zallet itself. No credentials or other secrets belong in this note or in the generated CPU-info file.
