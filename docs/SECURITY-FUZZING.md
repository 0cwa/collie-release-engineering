# Security fuzzing pilot

The advisory pilot exercises focused production entry points in [Collie Socket](https://github.com/0cwa/colliesocket/tree/main/fuzz) and [Collie Android](https://github.com/0cwa/collieim-android/tree/main/tooling/fuzz-encrypted-push). It covers Socket enrollment parsing and control-frame decoding in raw and synthetic-keyed modes, Android event parse/decide behavior, and endpoint-update promotion sequences. Android `ControlFrame.build` constructs outgoing authenticated frames; its fixed-nonce check covers repeatable construction, header identity, and size, not independent authentication verification or incoming decryption. The Android arbitrary-byte target derives decision context from the production parse result and uses the default `identityMatches = true`; it checks parser/decision consistency and replay handling, not an independent schema or enrollment-identity oracle. The pilot does not claim coverage of Whistle, Collie Pack, native code, or Python tooling.

## Scope and invariants

Use the pinned Collie Socket v3 contract as the oracle. Assert contract behavior, not implementation details:

- Identity: account selection precedes receipt, epoch, and sequence handling; receipts are scoped exactly by `(account_id, request_id)`.
- Replay: a historical receipt may be returned, but it never attests current state. A conflicting replay must not mutate state.
- Rejection: reject an ineligible import unless its `import_id`, `key_id`, context, epoch, and newer sequence satisfy the contract.
- Recovery: the server never permits a lower epoch. Phone-side explicit recovery may promote a lower epoch only with the exact eligible identity/import and a newer sequence. Ordinary export is not rejected merely because it is below the remembered server epoch.

The Android contract fixture is pinned under `app/src/test/resources/colliesocket-contract-v3/`; retain its existing ownership and verification mechanism. Do not silently regenerate or edit the fixture to make a failing assertion pass. Changes to assertions or exclusions require independent review against the contract and production behavior.

## Regression and bounded discovery

Run Android deterministic fixtures and saved crash inputs from the Android repository root:

```sh
./gradlew :fuzz-encrypted-push:test --no-daemon --no-configuration-cache --max-workers=1 -Dorg.gradle.jvmargs=-Xmx1g
```

Run the bounded Jazzer discovery target separately:

```sh
./gradlew :fuzz-encrypted-push:jazzerFuzz --no-daemon --no-configuration-cache --max-workers=1 -Dorg.gradle.jvmargs=-Xmx1g
```

The Android event parser and receiver `decide` target accept at most 4096 bytes, use a fixed clock, and permit protocol versions 1 through 3. Discovery is bounded to 60 seconds. Six pinned v3 event fixtures are initial seeds. The workflow caps the container at 4 GiB and two CPUs; Gradle uses one worker and a 1 GiB daemon heap. Keep minimized regression crashes in the module's `src/test/resources/im/molly/unifiedpush/receiver/EncryptedPushFuzzTestInputs/`; `.cifuzz-corpus/` is disposable rolling discovery data, capped at 512 files and 16 MiB. Cache growth is reported as a metric, not a success condition. Dispatch from a release tag replays that exact checked-out source revision and records its commit, contract pin, runtime, command, and bounds.

From the Socket repository root, first install its pinned tools and create writable per-target discovery directories so generated inputs stay separate from committed seeds:

```sh
rustup toolchain install nightly-2025-09-15 --profile minimal
cargo install cargo-fuzz --version 0.12.0 --locked
mkdir -p target/fuzz-cache/enrollment_parse target/fuzz-cache/control_raw target/fuzz-cache/control_authenticated
```

Run deterministic Rust regression tests, including committed parser and decoder seeds, with:

```sh
cargo test --locked --lib --bins
```

Run bounded discovery for each target:

```sh
rustup run nightly-2025-09-15 cargo fuzz run enrollment_parse target/fuzz-cache/enrollment_parse fuzz/corpus/enrollment_parse -- -max_total_time=60 -timeout=5 -rss_limit_mb=2048 -max_len=8192
rustup run nightly-2025-09-15 cargo fuzz run control_raw target/fuzz-cache/control_raw fuzz/corpus/control_raw -- -max_total_time=60 -timeout=5 -rss_limit_mb=2048 -max_len=8192
rustup run nightly-2025-09-15 cargo fuzz run control_authenticated target/fuzz-cache/control_authenticated fuzz/corpus/control_authenticated -- -max_total_time=60 -timeout=5 -rss_limit_mb=2048 -max_len=8192
```

The Socket targets use synthetic credentials and do not read configuration, databases, network services, or live credentials. Its advisory workflow runs on pull requests, manual exact-ref dispatch, and a daily 04:27 UTC schedule. On this host, ASan binaries abort under the global allocator preload, so local discovery used libFuzzer without ASan; this is not equivalent to ASan-enabled GitHub evidence. The rolling discovery cache is separate from committed regression seeds, and cache growth is informational, not a pass condition.

## Candidate evidence

Candidate replay remains advisory and must never delay releases. Evidence should identify source replay for the exact candidate commit; a packaged APK alone does not prove fuzzing ran against that source. Record:

- candidate source repository and full commit SHA;
- harness/replay revision and exact command;
- deterministic regression results and bounded discovery result;
- finding triage and exact reproducers when applicable.

Use synthetic accounts and data only. CI workflows have read-only repository access and no live-account secrets. Keep deterministic regressions separate from rolling discovery data. Preserve crash inputs byte-for-byte with the source commit, tool version, exact command, and replay result. Promote confirmed findings to deterministic regressions.

## Gate policy

Keep the pilot advisory. Required checks and actual branch protection/enforcement remain unchanged and require separate changes.
