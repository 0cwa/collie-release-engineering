# Building Collie

Reconstruction and release engineering live in [collie-release-engineering](https://github.com/0cwa/collie-release-engineering). See [VERIFY.md](VERIFY.md) for Git-only source inspection.

This checkout contains the reconstructed application source; Gradle does not run
an assembler or inject source from another worktree. JDK/Gradle/SDK/NDK and strict
dependency inputs are declared in this tree. Canonical identity is im.collie.app,
Collie backup names and optional FCM; legacy filename aliases are one product.

**Direct build readiness: BLOCKED.** `build-inputs/native-lock.json` identifies
accepted native bytes. Their public immutable repository has not been supplied
or independently downloaded. `native-publication.json` records this gap. Do not
substitute upstream native libraries or disable strict dependency verification.
Every direct Android Gradle configuration deliberately fails until the required publication layer
and clean-checkout build acceptance are reviewed. This draft is not releasable.

The retained local operator route is in collie-release-engineering and still
requires its explicit trusted-local native acceptance. That is not independent
native reproduction. The three locked binaries and transformed POMs now have tracked exclusive repository, catalog, substitutions and strict verification entries. The proposed origin in build-inputs/native-artifacts.json is NOT PUBLISHED. Obtain operator publication approval, independently read back all six exact files, review a separate acceptance/gate layer, then run a fresh controlled Gradle build. No native init script, local Maven lookup or external native source includeBuild is part of this direct-source recipe.
After reviewed gate removal, releaseVersionCode and releaseVersionName are normal source-defined Gradle properties. Final values must come from serialized external reservation and be recorded in the build receipt. The expected canonical task is `./gradlew --dependency-verification=strict
:app:assembleProdWebsiteRelease`; it is currently BLOCKED as described above.

Release versions must be externally reserved above 177702 and all later published
or reserved codes using engineering's serialized version policy. RC1 ingestion
reuses its existing signed bytes/code. No build has been performed from this G.
**DRAFT candidate proposal:** 8.35.1-2-rc1 / 177703. This value is not allocated
or reserved. The direct-build gate remains closed until the owner refreshes the
inventory and confirms this proposal through the serialized reservation policy.
No parity with historical APK bytes is claimed after changing build metadata.
Instrumentation remains opt-in. Physical-device/account/attachment/database
upgrade acceptance and independently reproduced native outputs are NOT RUN.

Native JVM regression readiness is separate: build-inputs/host-test-inputs.json records the historical Linux testing-JNI candidate and immutable staging-guard recipe. No public host-test input or complete host build recipe is supplied; those tests are NOT RUN, not skipped/passed. Do not package test JNI in a release or treat the six production Maven files as all-test readiness.

## Published source dates

The [v8.35.1-2-rc1 release](https://github.com/0cwa/collieim-android/releases/tag/v8.35.1-2-rc1) was published on 2026-11-09 13:57:11 UTC. Its signed tag, `v8.35.1-2-rc1`, points to source G `72e1b84383d068c488e96ca9cd451e730e6e4e77` and is dated 2026-11-09 13:38:34 UTC. The release record establishes when this release became public; the tag object records its tagger date separately.

Generated publication commits in this source snapshot show `2000-01-01T00:00:00Z` because the historical publisher used a fixed fixture timestamp for reproducibility. That date describes generated commit metadata; it is not the age of the application or the original upstream work. Authentic upstream commits remain in the ancestry with their original metadata, and generated-layer messages retain source provenance.
