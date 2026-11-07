# Versioned handoffs and ownership

Keep one canonical Collie artifact with optional FCM; all five filenames are
byte aliases. No additional product profiles are introduced. Application owns
reviewed source, Gradle/native recipes, tests, BUILD and Git-only VERIFY.
Engineering owns reconstruction, receipts, upstream synchronization, release
controls and binary repository infrastructure. Keep old route until cutover.

The strict `tools/patches/migration_receipts.py` parser defines schema2 source,
build, release and repository receipts; JSON schema is included beside this file.
Envelope: schema, kind, payload, evidence. Unknown or duplicate fields fail.
`digest` hashes canonical JSON (sorted keys, compact ASCII UTF8) rather than file
whitespace. Evidence PASS requires an evidence SHA256, but is still a claim until
that evidence is independently checked. UNKNOWN/NOT RUN are never success.
These unsigned documents are not signed attestations.

Source receipts bind authentic upstream, generator/verifier/engineering revisions,
ordered layer digests, reconstructed tree, ordered overlay digests and final G/tree.
G never contains its own receipt/hash. Build receipts bind the source receipt,
G/tree, recipe, toolchain/dependency/native digests, unsigned APK and declared
trust level. Release receipts separately bind source/engineering/workflow IDs,
build/source receipts, package/version, signed bytes and APK signer, all five
aliases. Repository receipts bind the approved release digest, exact APK, index
signer (distinct from APK signer), fdroidserver/metadata versions and index hashes.

`release_artifacts.validate_manifest` keeps schema1 equality semantics intact;
schema2 requires independently supplied source_commit, engineering_revision and
workflow_revision approvals. Do not infer approvals from candidate contents.
The historical adapter reports missing source/build receipts as UNKNOWN.

## Version reservation and local operator migration

Production signing and actual own-repository publishing now require
`--version-ledger`, `--published-inventory`, and
`--published-inventory-sha256`. Rehearsal and publish dry-run remain available.
The inventory is a freshly reviewed JSON list of {release, code, sha256}; obtain
codes and bytes from actual published APKs, not tag arithmetic. Authenticate its
SHA256 outside the candidate. Keep one durable ledger on the signing host; all
sign/promote commands MUST use the same ledger/lock. This is not a distributed
lock. Do not enable another publisher without a shared serialization mechanism.

Reservations bind version_name, code and unsigned-manifest digest, under flock,
atomic replace and fsync. Publish binds that reservation to signed bytes and
rejects substitution. New signing/promoting must exceed RC1 code177702 and all
inventory/reservation codes. Failed signing retains its reservation; resume exact
inputs. An already published RC1 feed ingestion preserves code/bytes and does
not go through new-release signing. Historical parsers retain RC3 semantics.

Operator rollback: retain old main, tag, APK and signer; revert preparatory code
if necessary, retaining ledger/evidence and never reusing allocated codes. Do not
merge cutover until reviewed engineering refs are durable and G/I/base are pinned.
Production index key provisioning, DNS and publication require separate approval.

## Executable graph verification

Use `python3 tools/patches/migration_receipts.py --source source.json --build
build.json --release release.json --repository repository.json --approvals
approved-digests.json` to compare all four canonical document digests with
independently authenticated approvals and check every cross-document link.
The approval document has exactly source, build, release, repository SHA256s;
never calculate approval from a candidate and present that as authorization.
Source commit/tree, engineering revision, APK digest and APK signer must agree
throughout the graph. Shape-valid replacement documents fail these checks.

Add `--promotion` for a promotion decision: every receipt must have nonempty
PASS evidence with digests. UNKNOWN/NOT RUN/BLOCKED/FAIL cannot promote. This
verifies document consistency, not truth of evidence or signer attestations;
operators still inspect/authenticate linked evidence and approve immutable
inputs. Synthetic graph fixtures deliberately contain UNKNOWN evidence and
therefore fail promotion. Historical RC1 remains importable, not upgraded into
a complete new evidence chain.

Current non-rehearsal signing requires explicit single-collie mode before any
key access. Own-repository publishing requires the same mode before any GitHub
mutation, including dry-run validation. Historical five-profile parsing and
rehearsal remain available for verification; production cannot select them.

## Distinct raw and canonical release bindings

`release.payload.release_manifest_sha256` and
`repository.payload.release_manifest_sha256` both mean SHA256 of exact raw
final-manifest.json bytes. `repository.payload.release_receipt_sha256` means
canonical `migration_receipts.digest(release-v2)` and is separately bound. Never
interpret a raw-file digest as a canonical receipt identity. A legacy repository
receipt explicitly uses null canonical receipt identity, parses as legacy-only
evidence, and cannot pass full graph promotion. `verify_release_inputs` consumes
externally approved source/build/release receipt digests plus the raw manifest
digest before new-schema binary ingestion. Historical RC1 schema1 remains readable
without manufacturing source/build evidence. Draft-v2 schema fixtures were
amended before production use; no old signed release is changed.
