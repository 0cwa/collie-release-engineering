# Retained receipt contracts

The receipt helpers and fixtures remain for historical verification and existing
distribution imports. Current release procedures are in the
[development cycle](../DEVELOPMENT-CYCLE.md#releases),
[Stable feed inputs](../../distribution/RELEASE-INPUTS.md) and
[TrailBlazer Beta feed](../../distribution/BETA-FEED-OPERATIONS.md).
The current Beta feed does not use the retired receipt/readiness route.

## Receipt verification

[`migration_receipts.py`](../../tools/release/migration_receipts.py) defines and
validates the source, build, release and repository receipt contracts. Its parser
rejects unknown, missing and duplicate fields. The `stable` profile is the default;
`--channel trailblazer-beta` selects the retained Beta identity contract.

Receipt identities use canonical JSON digests. `release_manifest_sha256` instead
binds the exact raw final-manifest bytes; `release_receipt_sha256` binds the
canonical release receipt. The verifier checks independently approved digests and
cross-document links. A digest supplied by a candidate is not approval.

```sh
python3 tools/release/migration_receipts.py \
  --source source.json --build build.json --release release.json \
  --repository repository.json --approvals approved-digests.json
```

`--promotion` requires completed evidence. The retained Beta prepublication mode
also requires `--repository-stage prepublication`: signed-index and client checks
must pass, while production HTTPS read-back remains `NOT RUN` until publication.
These unsigned receipts establish document consistency; operators still verify
the linked evidence. A historical import preserves missing evidence as `UNKNOWN`.

## Retained runtime adapters

[`runtime_receipts.py`](../../tools/release/runtime_receipts.py) adapts the
collector, signer and publisher in `tools/release`. Plans explicitly select
`legacy` or `new-source`; missing receipts never select legacy automatically.
Plan approval binds raw bytes, while source/build/release approvals bind canonical
receipt digests. Approvals are obtained outside the candidate.

Production new-source collection verifies the checkout and invokes the pinned
[`preparation_verifier.py`](../../tools/release/preparation_verifier.py). The
operator selects the baseline before preparation, then reviews the actual project
log and changelog before approving the build receipt. Signing preserves version
reservations, and publication retries require the same approved bytes. Use the
helpers' `--help` for their arguments.

## Fixtures

[`fixtures/`](fixtures/) contains historical RC1 manifest/provenance inputs and
synthetic receipt graphs used by
[`test_migration_receipts.py`](../../tools/release/test_migration_receipts.py).
Synthetic graphs deliberately contain `UNKNOWN` evidence and fail promotion. The optional
[`runtime_pipeline_smoke.py`](../../tests/runtime_pipeline_smoke.py) exercises the
retained adapters with a disposable fixture APK and key; it does not build Collie
or publish a production release.
