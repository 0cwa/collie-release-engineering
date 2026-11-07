# Release command receipt adapter

The existing local `release_collect.py` → `release_sign.py` → `release_publish.py` route remains the runtime. An independently reviewed **release plan** explicitly chooses `legacy` or `new-source`. Missing new receipts never select legacy. Candidate jobs cannot supply their own approval: privileged operators use separately pinned, trusted tool revisions and obtain approved digests through their existing manual authorization channel. A CLI digest is an input to that policy, not a cryptographic owner signature.

For the existing local schema1 route, propose a plan without changing or upgrading historical evidence:

```sh
python3 tools/patches/runtime_receipts.py import-legacy \
  --manifest manifest.json --engineering-revision "$ENGINEERING_REVISION" \
  --purpose production > legacy-plan.json
```

Review its exact source, engineering, historical workflow/source identity, package, version and existing certificate. Explicitly approve SHA256 of the plan's raw bytes. Pass `--release-plan legacy-plan.json --approved-release-plan-sha256 "$PLAN_SHA256"` to collection, signing and publication along with the existing arguments. For collection before a manifest exists, prepare the same small JSON plan from the approved release inputs; the importer is only an adapter for existing manifests. This does not fabricate source/build receipts for RC1, change the old build route, or authorize reusing a released version for different bytes. Historical signing rehearsal and publication dry-run can still inspect genuine schema1 evidence without a production plan; they reject new-source evidence. The production signer/publisher retain the single-artifact and version-reservation gates.

A release plan has exactly these fields: `schema: 1`, `kind: "release-plan"`, `mode: "legacy"|"new-source"`, `purpose: "production"|"fixture"`, `source_commit`, `engineering_revision`, `workflow_revision`, `package`, `version_code`, `version_name`, `certificate_sha256`, `source_receipt_sha256`, `build_inputs_sha256`, and `required_test_reports`. Legacy explicitly sets the two receipt/input digests to null and report names to `[]`. New-source requires both digests and nonempty distinct report names. Source, engineering and workflow identities can differ in new mode. Fixture plans cannot publish to the production application repository.

## New-source sequence

1. Review the source receipt and stable build inputs, then approve the plan's raw SHA256. The source receipt is the existing strict schema2 source receipt; the plan pins its canonical digest and exact G. Build-input JSON has exactly `schema: 1`, `recipe_revision`, `config_sha256`, `toolchain_sha256`, `dependencies_sha256`, `native_inputs_sha256`, `trust_level`. Its raw SHA256 is pinned by the plan. These identify declared inputs; trusted-local native bytes remain trusted-local unless independently reproduced.
2. Build G without source injection. Invoke collection with existing flags plus the approved plan, `--source-receipt`, `--build-inputs`, `--source-checkout`, `--toolchain-input`, `--dependency-input`, `--native-input`, and `--test-reports`. The actual input-file hashes must match the approved build-input document. Collection checks the exact checkout commit/tree and every tracked raw blob, file type, Git executable bit and symlink target without worktree conversion filters or cached index stat data. It reads the original index only for hidden-flag/staged-change rejection and leaves its bytes unchanged; unsupported gitlinks and symlinked parent directories fail closed. Source checkouts must therefore have the approved raw bytes, not locally converted equivalents. It also rejects hostile/incomplete Git stores, validates actual APK metadata/digest, and writes `build-receipt.json`. It cannot prove an arbitrary report is truthful; it records typed operator reports and their actual bytes, not independent reproduction.
3. Independently review the resulting build receipt, then supply its **canonical** digest as `--approved-build-receipt-sha256` and `--build-receipt` to signing. Signing also requires the plan/source/build-input documents. This approval occurs after collection, avoiding a future-build-receipt hash cycle. Required scoped source/build evidence must pass before key access. Keep broader unexecuted acceptance in the runbook and trust declaration; never relabel it PASS. Sign once using the operator's existing certificate; copy the same signed bytes to all five aliases.
4. Signing writes raw `final-manifest.json` schema2 with independent source/engineering/workflow IDs, source/build receipt digests and plan digest. It then writes schema2 `release-receipt.json`, binding the **raw final-manifest SHA256** and signed APK/certificate/aliases. The final manifest never embeds the later release-receipt digest. Legacy raw manifests remain schema1.
5. Independently approve the release receipt's canonical digest. Publication additionally requires `--release-receipt` and `--approved-release-receipt-sha256`. It validates the complete approved source/build/release graph and raw-final binding before any GitHub operation. The public bundle carries `source.json`, `build.json`, `release.json`, `final-manifest.json` and existing artifacts. Pass those same graph files/digests to distribution schema2 approval and ingestion. `release_manifest_sha256` always means raw final-manifest bytes; `release_receipt_sha256` always means the canonical receipt.
6. The existing publisher resumes interrupted draft/upload/publish operations using verified identical bytes, signed tag, channel and asset set. It never rebuilds during retry. Repository/Pages publication remains a separate approved operation coordinated by the existing promotion journal.

The report descriptor has exactly `schema: 1` and `reports`. Each report contains `name`, `status` (`PASS`, `FAIL`, `NOT RUN`, `BLOCKED`, `UNKNOWN`), `description`, `file`, `sha256`. Names must exactly match the plan. File paths are relative to the descriptor without traversal/symlinks, hashes bind actual bytes, and PASS requires a real report file. A null file requires null digest and cannot PASS.

## Executed fixture and limits

`tests/runtime_pipeline_smoke.py` executes the actual collector, one actual test-key APK signing and actual publisher logic with only GitHub transport mocked. It checks distinct source/engineering/workflow IDs, missing/substituted receipts/APK/raw-final/approval inputs, same graph export, five byte-identical aliases, and interrupted publication resume. Recreate with a disposable scratch directory and installed public Android SDK tools:

```sh
agent-heavy -- python3 tests/runtime_pipeline_smoke.py \
  --build-tools "$ANDROID_SDK_ROOT/build-tools/36.0.0" \
  --android-jar "$ANDROID_SDK_ROOT/platforms/android-36.1/android.jar" \
  --scratch "$TASK_SCRATCH/runtime-fixture" \
  --distribution-repo "$ENGINEERING_DISTRIBUTION_CHECKOUT"
```

The optional distribution checkout runs actual ingestion against the exact runtime-emitted schema2 raw final manifest and exported receipt bundle, checks immutable/idempotent copying, and rejects a missing graph. Its production certificate pin first rejects the test signer; only an explicit fixture-scoped patch supplies the disposable expected certificate. Production code and pins stay intact.

This uses a tiny resource-only fixture APK, synthetic Git source, disposable key, and typed fixture reports. It is **not** compilation of Collie G, production signing, account/device upgrade acceptance, public native availability, or independent native reproduction. No production API publication is exercised. Delete its generated APK, key and build directory after review, retaining only its text summary.
