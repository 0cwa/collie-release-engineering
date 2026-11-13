# Independent native verification

This runbook addresses the native provenance finding in the Collie security
review. It separates the work that can be run now from the evidence still needed
to close that finding. A successful APK rebuild, a valid download checksum, or a
host JNI test run does not by itself reproduce the Android native libraries.

## Current state and immutable target

As checked on 2026-11-13, Android `main` is
`75192c5b92c50af23b0c5f8fd92891c4a8e5398e`. Its
[`build-inputs/engineering.json`](https://github.com/0cwa/collieim-android/blob/75192c5b92c50af23b0c5f8fd92891c4a8e5398e/build-inputs/engineering.json)
pins recipe `36779887ffc0bb1ecff55a87c350740662570039` and explicitly says that
independent reproduction is not established. The native lock at engineering
baseline `b6ec5ca32e97136ba83488a1b7b86f7559d5a4e2` is byte-identical to the
Android lock. Its SHA-256 is
`557e20c3f8c1d6290af3007bb8eba5469e5a77f569b8efc7dc2edc1e68ba7856`.

| Input | Exact value |
| --- | --- |
| libsignal base | `904c8dc8216073b078a5f2f5090e3da8382e6a92` |
| RingRTC base | `bdbb0dd1b9f418850dfe1fa3cd4b1c2b1123436f` |
| WebRTC base | `d7dbd6fdddac949e89501e6ea947a344e9ffde72` |
| depot_tools | `ce5a30784f7323ee224f3a6e7d4ae208dd6d17fe` |
| libsignal prepared tree | `de930179286b4fe68875475c1b0678615e180908` |
| RingRTC prepared tree | `e993dcb5807367a5aab4c9620fdb1f039ae0a013` |
| WebRTC prepared tree | `46c89976e694cb5c3ea74e2632df7600ab693918` |
| libsignal Android AAR | `4247031a753f73f12753d04f14d9316436bbf07af2097c1b897feedd360dbd9a` |
| libsignal client JAR | `c361b8e905c770b838535fdac5a0bdf0f995f8ed6ce0a6837cb4c7360e8c5a79` |
| RingRTC Android AAR | `51f1f9b1f2335ba1b6557b5f0810227ac66a54ec8cbf0f2c789091b0ab5d245b` |

The three mailbox/patch sets and their hashes are in
[`tools/native/native-lock.json`](../tools/native/native-lock.json). The app's
[`native-artifacts.json`](https://github.com/0cwa/collieim-android/blob/75192c5b92c50af23b0c5f8fd92891c4a8e5398e/build-inputs/native-artifacts.json)
also pins the six published binary/POM files. Published POMs have been transformed
to Collie coordinates; their hashes intentionally differ from the source POM
hashes in the native lock. Binary bytes are unchanged by staging.

The [2026-11-13 published-input audit](evidence/native-published-input-audit-2026-11-13.json)
retrieved all six published files and matched their recorded byte lengths and
SHA-256 hashes. The three archives also passed the expected library-coverage
and ELF class/machine checks. This is current distribution evidence only; it
records that no independent native build was performed.

The old `distribution/evidence/native-independent-verification.json` records an
independent check of existing archive bytes. Its scope explicitly excludes a
native source rebuild. Do not reuse its `PASS` as evidence for this task.

The new `--fresh` RingRTC mode and archive comparator make fresh-output checks
and mismatch diagnosis executable. They do not record an actual independent
build, authenticate every bootstrap input, or grant release permission. All
diagnostic/comparison receipts retain `release_allowed: false`.

## Work order and completion criteria

1. Prepare and review the verifier change and run its focused tests.
2. Provision a disposable verifier host, independent of the original local
   builder. Complete the authenticated input/bootstrap inventory below.
3. Reconstruct the pinned sources, build libsignal and all four RingRTC/WebRTC
   ABIs with absent output directories, and compare the three resulting
   archives with the published target.
4. Investigate every difference. Preserve failed runs and the original lock.
5. Exercise the distributed binaries using the runtime matrix below.
6. Review the complete evidence and then update the app's provenance statement
   through a separate app PR. Record the verifier's exact revision as well as
   the original recipe revision; neither identity should be lost.

Closing the source-to-binary finding requires an independent operator/runner,
reviewed input origins and digests, complete commands and logs, correct source
trees, all three exact archive matches, four-ABI library coverage, and no reused
compiler outputs. Runtime acceptance is a separate result. A matching build is
evidence of reproducibility for the recorded environment, not proof of compiler
correctness or of every application security property.

## Host and bootstrap prerequisites

Use Linux x86-64 and an otherwise empty work directory. The original builder's
Cargo target trees, WebRTC `out` trees, Gradle task outputs, compiler caches and
user configuration must not be imported. Use a fresh user profile with no local
Maven repository: both native projects retain `mavenLocal()` in their upstream
repository lists. Source archives and authenticated
dependency download inputs may be reused after their provenance is checked.
Retain the runner/image identity, OS/package inventory, free disk/RAM, CPU count,
tool digests and input origin URLs with the run evidence.

No native-capable runner has been established by this change. The review
workspace had roughly 58 GiB free disk and 9.7 GiB RAM, Java 21, and no installed
Rust toolchain, Android SDK or Docker. No full native rebuild was run there.
There is no measured minimum for this exact Collie build. Budget conservatively
for all four WebRTC outputs and dependencies, and check actual free space before
starting. GitHub documents standard public Ubuntu runners with 14 GB storage;
do not assume that label guarantees enough space. Its larger runner examples
include 150 GB/16 GB RAM and 300 GB/32 GB RAM. Selecting or purchasing a runner is
an operator setup decision, not something this runbook has performed.

Sources: [GitHub runner specifications](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job),
[larger runner specifications](https://docs.github.com/en/actions/reference/runners/larger-runners).

| Required input | Verification and remaining boundary |
| --- | --- |
| Rust 1.98.1, host std, Cargo, four Android std targets | Follow [RUST-PROVENANCE.md](../tools/native/RUST-PROVENANCE.md). Verify the signed channel manifest and component archives before installation; the build guard checks the pinned installed payloads. Pass the direct toolchain `bin`, not rustup shims. |
| Android NDK `28.0.13004108` | Follow [NDK-PROVENANCE.md](../tools/native/NDK-PROVENANCE.md). The guard requires 8,449 entries with tree digest `cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d`. |
| Android SDK platform 34 and required build tools | Both pinned native Android projects declare compile SDK 34. Acquire through the official SDK distribution; record the exact installed build-tool packages and their authenticated download evidence. Merely checking the package names is insufficient. |
| JDK 21 for libsignal | Supply an official JDK archive/image with a separately recorded digest/origin. The current libsignal guard checks the Java version and records the executable hash; it does not authenticate the entire JDK by itself. |
| WebRTC JDK 25 and WebRTC NDK `2@30.0.15729638` | Obtain through the pinned WebRTC dependency setup. RingRTC requires `workspace/src/third_party/jdk/current`. The separate NDK r28 above is used by RingRTC Rust/AAR work; do not replace one NDK with the other. |
| Gradle 9.6.0 all ZIP for libsignal | Official ZIP SHA-256 `87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396`; extracted contents are checked before and after the build. |
| Gradle 9.3.1 all ZIP for RingRTC | Official ZIP SHA-256 `17f277867f6914d61b1aa02efab1ba7bb439ad652ca485cd8ca6842fccec6e43`; extracted contents are checked before and after the build. |
| depot_tools and WebRTC dependencies | Pin depot_tools as above; independently repeat [CIPD-PROVENANCE.md](../tools/native/CIPD-PROVENANCE.md). The build checks the retained 70 Git repositories and 59 CIPD payload manifests. |
| Host C/C++ tools, protobuf, Python/vpython and wheels | Install from recorded authenticated sources. Existing guards do not establish a complete host/bootstrap closure. Record this as a trust boundary until the inventory is reviewed. |
| Cargo and Maven/Gradle dependencies | Populate clean download caches from the pinned source manifests and record their origins/digests. Do not import prior build caches, targets, transformed outputs or user init scripts. See the offline prerequisite below. |

The Gradle ZIPs are at
`https://services.gradle.org/distributions/gradle-9.6.0-all.zip` and
`https://services.gradle.org/distributions/gradle-9.3.1-all.zip`.
Download over verified HTTPS, validate the hashes above, then extract to private
directories without writable-by-other-users files. Do not repair a failed
verification by changing the expected hash.

### Offline dependency prerequisite

Both fresh build modes deliberately disable online dependency resolution and
compiled-output reuse. An empty Cargo or Gradle download cache will fail. There
is currently no fully verified, one-command bootstrap in this repository that
populates all required dependencies and proves their provenance. This is an
explicit remaining work item, not a reason to drop `--fresh` or `--offline`.

Cargo dependency acquisition can start in a separate prepared bootstrap source
tree, using the authenticated direct Cargo binary:

```sh
"$RUST_BIN/cargo" fetch --locked --manifest-path "$BOOTSTRAP_SOURCES/libsignal/Cargo.toml"
"$RUST_BIN/cargo" fetch --locked --manifest-path "$BOOTSTRAP_SOURCES/ringrtc/Cargo.toml"
```

This downloads dependency inputs; it does not establish their independent
authenticity or build the Android libraries. Retain both Cargo locks, Git
dependency revisions and registry archive checksums. Validate extracted sources
against those inputs before a verifier consumes them. Keep Cargo configuration
and compiler wrappers out of the verifier's fresh Cargo home.

For Gradle, use the verified distributions and a disposable bootstrap source
tree to resolve the native projects' plugin and dependency artifacts. A
`dependencies` report alone does not fetch every artifact needed by the build.
Before proceeding, demonstrate that the same pinned projects resolve offline
from the retained download inputs; capture the resolved coordinate/digest
inventory. The approved transfer should contain repository download inputs
(such as Gradle `modules-2` and Cargo registry/Git inputs), not Gradle build-cache
entries, artifact transforms, project `.gradle` directories or compiled build
scripts. If missing inputs prevent the offline build, stop, complete this
bootstrap work, and start again in new source/output directories. Do not turn
the failed attempt into a passing receipt.

## Reconstruct sources

Run from the reviewed engineering checkout. Set `NATIVE_RUN` to a new absolute
directory on the verifier host. The commands intentionally create new source
directories and never clean an existing operator directory.

```sh
set -eu
ENGINEERING_ROOT="$(pwd -P)"
ENGINEERING_REV="$(git rev-parse HEAD)"
test -z "$(git status --porcelain --untracked-files=all)"
: "${NATIVE_RUN:?set NATIVE_RUN to a new absolute directory}"
case "$NATIVE_RUN" in /*) ;; *) exit 1 ;; esac
test ! -e "$NATIVE_RUN"
mkdir -p "$NATIVE_RUN/inputs" "$NATIVE_RUN/evidence"
printf '%s\n' "$ENGINEERING_REV" > "$NATIVE_RUN/evidence/verifier-revision.txt"
git show -s --format=fuller HEAD > "$NATIVE_RUN/evidence/verifier-commit.txt"
sha256sum tools/native/native-lock.json > "$NATIVE_RUN/evidence/native-lock.sha256"
df -h "$NATIVE_RUN" > "$NATIVE_RUN/evidence/disk-before.txt"
free -h > "$NATIVE_RUN/evidence/memory-before.txt"
uname -a > "$NATIVE_RUN/evidence/host.txt"

git init "$NATIVE_RUN/inputs/libsignal"
git -C "$NATIVE_RUN/inputs/libsignal" fetch --no-tags --depth=1 \
  https://github.com/signalapp/libsignal.git 904c8dc8216073b078a5f2f5090e3da8382e6a92
git init "$NATIVE_RUN/inputs/ringrtc"
git -C "$NATIVE_RUN/inputs/ringrtc" fetch --no-tags --depth=1 \
  https://github.com/signalapp/ringrtc.git bdbb0dd1b9f418850dfe1fa3cd4b1c2b1123436f
git init "$NATIVE_RUN/inputs/webrtc"
git -C "$NATIVE_RUN/inputs/webrtc" fetch --no-tags --depth=1 \
  https://github.com/signalapp/webrtc.git d7dbd6fdddac949e89501e6ea947a344e9ffde72
git init "$NATIVE_RUN/inputs/depot_tools"
git -C "$NATIVE_RUN/inputs/depot_tools" fetch --no-tags --depth=1 \
  https://chromium.googlesource.com/chromium/tools/depot_tools.git \
  ce5a30784f7323ee224f3a6e7d4ae208dd6d17fe
git -C "$NATIVE_RUN/inputs/depot_tools" checkout --detach FETCH_HEAD

python3 tools/native/prepare_sources.py --lock tools/native/native-lock.json \
  --libsignal "$NATIVE_RUN/inputs/libsignal" \
  --ringrtc "$NATIVE_RUN/inputs/ringrtc" \
  --webrtc "$NATIVE_RUN/inputs/webrtc" --output "$NATIVE_RUN/sources"
python3 tools/native/prepare_sources.py --lock tools/native/native-lock.json \
  --verify-prepared --output "$NATIVE_RUN/sources"

python3 tools/native/setup_webrtc.py --lock tools/native/native-lock.json \
  --sources "$NATIVE_RUN/sources" \
  --depot-tools "$NATIVE_RUN/inputs/depot_tools" \
  --output "$NATIVE_RUN/webrtc-workspace" \
  > "$NATIVE_RUN/evidence/webrtc-setup.log" 2>&1
```

Verify that `sources/source-receipt.json` contains the three prepared trees in
the table above. `setup_webrtc.py` downloads dependencies and runs the pinned
hooks; it must not copy an old WebRTC workspace. Repeat the documented Git/CIPD
reference authentication before treating the newly installed payloads as
verified. A setup receipt covers setup only.

## Build with fresh outputs

Supply `ANDROID_SDK`, `JAVA21_HOME`, `RUST_BIN`, `GRADLE_960_ZIP`,
`GRADLE_960_DIR`, `GRADLE_931_ZIP` and `GRADLE_931_DIR` as absolute paths to the
authenticated inputs above. `CARGO_HOME` and `GRADLE_USER_HOME` must be private
clean homes populated only with the reviewed dependency download inputs. The
builder rejects user JVM/Gradle injection, compiler/cache overrides, reused
output trees and unsafe toolchain paths; consult the failure instead of removing
the guard. No signing or upload credentials are needed.

Run both commands even if one finishes its build but reports a hash mismatch.
Preserve each exit code separately. A failure before artifacts exist is a build
blocker; it cannot be replaced with the published archive to satisfy comparison.

```sh
set +e
set -u
export PATH="$NATIVE_RUN/inputs/depot_tools:$PATH"
python3 tools/native/build_sources.py --fresh \
  --lock tools/native/native-lock.json --sources "$NATIVE_RUN/sources" \
  --sdk "$ANDROID_SDK" --java-home "$JAVA21_HOME" --rust-bin "$RUST_BIN" \
  --gradle-zip "$GRADLE_960_ZIP" --gradle-distribution "$GRADLE_960_DIR" \
  --report "$NATIVE_RUN/evidence/libsignal-build.json" \
  > "$NATIVE_RUN/evidence/libsignal-build.log" 2>&1
LIBSIGNAL_EXIT=$?
printf '%s\n' "$LIBSIGNAL_EXIT" > "$NATIVE_RUN/evidence/libsignal-exit.txt"

python3 tools/native/build_ringrtc.py --fresh \
  --lock tools/native/native-lock.json --sources "$NATIVE_RUN/sources" \
  --workspace "$NATIVE_RUN/webrtc-workspace" --sdk "$ANDROID_SDK" \
  --java-home "$NATIVE_RUN/webrtc-workspace/src/third_party/jdk/current" \
  --rust-bin "$RUST_BIN" --gradle-zip "$GRADLE_931_ZIP" \
  --gradle-distribution "$GRADLE_931_DIR" --output "$NATIVE_RUN/ringrtc-build" \
  > "$NATIVE_RUN/evidence/ringrtc-build.log" 2>&1
RINGRTC_EXIT=$?
printf '%s\n' "$RINGRTC_EXIT" > "$NATIVE_RUN/evidence/ringrtc-exit.txt"
```

Run that block in a new shell without `errexit`; otherwise the first failed
diagnostic exits before its status is recorded. Do not rerun a command using an
existing report/output path. A prior partial receipt is not the result of a
later failed rerun.

The libsignal run must execute the four proxy-mode tests and build the Android
AAR/client JAR. The RingRTC log must show fresh WebRTC compilation for `arm`,
`arm64`, `x86` and `x64`, followed by RingRTC and AAR packaging. A WebRTC prebuild,
RingRTC-only build, task-output cache hit or remote compiler cache does not meet
this runbook's completion criteria.

## Fetch the reference and compare

Download the reference archives separately from the build outputs. This script
uses the exact published filenames and verifies their lock hashes before the
comparator consumes them. It does not download any source build outputs.

```sh
set -eu
mkdir "$NATIVE_RUN/reference"
python3 - "$NATIVE_RUN/reference" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

target = Path(sys.argv[1])
lock = json.loads(Path('tools/native/native-lock.json').read_text())
base = 'https://repo.collie.im/native-inputs/v1/maven/im/collie/native/'
for module in lock['modules']:
    name, version, extension = (module[key] for key in ('name', 'output_version', 'extension'))
    filename = f'{name}-{version}.{extension}'
    url = f'{base}{name}/{version}/{filename}'
    destination = target / filename
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response, destination.open('xb') as out:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != module['binary_sha256']:
        raise SystemExit(f'Reference hash mismatch: {filename}; preserve evidence and stop')
    print(digest.hexdigest(), filename)
PY

python3 tools/native/compare_reproduction.py \
  --lock tools/native/native-lock.json --reference-dir "$NATIVE_RUN/reference" \
  --libsignal-android "$NATIVE_RUN/sources/libsignal/java/android/build/outputs/aar/libsignal-android-release.aar" \
  --libsignal-client "$NATIVE_RUN/sources/libsignal/java/client/build/libs/libsignal-client-0.106.1.jar" \
  --ringrtc-android "$NATIVE_RUN/ringrtc-build/gradle/outputs/aar/ringrtc-android-release.aar" \
  --report "$NATIVE_RUN/evidence/archive-comparison.json"
```

If an upstream output filename differs, use the sole actual release AAR/client
JAR reported by the successful build, record that path, and retain the output
inventory. Do not choose a previously downloaded reference as a rebuilt input.
The comparator requires all three artifacts, exact reference hashes, complete
archive-entry comparison, and the expected ELF libraries in all four ABIs. It
also compares entries such as the existing libsignal testing JNI libraries.

| Result | Meaning and next action |
| --- | --- |
| `EXACT_BYTES_MATCH` | All three archives match the locked bytes and library coverage checks passed. Review source/build/bootstrap evidence before calling this an independent reproduction. The comparator itself cannot prove a supplied file was built. |
| `ARTIFACT_MISMATCH` | At least one archive differs. Retain both archives and the per-entry hashes. Diagnose ZIP metadata separately from Java/native payload differences. |
| `ZIP_METADATA_ONLY` for a module | Diagnostic classification only. It still fails exact reproduction; fix deterministic packaging or document and review a different acceptance policy separately. |
| `BLOCKED_INPUT` | Missing, malformed, unsafe or incorrectly pinned input. Fix the input problem and perform a new run; do not infer a binary match. |

For a mismatch, inspect changed ELF build IDs, symbols/debug paths, generated
Java/resources, archive ordering/timestamps and command/toolchain differences.
Do not overwrite native-lock or app verification hashes to make the verifier
green. If new artifacts are deliberately adopted, use new immutable coordinates
and a separate review showing why they differ and how they were produced.

## Runtime matrix for the resulting binaries

Build the test APK from the pinned app commit with its strict native input
verification. Record APK hash, app revision, build variant, AAR hashes, device
model/API/ABI and proxy configuration. Check which native entries reach the APK;
document expected stripping/packaging transformations. Host JNI tests and the
small extracted C++ patch tests do not substitute for this Android run.

Run each applicable scenario for `arm64-v8a`, `armeabi-v7a`, `x86` and `x86_64`.
Mark an unavailable ABI/device or scenario `NOT RUN`, with its reason. Do not
turn it into a passing row. Include a physical ARM device for actual calls.
Use the intended configured proxy policy and capture device/proxy traffic so
that an apparent UI failure cannot conceal a direct connection.

| Scenario | Required observation |
| --- | --- |
| Working proxy, IPv4 and IPv6 destinations | Registration/account access, messaging and supported call paths work through the selected proxy; address-family behavior matches the policy. |
| Proxy unreachable or connection refused | Protected paths fail without a direct fallback or direct destination DNS lookup. |
| Proxy authentication failure or malformed response | No fallback to direct transport, no credential disclosure to an unintended endpoint, and a recoverable error. |
| Proxy disconnect during an established connection | Reconnect continues to use the configured proxy; retries do not escape it. |
| Proxy change and network change | New and existing transport lifecycles adopt the intended policy, including Wi-Fi/mobile and IPv4/IPv6 transitions. |
| Unknown/unsupported proxy type and SOCKS IPv6 | Exercise the changed WebRTC branches with real sockets; reject unsupported policy rather than silently creating a direct socket. |
| Voice/video call setup, media and reconnect | Inspect signaling plus selected ICE/TURN/media paths against the configured policy; test both directions and proxy failure during a call. |

Record observed destinations and DNS traffic, expected policy, failures, logs
and exact test steps. The operator should distinguish traffic intentionally
outside a policy from a fallback that violates it; document that policy before
testing, not after observing an unexpected connection.

## Evidence handoff and CI preparation

Retain the verifier revision, app revision, original recipe revision, native
lock, source receipts, bootstrap inventory and authentication evidence, all
build logs and exit codes, fresh-build receipts, three output hashes, archive
comparison and runtime matrix. Store large artifacts as immutable CI artifacts
with IDs/digests/retention details; commit a small reviewed manifest and durable
links. Bind a hosted run's repository, workflow revision, run ID and attempt to
the evidence. Record who controlled the independent builder.

Review failure evidence as well as successful output. Do not let a generated
JSON boolean alone open a release gate: a gate must validate expected identities
and retrieve evidence from the trusted workflow/operator path. A new app PR can
then update provenance to the exact result demonstrated. Until that evidence
exists, preserve `trusted-local native outputs; independent reproduction not
established`.

For the separate CI finding, reuse the existing
[host JNI/unit-test repair PR #104](https://github.com/0cwa/collieim-android/pull/104).
Its recorded passing run is evidence about host tests, not Android native
reproduction. Revalidate the full suite on the combined current source, prepare
the unsigned production build and stable required checks, and require release
checks for the exact release source. Activate enforcement only with a reviewed
passing baseline; this runbook does not change repository rules.
