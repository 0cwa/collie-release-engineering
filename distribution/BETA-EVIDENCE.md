# TrailBlazer Beta pre-readiness evidence

`beta_evidence.py` builds and verifies the immutable evidence archive used before
the Android Beta readiness run. It does not sign artifacts, upload a GitHub release
asset, create a readiness artifact, approve a Pages promotion, or publish a feed.
Stable release tooling and trust defaults are unchanged.

## Fixed archive contents

The packer accepts only regular, single-link files at these paths:

Each `release_id` is the authoritative GitHub release ID for the approved draft,
not a workflow run ID or an operator-defined sequence number. Verification fetches
that exact release and checks its ID, tag, title, prerelease state and APK digest.

```text
releases/<positive-release-id>.json
bundles/<release-id>/{source,build,release}.json
bundles/<selected-release-id>/preparation.json
repository-receipt.json
manifests/unsigned-manifest.json
manifests/final-manifest.json
apk/trailblazer-beta.apk
metadata/im.collie.app.trailblazer.yml
candidate/<Beta public repository files>
```

`candidate/` uses the existing Beta F-Droid public-path allowlist and contains the
signed indexes and every indexed APK. The generated `bundle.json` records the
sorted file inventory, byte lengths and SHA256 values. The tar uses sorted USTAR
regular files, mode `0644`, zero timestamps and no owner data. A second pack of
unchanged inputs produces the same archive bytes. The complete tar is capped at
950,000,000 bytes, 40,000 input files, and 32 MiB for its manifest. Pages approval,
readiness JSON/artifact data, unknown paths, symlinks, hardlinks, special files,
duplicate tar members and unsafe extraction paths are excluded or rejected.

The verifier checks the existing schema 2 source/build/release receipt graph and
the selected Beta release's canonical preparation companion. It binds that record
to the unique BUILD plan and preparation digest markers, source tree/commit,
channel and version. It has no source Git checkout and validates the approved
record bindings without rerunning Git preparation checks. The operator must
review the actual project log and changelog before approving the BUILD receipt.
It also verifies approved release records and both manifests; the exact
TrailBlazer Beta package,
APK, version, tag, title and signer; actual APK bytes and `aapt`/`apksigner`
metadata; signed v1/v2 F-Droid indexes with the pinned repository certificate,
address, package, APK parity and absent-or-empty `releaseChannels`; and the
prepublication repository evidence (`local index PASS`, `client PASS`, production
HTTPS readback `NOT RUN`). It verifies the exact successful unsigned build run
through GitHub's run-attempt API, the signed source tag, and the corresponding
draft prerelease and uploaded APK asset. Fixed Beta metadata must match the bytes
at the immutable engineering revision named by the repository receipt.

## Pack and record the independent approval

Run in the pinned engineering Python environment with `fdroidserver`, and provide
the Android SDK `aapt` and `apksigner` binaries:

```sh
python distribution/beta_evidence.py pack <verified-input-dir> <new-output.tar> \
  --aapt <android-build-tools>/aapt \
  --apksigner <android-build-tools>/apksigner
```

The command validates actual inputs and external GitHub provenance before writing
the archive. It prints the complete archive SHA256 and byte length. An independent
operator approval must bind that SHA256 to the exact uploaded
`trailblazer-beta-release-evidence.tar` asset ID in the fixed `0cwa/collie-release-engineering`
repository. This CLI deliberately has no upload or approval command; it accepts no
arbitrary repository or download URL.

## Strict local archive verification

Before the readiness job, any authorized verifier can rerun the full graph against
the local archive, while checking its SHA and size against the exact uploaded
engineering release asset ID:

```sh
install -d -m 700 <private-scratch-dir>
python distribution/beta_evidence.py verify <evidence.tar> \
  --bundle-sha256 <independently-approved-complete-tar-sha256> \
  --engineering-asset-id <fixed-repository-release-asset-id> \
  --scratch <private-scratch-dir> \
  --aapt <android-build-tools>/aapt \
  --apksigner <android-build-tools>/apksigner
```

The verifier checks GitHub asset metadata in the fixed engineering repository,
then the local complete archive bytes and every receipt/index/APK/provenance link.
It prints a compact verified identity summary and does not produce a readiness
record.

## Pre-index candidate input

Repository generation consumes a separate `trailblazer-beta-release-input.tar`
asset. Its exact contents are one selected release approval, its source/build/
release receipts, the unsigned/final manifests and `apk/trailblazer-beta.apk`.
It excludes all repository indexes, repository receipt and readiness/Pages data.
The operator stages this fixed asset in an engineering-owned draft release and
approves its numeric asset ID plus complete raw SHA256. The candidate job can read
that same-repository draft asset with its own token. It does not attempt to read an
Android draft release, which the engineering token cannot access.

The candidate CLI authenticates the asset through the fixed engineering API, then
checks the archive against the source/build/release graph, actual APK bytes and
package/certificate/version, exact successful source CI run and signed source tag.
Its final source commit must contain the approved Fastlane branding commit
`ae514b281b9c` and source-workflow commit
`62e74987dcdb71bad6b4f52244e01c7d66cd51ba`; it reads only
`fastlane/metadata/trailblazer-beta` from that exact receipt commit and checks the
TrailBlazer title and pinned icon digest. The candidate path checks signed source
release metadata for title/tag/prerelease but does not claim an independent API
observation of the Android draft release or its upload state. The later full
readiness/Pages verifier retains its existing strict draft-release lookup.

```sh
python distribution/beta_evidence.py verify-preindex <release-input.tar> \
  --bundle-sha256 <independently-approved-complete-tar-sha256> \
  --engineering-asset-id <fixed-repository-release-asset-id> \
  --scratch <private-scratch-dir> --aapt <android-build-tools>/aapt \
  --apksigner <android-build-tools>/apksigner
```

## Emit the readiness JSON inside its own workflow

The readiness job passes the approved complete archive SHA and fixed engineering
asset ID, plus its own run ID and attempt from the workflow environment:

```sh
install -d -m 700 "$RUNNER_TEMP/beta-evidence-private"
python distribution/beta_evidence.py emit-readiness \
  --bundle-sha256 "$APPROVED_BUNDLE_SHA256" \
  --engineering-asset-id "$APPROVED_ENGINEERING_ASSET_ID" \
  --readiness-run-id "$GITHUB_RUN_ID" \
  --readiness-run-attempt "$GITHUB_RUN_ATTEMPT" \
  --output "$RUNNER_TEMP/trailblazer-beta-readiness.json" \
  --scratch "$RUNNER_TEMP/beta-evidence-private" \
  --aapt <android-build-tools>/aapt \
  --apksigner <android-build-tools>/apksigner
```

The emitter fetches authoritative GitHub metadata for the fixed asset and current
readiness run, downloads the asset through `gh`, checks its full raw SHA256, safely
extracts it, and runs the same receipt/APK/index/tag/release verification as the
packer. The current run must be the exact repository, `trailblazer-beta` branch,
`.github/workflows/beta-readiness.yml` path, run ID/attempt, verified source head,
and an allowed `push` or `workflow_dispatch` event. It must be `in_progress`: this
job is running inside that run and cannot claim its future success or artifact.
The output contains exactly the schema and field set consumed by
`distribution/pages.py`; Pages later checks the completed successful run and its
uploaded one-file readiness artifact. The emitter neither creates nor approves
that future artifact.

The Android repository contains
[`beta-readiness.yml`](https://github.com/0cwa/collieim-android/blob/main/.github/workflows/beta-readiness.yml).
Its presence does not establish an eligible Beta source branch, a preparation-aware
verifier pin, a successful readiness run, or a published Beta feed. Follow the
[source activation gates](../docs/DUAL-RELEASE-POLICY.md#source-activation-gates)
and the separate [Pages promotion procedure](BETA-FEED-OPERATIONS.md) before using
this evidence for publication. Tests use synthetic APK/index fixtures and mocked
GitHub/tool seams; they do not prove a real signed Beta release or hosted run.
