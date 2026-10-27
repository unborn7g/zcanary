# CI runner note: Zallet and small machines

## What we saw

On a 2-CPU GitHub runner (the size private repositories get), Zallet's wallet-database RPCs (`z_listaccounts`, `z_getnewaccount`, `listaddresses`, `getwalletstatus` and similar) never answered, and the RPC server eventually returned an empty reply. RPCs that do not touch the wallet database (`help`, `rpc.discover`, `getwalletinfo`) answered in about a millisecond. This happened with Zallet `v0.1.0-beta.1`, `beta.2` and `beta.3`, with the password and call order ruled out.

On a 4-CPU machine (a GitHub Codespace, and the 4-vCPU runner public repositories get), the same code and the same images start normally and the full integration suite passes (8 of 8 on 2026-10-27, Codespace, commit `37b744c`).

## Leading hypothesis (not confirmed)

Zallet keeps its wallet database connections in a pool whose default size appears to scale with the number of CPUs. Four long-running sync tasks each hold one connection for the life of the process. On a 2-CPU machine the pool would then be about four connections, leaving none free for RPC requests. We could not download the Zallet source or build a patched image in our environment to prove this, so treat it as a hypothesis.

## What did not help

- Bind-mounting a fake `/proc/cpuinfo` with more cores into the Zallet container (an earlier workaround, still present in `scripts/spike.sh` behind `ZCANARY_ZALLET_PHYSICAL_CPU_WORKAROUND`; set it to `off`).
- Raising `TOKIO_WORKER_THREADS`.

## What to do

Run ZCanary on a machine with at least 4 CPUs. In GitHub Actions that means a public repository on a standard runner, or a larger runner for a private one. If you can build Zallet yourself, a useful upstream test is to raise the pool size or release the long-lived connections and see whether the hang disappears.
