# Version and evidence matrix

**Updated:** 2026-10-14. This document deliberately separates transaction-level runtime evidence from source/release-note evidence. A configured pin is not a test result.

## Selected default

| Component | Default pin | Why selected | Evidence status |
|---|---|---|---|
| Z3 Compose/config | `ZcashFoundation/z3@e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` | Existing regtest setup, NU5 through NU6.3 activation configuration, and rpc-router wiring; its image defaults are explicitly overridden below. | The previous beta.1 stack was run; the beta.3 combination has **not** been runtime-tested here. |
| Zebra | `zfnd/zebra:6.3.0@sha256:52a67e543906c98a0ed1599e2ce3ee238fc05b40592ae26ee5914ddb6ede51e3` | Supports through NU6.3/Ironwood; immutable multi-platform image-index digest. | Release notes and Docker Hub tag metadata checked; runtime pull/start pending. |
| Zallet | `zodlinc/zallet:v0.1.0-beta.3@sha256:54d1a774317371b1e88c8a54712cdfa4aa7d8f20ae2a0205f9a190e502aef0f6` | Includes the privacy-policy hardening covering all shielded pools, including Ironwood. | Release/source and Docker Hub tag metadata checked; exact RPC behavior is **not yet runtime-verified**. |

`spike.sh` exports the Zebra and Zallet image references to override the beta.1/6.2.3 defaults in the pinned Z3 Compose file. For beta.3, the local generated Zallet regtest config sets `as_of_version = "0.1.0-beta.3"` and `rpc.allow_insecure_remote_bind = true`. The latter is required because this disposable container listens on a non-loopback address inside its Docker network; the option is an explicit plaintext-RPC opt-in. The published host endpoints remain loopback-only in the harness configuration. Do not reuse that opt-in on an exposed or production service.

### beta.3 evidence (source/release-note only)

- Zallet release `v0.1.0-beta.3` is commit `987382f67e622915228686e9f956c6a9c9a7514c`; its notes include the security-hardening work and the explicit opt-in for plaintext JSON-RPC on non-loopback binds: [release](https://github.com/zcash/zallet/releases/tag/v0.1.0-beta.3), [bind-policy PR #750](https://github.com/zcash/zallet/pull/750).
- The privacy-policy source change is commit [`3da74f32ff`](https://github.com/zcash/zallet/commit/3da74f32ff). It checks Sapling, Orchard, and Ironwood shielded-pool crossings and requires `AllowRevealedAmounts` for a crossing. This supports a source-level fix claim only.
- Zebra 6.3.0 release notes report support through NU6.3/Ironwood: [release](https://github.com/ZcashFoundation/zebra/releases/tag/v6.3.0).
- The registry index digests were checked in Docker Hub tag metadata: [Zebra 6.3.0](https://hub.docker.com/v2/repositories/zfnd/zebra/tags/6.3.0) and [Zallet beta.3](https://hub.docker.com/v2/repositories/zodlinc/zallet/tags/v0.1.0-beta.3). This verifies published tag metadata, **not** a pull/start in this workspace.
- `tests/integration/test_beta3_rejects_exact_fullprivacy_ironwood_to_sapling_call` is written to run the exact no-memo, 0.1 ZEC `FullPrivacy` call and expect the policy error. It has not run here because Docker is unavailable.

## Retained beta.1 known-bad fixture

| Component | Fixture pin | Evidence |
|---|---|---|
| Z3 | `e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` | Used in the earlier disposable regtest run. |
| Zebra | `zfnd/zebra:6.2.3@sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2` | Runtime-tested in the earlier stack. |
| Zallet | `zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` | Runtime-tested in the earlier stack. |

`docs/finding-fullprivacy.md` records three successful beta.1 runs on separate regtest chains. The exact call was `z_sendmany(sender Orchard receiver, [{address: recipient Sapling receiver, amount: 0.1}], 1, null, "FullPrivacy")`, with no memo. The decoded transaction had no transparent legs, a negative Sapling valueBalance and positive Ironwood valueBalance. This is prior runtime evidence for beta.1, not beta.3.

The source-level beta.1 explanation and its limits remain in `docs/finding-fullprivacy.md`. The env-only image profile is `fixtures/version-matrix/beta1-known-bad.env`; `scripts/reproduce_fullprivacy_crosspool.py` contains the exact call and validates the decoded result. The CI integration suite attempts both the known-bad beta.1 fixture and the beta.3 rejection. Neither new CI test was executed in this workspace.

## Other version-specific runtime observations

| Observation | Runtime evidence | Scope/limit |
|---|---|---|
| C1 regtest genesis | Zebra 6.2.3 returned `getblockhash 0 = 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`. | Chain name `"test"` alone is ambiguous between testnet and regtest; C1 compares the hash to the known genesis map. The testnet-vs-regtest distinction is covered by a synthetic failing test. The beta.3 node has not been queried here. |
| Orchard receiver placement | On Zebra 6.2.3 / Zallet beta.1 with NU6.3 active, an Orchard receiver delivered into Ironwood; transaction bundle counts/valueBalances and block `valuePools` deltas were captured. | Version-specific transaction evidence. Block aggregates are only a cross-check, not attribution by themselves. Do not generalize to all versions. See `docs/pool-behavior.md`. |
| Fee/cross-pool checks | Fixtures cover a same-pool fee-only control, cross-pool txs, a transparent deshield larger than the derived ZIP-317 fee, and a tiny crossing. | Unit/fixture-tested; no claim that total-delta sums alone identify crossings. C4b compares per-pool effects and derives the fee from the transaction. |

## Reproduction and CI status

- Unit tests have been run locally; the current result is recorded in the project handoff/final response.
- Docker-backed integration tests, image-pull time, and total GitHub Actions time are **pending**. Docker is unavailable in the current workspace, and no remote Actions run/link is available from this session.
- `.github/workflows/ci.yml` records the first Compose service-image pull plus digest-pinned `regtest-init` helper pull, and integration-job elapsed time from the first runner step on a clean runner. It uploads only sanitized JSON/Markdown reports and timing metadata, never wallet volumes or raw app logs.
- The integration job requires private Actions environment secrets `ZCANARY_ZEBRA_PASSWORD` and `ZCANARY_ZALLET_PASSWORD`. Values are not included in this repository or documentation. If either is unset, the integration preflight fails and names only the missing environment variable.

## Explicitly unverifiable here

- Beta.3 runtime rejection of the exact beta.1 FullPrivacy cross-pool call, and the matching beta.1 rerun through the new reproduction script.
- Docker image pull/start, clean-runner cold pull duration, and total CI duration.
- Runtime behavior on platforms other than the local historical x86_64 baseline and the GitHub runner's architecture.
- Public testnet, mainnet, production wallets/funds, NU7, non-configured app logs/artifacts, telemetry outside captured sources, and any guarantee against leaks beyond the explicitly scanned sources.
