# Security fuzzing pilot

The advisory pilot exercises focused production entry points in [Collie Socket](https://github.com/0cwa/colliesocket/tree/main/fuzz) and [Collie Android](https://github.com/0cwa/collieim-android/tree/main/tooling/fuzz-encrypted-push). It covers Socket enrollment parsing and control-frame decoding in raw and synthetic-keyed modes, Android event parse/decide behavior, and endpoint-update promotion sequences. Android `ControlFrame.build` constructs outgoing authenticated frames; its fixed-nonce check covers repeatable construction, header identity, and size, not independent authentication verification or incoming decryption. The Android arbitrary-byte target derives decision context from the production parse result and uses the default `identityMatches = true`; it checks parser/decision consistency and replay handling, not an independent schema or enrollment-identity oracle. The pilot does not claim coverage of the push provider, Collie Pack, native code, or Python tooling.

## Scope and invariants

Use the pinned Collie Socket v3 contract as the oracle. Assert contract behavior, not implementation details:

- Identity: account selection precedes receipt, epoch, and sequence handling; receipts are scoped exactly by `(account_id, request_id)`.
- Replay: a historical receipt may be returned, but it never attests current state. A conflicting replay must not mutate state.
- Rejection: reject an ineligible import unless its `import_id`, `key_id`, context, epoch, and newer sequence satisfy the contract.
- Recovery: the server never permits a lower epoch. Phone-side explicit recovery may promote a lower epoch only with the exact eligible identity/import and a newer sequence. Ordinary export is not rejected merely because it is below the remembered server epoch.

The Android contract fixture is pinned under `app/src/test/resources/colliesocket-contract-v3/`; retain its existing ownership and verification mechanism. Do not silently regenerate or edit the fixture to make a failing assertion pass. Changes to assertions or exclusions require independent review against the contract and production behavior.

## Regression and bounded discovery

Use the harness instructions at the source revision being tested:

- [Android JVM checks](https://github.com/0cwa/collieim-android/blob/main/tooling/fuzz-encrypted-push/README.md) cover deterministic replay, bounded Jazzer discovery, resource limits and crash storage.
- [Socket fuzzing](https://github.com/0cwa/colliesocket/blob/main/fuzz/README.md) covers pinned tools, bounded discovery, crash reproduction and regression seeds. Its ordinary `cargo test --locked --lib --bins` run replays committed seeds.

Keep rolling discovery caches separate from committed regression inputs. Cache
growth is informational, not a pass condition. Record the exact source revision,
tools, commands and resource bounds with each run.

## Candidate evidence

Candidate replay remains advisory and must never delay releases. Evidence should identify source replay for the exact candidate commit; a packaged APK alone does not prove fuzzing ran against that source. Record:

- candidate source repository and full commit SHA;
- harness/replay revision and exact command;
- deterministic regression results and bounded discovery result;
- finding triage and exact reproducers when applicable.

Use synthetic accounts and data only. CI workflows have read-only repository access and no live-account secrets. Keep deterministic regressions separate from rolling discovery data. Preserve crash inputs byte-for-byte with the source commit, tool version, exact command, and replay result. Promote confirmed findings to deterministic regressions.

## Gate policy

Keep the pilot advisory. Required checks and actual branch protection/enforcement remain unchanged and require separate changes.
