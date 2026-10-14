# Version and evidence matrix

**Updated:** 2026-10-14. This matrix distinguishes runtime observations from source, release-note, and registry-metadata claims. “Yes” applies only to the named versions and scenario; it is not a universal compatibility claim.

## Selected default: beta.1 baseline, pending beta.3 runtime proof

Beta.3 has not passed the full runtime suite in this workspace, so the default remains the previously runtime-tested beta.1 stack. Do not change the default to beta.3 until the exact policy regression and the complete sample-app suite pass at runtime.

| Component | Default pin | Reason | Verified at runtime? |
|---|---|---|---|
| Z3 Compose/config | `ZcashFoundation/z3@e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` | Existing isolated regtest setup, NU6.3 activation configuration, and rpc-router wiring. | yes — historical beta.1 stack |
| Zebra | `zfnd/zebra:6.2.3@sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2` | Compatible with the historical stack and the retained fixtures. | yes — historical beta.1 stack |
| Zallet | `zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` | Known-bad baseline retained for regression testing. | yes — the exact FullPrivacy crossing was reproduced on three separate regtest runs |

`scripts/spike.sh` defaults to those beta.1 pins. For beta.3, it patches the generated regtest config to set `as_of_version = "0.1.0-beta.3"` and `rpc.allow_insecure_remote_bind = true`. That opt-in is for the disposable container's non-loopback bind only; the published host endpoints remain loopback-only. Do not reuse it on an exposed or production service.

## Beta.3 candidate pins and source/release evidence

| Claim | Evidence | Verified at runtime? |
|---|---|---|
| Zallet beta.3 is a candidate policy fix | Release `v0.1.0-beta.3`, commit `987382f67e622915228686e9f956c6a9c9a7514c`; notes describe hardening and the non-loopback JSON-RPC opt-in: [release](https://github.com/zcash/zallet/releases/tag/v0.1.0-beta.3), [bind-policy PR #750](https://github.com/zcash/zallet/pull/750). | no — release/source evidence only |
| The privacy-policy source change covers Ironwood crossings | Commit [`3da74f32ff`](https://github.com/zcash/zallet/commit/3da74f32ff) checks Sapling, Orchard, and Ironwood crossings and requires `AllowRevealedAmounts`. | no — source evidence only |
| Zebra 6.3.0 supports NU6.3/Ironwood | [Zebra 6.3.0 release notes](https://github.com/ZcashFoundation/zebra/releases/tag/v6.3.0). | no — release-note evidence only |
| Candidate immutable image references | Docker Hub tag metadata: [Zebra 6.3.0](https://hub.docker.com/v2/repositories/zfnd/zebra/tags/6.3.0), [Zallet beta.3](https://hub.docker.com/v2/repositories/zodlinc/zallet/tags/v0.1.0-beta.3). Metadata is not a pull or startup test. | no |
| Exact beta.3 FullPrivacy policy result | `tests/integration/test_beta3_rejects_exact_fullprivacy_ironwood_to_sapling_call` runs the no-memo, 0.1 ZEC `FullPrivacy` call and expects a policy rejection. | no — Docker integration has not run here |

The beta.3 candidates are Zebra `zfnd/zebra:6.3.0@sha256:52a67e543906c98a0ed1599e2ce3ee238fc05b40592ae26ee5914ddb6ede51e3` and Zallet `zodlinc/zallet:v0.1.0-beta.3@sha256:54d1a774317371b1e88c8a54712cdfa4aa7d8f20ae2a0205f9a190e502aef0f6`. The integration test sets both references explicitly so a beta.1 environment profile cannot accidentally leak into the beta.3 case.

## Retained beta.1 known-bad fixture

| Component | Fixture pin | Verified at runtime? |
|---|---|---|
| Z3 | `e84ce9fd8e864ff0b2a8a62f6ce14392145db0fb` | yes |
| Zebra | `zfnd/zebra:6.2.3@sha256:bb2a6029db277ee3a10e951dcc0ddd36b4cbcbe0fad684746d695ee21d53fde2` | yes |
| Zallet | `zodlinc/zallet:v0.1.0-beta.1@sha256:1849b4469875dc0165942c06d15fa6a7da76b2d43bade578cc8e5903a639869d` | yes |

`docs/finding-fullprivacy.md` records three beta.1 regtest runs of the exact call: `z_sendmany(sender Orchard receiver, [{address: recipient Sapling receiver, amount: 0.1}], 1, null, "FullPrivacy")`, with no memo. The decoded transaction had no transparent legs, a negative Sapling valueBalance, and a positive Ironwood valueBalance. This is beta.1 runtime evidence, not beta.3 evidence. The source explanation for beta.1 remains an inference.

`fixtures/version-matrix/beta1-known-bad.env` retains the image profile, and `scripts/reproduce_fullprivacy_crosspool.py` makes the exact call. The new integration rerun against beta.1 and the rejection against beta.3 are both pending because Docker is unavailable here.

## Other version-specific observations

| Claim | Runtime evidence | Verified at runtime? | Scope/limit |
|---|---|---|---|
| C1 regtest genesis | Zebra 6.2.3 returned `getblockhash 0 = 029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`. | yes — historical pinned node | `chain="test"` alone is ambiguous; C1 also compares the genesis hash to the known map. The beta.3 node has not been queried. |
| Orchard receiver placement | On Zebra 6.2.3 / Zallet beta.1 with NU6.3 active, an Orchard receiver delivered into Ironwood; transaction bundle counts and valueBalances were captured. Block `valuePools` deltas were a cross-check. | yes — those versions only | Do not generalize to beta.3 or other activation schedules; block aggregates do not attribute a crossing by themselves. See `docs/pool-behavior.md`. |
| C4b same-pool and cross-pool behavior | Historical same-pool fee-only and Ironwood-to-Sapling transaction fixtures; unit tests also cover tiny crossing and a transparent deshield larger than the derived fee. | yes — historical fixtures; synthetic cases are test-only | No claim that a total-delta sum alone identifies a crossing. Per-pool transaction valueBalances are compared using the documented sign convention. |

## Reproduction and CI status

| Claim | Status | Verified at runtime? |
|---|---|---|
| Unit tests | Latest local result: 76 passed, 8 skipped; non-integration: 76 passed, 8 deselected. | yes — local unit/fixture tests |
| Docker-backed integration and six apps | Eight integration cases are written; Docker is unavailable in this workspace. | no |
| Cold image-pull and total GitHub Actions time | The workflow records the initial Compose service-image plus pinned `regtest-init` helper pull and integration-job elapsed time. | no — no Actions run/link yet |
| CI credentials | Private Actions secrets `ZCANARY_ZEBRA_PASSWORD` and `ZCANARY_ZALLET_PASSWORD` are required; values are not in the repository. | no — remote CI has not run |

The CI workflow uploads only sanitized JSON/Markdown reports and timing metadata, not wallet volumes or raw app logs. Reports include `NOT COVERED`; the marker scan applies only to configured captured sources and uploaded artifacts.

## Explicitly unverifiable here

- Beta.3 runtime rejection of the exact beta.1 FullPrivacy cross-pool call, and the matching rerun through the new beta.1 reproduction script.
- Docker image pull/start, clean-runner cold pull duration, and total CI duration.
- Runtime behavior on architectures other than the historical x86_64 baseline and the GitHub runner's architecture.
- Public testnet, mainnet, production wallets/funds, NU7, non-configured app logs/artifacts, telemetry outside captured sources, and any guarantee against leaks beyond the explicitly scanned sources.
