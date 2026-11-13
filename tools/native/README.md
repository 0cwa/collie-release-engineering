# Pinned native artifact staging (incremental)

This is a pinned source-preparation and fail-closed artifact-staging component, not yet a native source builder. The lock records exact source commits, attributed patch bytes, and hashes of the three locally built candidate binaries and their original POMs. It does not prove those binaries were produced from those sources, that the build is reproducible, or that real proxy and call behavior works. The complete RingRTC/WebRTC builder, independently pinned output trees/toolchain verification, and Android runtime gates must be completed before release. The accompanying build-driver integration permits provisional debug/test runs only and explicitly blocks release assembly.

Given local Git repositories containing the three exact pinned commits, prepare fresh source checkouts without network access:

```sh
python3 tools/native/prepare_sources.py --lock tools/native/native-lock.json \
  --libsignal "$LIBSIGNAL_REPO" --ringrtc "$RINGRTC_REPO" --webrtc "$WEBRTC_REPO" \
  --output "$NATIVE_SOURCES"
```

The helper verifies input commits and patch hashes, preserves authors in the libsignal/RingRTC mailboxes, and records the resulting source trees. It has been tested with synthetic Git fixtures and the three exact pinned source repositories. `ATTRIBUTION.md` records the composite WebRTC origin. Source preparation alone does not prove the staged binaries came from those trees. `prepare_sources.py --verify-prepared` independently replays the pinned patch bytes into a temporary index and checks each prepared tree and working source.

Given a local Maven repository containing exactly the locked candidate inputs and a fresh reconstructed app checkout, stage them using paths supplied by the caller:

```sh
python3 tools/native/stage_artifacts.py --lock tools/native/native-lock.json \
  --input-maven "$NATIVE_INPUT_MAVEN" --output "$NATIVE_STAGE" \
  --verification "$APP_SOURCE/gradle/verification-metadata.xml"
python3 tools/native/stage_artifacts.py --verify-stage --lock tools/native/native-lock.json \
  --output "$NATIVE_STAGE" --verification "$APP_SOURCE/gradle/verification-metadata.xml"
```

Output contains a local Maven repository at distinct `im.collie.native` coordinates, a Gradle init script, generated strict verification metadata and a digest receipt. The input AAR/JAR bytes are unchanged. The generated POMs keep upstream dependencies and redirect libsignal's matching client dependency to the Collie coordinate. The generated metadata narrows the existing blanket POM trust rule so it cannot bypass the new native POM hashes; trust for other groups is preserved.

The companion catalog patch switches the application to those distinct coordinates. It is mandatory: without the generated exclusive repository, resolution must fail instead of falling back to published Molly or Signal binaries. The accompanying build-driver integration verifies the stage immediately before every Gradle invocation, permits only the exact recomputed verification-metadata transformation in its disposable checkout, and passes the generated init script with strict verification. It refuses release assembly and reports that the complete RingRTC/WebRTC native source build is not implemented. A separate `native-build-libsignal` diagnostic phase validates prepared source, checks the recorded Rust/NDK versions and four proxy-mode tests, then invokes the documented libsignal Android build; its receipt is explicitly partial and cannot authorize release. On the real pinned source, its four proxy-only unit tests passed and Gradle built the AAR and client JAR; the diagnostic correctly rejected their hashes because they differ from the locked candidate bytes. The `native-prepare` phase accepts three caller-supplied local repositories; `native-integrate` stages the pinned provisional artifacts from the supplied `--maven-dir`. Do not add private or absolute machine paths to tracked configuration. The generated init script necessarily contains the caller's local Maven URI and must remain untracked.

The current three artifact hashes identify one local native build. A fresh build can legitimately differ until reproducibility is demonstrated, and should not be accepted by weakening verification or overwriting the lock automatically. Additional source-based build, source provenance, four-ABI output and no-direct-network runtime tests remain required.

`setup_webrtc.py` is a standalone diagnostic for the pinned WebRTC gclient setup. It omits only the source test-media hook and restores DEPS afterward, but it does not attest the downloaded dependency closure. `build_ringrtc.py` runs the pinned RingRTC builder with a narrowly checked in-memory adaptation that avoids copying WebRTC build trees, using a temporary link in the prepared scratch checkout. It validates the tracked WebRTC tree and disables the upstream wrapper's cloud upload trigger. A matching candidate hash produces only a partial receipt; neither script is integrated into release assembly or has yet proved the complete source-to-artifact chain.

For an explicitly non-release RingRTC diagnostic after source preparation and gclient setup:

```sh
python3 tools/native/build_ringrtc.py --lock tools/native/native-lock.json \
  --sources "$NATIVE_SOURCES" --workspace "$WEBRTC_WORKSPACE" --sdk "$ANDROID_HOME" \
  --java-home "$WEBRTC_WORKSPACE/src/third_party/jdk/current" \
  --rust-bin "$RUST_BIN" --output "$NEW_DIAGNOSTIC_DIR" \
  --gradle-zip "$GRADLE_931_ZIP" --gradle-distribution "$GRADLE_931_EXTRACTED"
```

Provide pinned depot_tools on `PATH` and Rust 1.98.1 with all four Android targets. The host must have a working C and C++ toolchain; an Android NDK alone is insufficient. The build script refuses a pre-existing output directory and never enables upstream cloud upload. Its exact-byte comparison may fail because reproducibility is not established; never update the hash automatically to make it pass.

The RingRTC diagnostic checks that the supplied 9.3.1 all-distribution ZIP matches Gradle's official SHA-256 (`17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43`), checks the extracted files against that ZIP, and executes the verified distribution directly with the RingRTC project directory explicitly selected. It rejects a missing ZIP, modified extraction, symlinks, extra files and group/world-writable distribution paths. Source ZIP: https://services.gradle.org/distributions/gradle-9.3.1-all.zip.sha256. This is not an independent source provenance or runtime proof; the running user's ability to modify files concurrently remains outside the check.

Native diagnostic Gradle invocations reject inherited Gradle/JVM options, user-home init scripts and user-home `gradle.properties`. To retain caches, use a clean `GRADLE_USER_HOME` whose `caches` and `wrapper` directories refer to the desired caches. Dependency cache contents are not authenticated by these environment checks; the native release gate remains closed. The libsignal and native app diagnostics also require an official 9.6.0 ZIP and an extracted distribution that matches it; the real 9.6.0 ZIP and extraction passed the verifier, while the diagnostics have not yet executed under that requirement.


The `native-build-libsignal` and native app diagnostic phases require `--native-gradle-zip` and `--native-gradle-distribution` pointing to the Gradle 9.6.0 all ZIP and its extracted directory. The ZIP must match the checksum already pinned by the 8.35.1 source, `87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396`; the directory is checked against the ZIP before and after execution. The build runs the verified distribution directly with the intended project directory, leaving unrelated legacy build flows unchanged. The cached 9.6.0 ZIP and extracted distribution passed this verifier.

An offline strict-verification fixture for direct and transitive upstream-coordinate substitution is available via `COLLIE_NATIVE_TEST_GRADLE=/path/to/verified/gradle python3 tools/native/native_fallback_smoke.py`. It creates and removes a small temporary Maven repository and checks all three native modules resolve only to Collie coordinates.

The partial RingRTC diagnostic also checks 70 nested Git checkouts against the
pinned retained gclient snapshot before and after the build. It hashes tracked
working files, checks file modes and symlinks, permits pinned Git CRLF checkout
conversion, and checks Signal's exact upstream Java-release hook output. The
27 recorded Gitlinks must be absent or empty. This is a consistency check, not
independent authentication of the snapshot against DEPS; ignored and untracked
files, CIPD package contents, compiler provenance and concurrent mutations are
not attested. It cannot authorize a release.

The partial RingRTC diagnostic verifies the recorded 59 Linux/Android CIPD
package manifests and their installed payloads before and after execution. The
reference hashes were derived by checking the packages' content-addressed CIPD
archive IDs against the upstream DEPS selections; generated version files and
four separately pinned upstream Git files sharing a package root are checked as
well. The check rejects undeclared files and directories under those package
roots and writable package files. It does not authenticate its reference by
itself, attest symlink target contents, other build inputs or concurrent
changes, or establish source-to-binary provenance. Release remains blocked.

[CIPD-PROVENANCE.md](CIPD-PROVENANCE.md) records how the package reference was derived and its remaining trust boundaries.

[RUST-PROVENANCE.md](RUST-PROVENANCE.md) describes the partial Rust payload check, how its reference was derived, and what remains unverified.

[NDK-PROVENANCE.md](NDK-PROVENANCE.md) describes the NDK payload tree check, its archive reference and remaining limits.

WebRTC setup removes inherited Python module paths and depot_tools bypass overrides, disables user-site imports, and rejects custom `.cipd_client_root` and `.cipd_client_platform` files; use the checkout’s default CIPD root and platform. It also removes shell startup and exported-function overrides from the child environment. These checks do not authenticate vpython, wheels, other executables or caches.

The libsignal helper accepts `--fresh` for an offline diagnostic that requires its named Cargo, Gradle and generated JNI outputs to be absent, fixes the Rust compiler and target directory, disables Gradle build and configuration caches, and reruns tasks. The helper never deletes existing outputs. Its local failure record binds output hashes to selected inputs and commands, but cannot authorize staging or release; host compiler/runtime, Cargo cache and other unchecked inputs remain part of the builder trust assumption.

## Fresh native diagnostics and exact archive comparison

The RingRTC helper also accepts `--fresh`. Use a newly prepared RingRTC source
tree and newly set up WebRTC workspace. The guard rejects existing WebRTC `out`
and Ninja state, RingRTC Gradle/Cargo/build outputs, and local Siso option files.
It does not delete existing files to make a contaminated workspace pass. Fresh
mode runs Siso with `--offline` to disable remote execution, disables Cargo
incremental compilation, and passes `--offline --no-daemon --no-build-cache
--no-configuration-cache --rerun-tasks` to the verified Gradle distribution.
Both native helpers reject inherited compiler, build-output, remote-cache and
Python injection overrides, and external or untracked Cargo configuration,
including nested `.cargo` directories. Custom Cargo/Gradle cache homes must be
absolute paths so the parent check and child process refer to the same location.
Fresh mode also rejects Maven local artifacts and user Maven settings or
environment redirects; native upstream builds contain `mavenLocal()` lookups.

Source verification hashes tracked working files directly, including executable
bits and symlink targets. It rejects `assume-unchanged` and `skip-worktree` flags,
local Git filters and attributes, and unexpected populated Gitlinks. This applies
to the active WebRTC compile checkout as well as the prepared sources and tracked
depot_tools files, so cached Git status cannot hide changed compilation inputs.

These checks do not bootstrap or authenticate the remaining dependency closure.
Fresh compilation still needs populated, reviewed dependency caches, pinned
toolchains and sufficient disk/memory. An empty offline Cargo/Gradle cache is a
blocked build, not a reason to reuse previous compiler outputs or disable the
checks. The diagnostic receipts retain `release_allowed: false`; a fresh output
check is not an independent source-to-binary attestation.

Compare all three supplied build outputs against the published binary pins:

```sh
python3 tools/native/compare_reproduction.py \
  --lock tools/native/native-lock.json \
  --reference-dir "$PUBLISHED_NATIVE_FILES" \
  --libsignal-android "$LIBSIGNAL_AAR" \
  --libsignal-client "$LIBSIGNAL_JAR" \
  --ringrtc-android "$RINGRTC_AAR" \
  --report "$NEW_COMPARISON_JSON"
```

`PUBLISHED_NATIVE_FILES` is a flat directory containing the three distributed
filenames, for example `libsignal-android-0.106.1-collie.1.aar`. Each reference
must match its checked-in `binary_sha256`. The other three arguments point to
the actual diagnostic build outputs; the comparison rejects symlinks and using
the same file or hard link as both reference and output. It hashes every ZIP
entry, including testing JNI, and checks the required libraries for all four
ABIs with ELF class, byte-order, type and machine sanity checks. It never
extracts, normalizes, replaces or updates either input or the native lock.

The JSON result is `EXACT_BYTES_MATCH` (exit 0), `ARTIFACT_MISMATCH` (exit 1), or
`BLOCKED_INPUT` (exit 1). A timestamp, compression or other container difference
may be diagnosed as `ZIP_METADATA_ONLY`; it remains a mismatch. Changed payloads
are listed with reference/output hashes to support investigation. An existing
report is never overwritten (exit 2); preserve each attempt's report and honor
the command's exit status. Missing or invalid inputs produce a blocked report.

Even an exact match only compares the supplied files. It cannot distinguish an
independent build from a copied published binary. Every report therefore says
`independence_verified: false` and `release_allowed: false`. Reviewed build logs,
authenticated dependency inputs, fresh build receipts and real proxy/call
runtime evidence remain necessary. The operational sequence and remaining
bootstrap work are in [the native verification runbook](../../docs/NATIVE-VERIFICATION.md).

## Local diagnostic candidate provenance

The diagnostic candidate changes the original native lock SHA-256 `9866196dec36f096d2757bbf9e9d774c7147548542dd2a041e6438f79dbd73e4` to `557e20c3f8c1d6290af3007bb8eba5469e5a77f569b8efc7dc2edc1e68ba7856`. Its libsignal Android AAR, libsignal client JAR and RingRTC AAR hashes are respectively `4247031a753f73f12753d04f14d9316436bbf07af2097c1b897feedd360dbd9a`, `c361b8e905c770b838535fdac5a0bdf0f995f8ed6ce0a6837cb4c7360e8c5a79` and `51f1f9b1f2335ba1b6557b5f0810227ac66a54ec8cbf0f2c789091b0ab5d245b`.

Each source diagnostic checked the prepared source trees against patch replay: libsignal `de930179286b4fe68875475c1b0678615e180908`, RingRTC `e993dcb5807367a5aab4c9620fdb1f039ae0a013` and WebRTC `46c89976e694cb5c3ea74e2632df7600ab693918`. The libsignal build required the specified output directories to be absent, used offline dependency caches and executed all 35 Gradle tasks; four proxy-mode host tests passed. The RingRTC build reused existing WebRTC and compiler outputs. Neither is an independent reproducibility proof. Both original comparisons to the prior lock failed and their failure records remain part of the diagnostic evidence.

This candidate trusts the local builder, host operating system, compiler, runtime and dependency caches. Exact candidate staging checks its pinned inputs but does not prove runtime behavior, call privacy, installed upgrades or release readiness. The release block remains in effect.
