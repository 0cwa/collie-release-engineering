# Security fuzzing pilot

This planned pilot covers four production responsibilities: Socket enrollment parsing; Socket control decryption in raw and synthetic-keyed modes; Android event parse/decide; and Android endpoint-update promotion sequences. Android `ControlFrame` builds authenticated outbound control frames; it is not an incoming payload decrypt target. These harnesses are not yet implemented. The pilot does not claim coverage of Whistle, Collie Pack, native code, or the Python tooling.

## Scope and invariants

Keep the target set tied to production entry points:

- Socket enrollment parser and control decrypt path, including raw input and synthetic keyed input.
- Android event parse/decide path and endpoint-update promotion sequences. Android authenticated `ControlFrame` construction is outbound and separate from incoming event handling.

Use the pinned Collie Socket v3 contract as the oracle. Assert contract behavior, not implementation details:

- Identity: account selection precedes receipt, epoch, and sequence handling; receipts are scoped exactly by `(account_id, request_id)`.
- Replay: a historical receipt may be returned, but it never attests current state. A conflicting replay must not mutate state.
- Rejection: reject an ineligible import unless its `import_id`, `key_id`, context, epoch, and newer sequence satisfy the contract.
- Recovery: the server never permits a lower epoch. Phone-side explicit recovery may promote a lower epoch only with the exact eligible identity/import and a newer sequence. Ordinary export is not rejected merely because it is below the remembered server epoch.

The Android contract fixture is pinned under `app/src/test/resources/colliesocket-contract-v3/`; retain its existing ownership and verification mechanism. Do not silently regenerate or edit the fixture to make a failing assertion pass. Changes to assertions or exclusions require independent review against the contract and production behavior.

## Regression and discovery runs

First add deterministic executable runtime fixtures for these responsibilities. The existing Android JUnit4 contract test inspects fixture values; it is not a production runtime adapter. Preserve existing fixture ownership rather than introducing a second canonical copy. Add bounded discovery fuzzing after the deterministic fixtures, limited to relevant pure actual sources under isolated Jazzer harnesses.

Run deterministic regressions on each PR and bounded discovery on a nightly schedule. Promote findings to deterministic regressions. Use synthetic accounts and data only; CI Actions receive no secrets or live-account access, and fuzz jobs are read-only.

## Candidate evidence

Candidate replay will remain advisory and must never delay releases. When implemented, evidence should identify source replay for the exact candidate commit; a packaged APK alone does not prove fuzzing ran against that source. Record:

- candidate source repository and full commit SHA;
- harness/replay revision and exact command once implemented;
- deterministic regression results and bounded discovery result;
- finding triage and exact reproducers when applicable.

The worker harness commands are not yet established; do not claim replay is complete before they exist and are recorded. Independently review changes to assertions or exclusions.

## Gate policy

Keep the pilot advisory. Required checks and actual branch protection/enforcement remain unchanged and require separate changes.
