# Source publication and contributor round trip

> **Historical workflow.** The current source and release process is [the development cycle](../docs/DEVELOPMENT-CYCLE.md): app `main` is the source and application changes go through ordinary signed app PRs. This page describes the former generated G/I publication commits and plan/receipt composer; retain it for historical replay, not new publication work.

The application is intended to contain the code Gradle compiles. Engineering
construction is a maintainer operation before compilation. Publication is
currently a draft until public native inputs and direct build acceptance pass.

## Identities and approval

1. Review the engineering recipe/input commit and fresh reconstruction report.
2. Pin a complete publication plan: exact official Signal commit, current
   application main, all complete intermediate trees, reconstruction endpoint,
   standalone-build and publication overlays, and complete final tree. A plan
   contains no generated commit hash, avoiding a self-reference. New plans use
   schema 2 and include a canonical UTC `publication_timestamp`; the timestamp
   is covered by the approved plan digest and supplies the author and committer
   dates for each generated publication commit. Ambient `GIT_*_DATE` values are
   ignored. Schema 1 plans remain available for historical replay only; replay
   them with `--historical-publication` (or the explicit historical-message
   mode) to preserve their original fixture timestamp.
   The production draft composer (`source/compose_security_publication.py`)
   requires `--publication-timestamp YYYY-MM-DDTHH:MM:SSZ`; it upgrades the
   retained checkpoint plan to schema 2 before hashing the plan or generating
   editable message previews. The timestamp therefore travels through the
   approved plan digest used by both the publisher and candidate verifier.
3. A trusted operator/verifier outside the candidate computes and approves the
   plan file SHA-256 and re-reads current remote main. Run the trusted revision of
   `source/publish.py --repo REPLAY --plan PLAN --approved-plan-sha256 SHA256
   --current-main CURRENT`. Candidate jobs receive no credentials and cannot
   supply their own trusted verifier/policy. The tool produces deterministic G
   and I, where I has parents [old main, G] and its complete tree equals G.
   Candidate verification also checks generated commit dates against a schema 2
   plan's approved timestamp.
4. Review every path in `git diff OLD_MAIN I` and `git diff SIGNAL G`. Full-tree
   equality includes binary files, modes and symlinks; directory allowlists are
   not evidence of equality. Composite layers use explicit generated authorship
   and retain original layer provenance rather than impersonating source authors.
5. Create retained candidate refs, then an ordinary draft PR whose head is I.
   Approval names plan digest, old main, G and I. A changed base or layer invalidates
   that approval. Do not accept GitHub's synthetic test merge as I.
6. Before merge eligibility, engineering dependencies must be merged, pinned to
   durable approved refs, regenerated and reverified. Do not force push main or
   move old release tags. Keep candidate refs through review and protect retained
   release source refs using separately approved repository settings.

## Optional SSH signatures for generated commits

Unsigned generation remains the default, and candidate CI needs no signing credentials. An approved signing request can bind the publication plan and message digests, current main, unsigned G/I commits, schema-2 timestamp, public-key fingerprint, committer identity, and generated-author inventory. The request is a protocol record; its values identify one exact candidate graph.

The shape-only example below uses placeholders; a real request lists every generated author. It identifies the unsigned graph and signer without prescribing where or how an operator carries out signing:

```json
{
  "schema": 1,
  "purpose": "collie-source-publication-ssh-signing",
  "publication_plan_sha256": "<plan-digest>",
  "messages_sha256": "<message-digest>",
  "current_main": "<base-commit>",
  "unsigned_source": "<generated-G>",
  "unsigned_integration": "<generated-I>",
  "publication_timestamp": "<approved-UTC-timestamp>",
  "signer_fingerprint": "SHA256:<public-key-fingerprint>",
  "committer_name": "<committer>",
  "committer_email": "<verified-identity@example.invalid>",
  "authors": [{"name": "<author>", "email": "<author@example.invalid>"}]
}
```

The implementation also binds the approved plan and message digests, committer identity, and author inventory. A public-key verifier checks signatures on every generated layer and I, then verifies that signing preserved each approved tree, message, author, date, and parent relationship. I retains parents `[old main, signed G]`; upstream commits are neither signed nor rewritten. Signed output creates no refs and performs no push. The unsigned objects remain available for comparison.

The verifier requires a public key and an allowed-signers policy matching the approved signer. Schema-1 historical replay cannot use this signing path; signed candidates use timestamp-bound schema 2. A local signature check does not establish publication approval or GitHub's verified-signature status. Only GitHub's status after ordinary review can establish the latter. Git 2.34 or later and OpenSSH SSH-signing support are required by the implementation.

The required MollySocket TLS correction is a feature layer for the next release.
Apply its exact reviewed patch from `source/layers/mollysocket.patch` after the
complete source checkpoint recorded in `source/layers/mollysocket.json`; retain
the original commit and author in the generated layer's provenance. Its patch
includes both the trust-manager implementation and regression tests. Do not
copy later permanent-policy documentation from the old application PR.

The publication layer receives `application_base=plan['old_main']` and the same
approved plan. That exact immutable application commit supplies `README.md`,
`LICENSE`, `NOTICE`, and the approved asset tree. The producer places assets in
`docs/assets/`, moves the build guides into `docs/`, and rewrites only the
README's relative asset and legal-document links. `publish.py` checks the exact
README bytes after those path rewrites, preserves LICENSE/NOTICE entries, and
compares every asset's relative path, mode, type, blob, and symlink target with
the approved base. It accepts `assets/` as the historical base path and
`docs/assets/` after migration. Regenerating `docs/BUILD.md` also carries forward
the approved final `Published source dates` section from either supported build-guide path.

The same output pass places build helpers under `tooling/` and FOSS Google
service modules under `core/gms/`. Gradle project IDs such as `:core-gms:base`
remain stable through explicit `projectDir` mappings in `settings.gradle.kts`.
Root build entrypoints and root `LICENSE`, `NOTICE`, and `README.md` remain in
place. When an ordinary source contribution becomes part of application main,
bind the regenerated plan and publication layer to that newly reviewed main
tip. A stale baseline is rejected instead of silently restoring old user
content.

Source receipts are emitted *after* G exists and live outside G. Receipt hashes
are implementation evidence unless separately signed; they do not prove
independent reproduction. Neither equal trees nor upstream ancestry proves equal
APKs: Git history, paths, timestamps and dependency/native inputs can change
build metadata. Old RC1 reconstruction and merged-main reconstruction are
separate identities. A new G is never retroactively called RC1's original input.

## Ordinary contributions

Contributors edit application files and open normal source PRs. Maintainers
export an approved single-parent delta using `round_trip(repo, before, reviewed,
patch_path)` from `source/publish.py`; the output records whole before/after trees
and patch digest. `git diff --binary --full-index` preserves binary files, modes,
symlinks, deletions and renamed-file content without rename ambiguity.

Commit that patch and its provenance as a new engineering feature layer. In a
fresh reconstruction apply with `git apply --index --binary`, require its before
tree and full resulting after tree, append it to the approved plan, regenerate G,
and compare the complete reviewed application tree. A mismatch blocks release.
Do not regenerate unrelated layers or silently choose one divergent copy. Future
upgrades integrate old Collie main and the new Signal-based generated endpoint;
synthetic upgrade coverage checks both histories and exact final tree.

## Recovery

Preparatory engineering merges do not remove the old local application route.
If cutover checks fail, leave application main unchanged and retain candidates
for review. If a later source cutover needs reversal, create a reviewed ordinary
revert/integration commit preserving ancestry; never reset protected history.
Released binaries, tags and signing keys are unaffected. A defective binary is
fixed through a higher version, never by replacing published bytes.

## Candidate verification command (read-only)

Obtain the trusted verifier commit through the engineering review process, check
out that exact immutable revision in a separate clean directory, and independently
approve the full plan digest. Re-read `git ls-remote --refs
https://github.com/0cwa/collieim-android.git refs/heads/main` immediately before the
following command. Pass the actual remote value, not a candidate-controlled value:

```
python3 source/verify_candidate.py --repo REPLAY --plan PLAN \
  --expected-plan-sha256 EXTERNALLY_REVIEWED_DIGEST \
  --current-main ACTUAL_REMOTE_MAIN --expected-source APPROVED_G \
  --expected-integration APPROVED_I
```

This checks the intended immutable G/I and current base, never the synthetic PR
merge. Layout CI in the application candidate is explicitly not this approval.
This verifier is credential-free; manually approving it is separate from APK
signing, index signing and production publication. A candidate cannot select its
own trusted verifier. Required-check/ruleset administration remains an operator
item; no policy protection is claimed merely because this command exists.

## Import after an ordinary source PR merges

`source/import_contribution.py` consumes a reviewed single-parent application
commit and exports/replays the exact binary/mode delta against the complete
approved source tree. Supply `--current-main` from an independent remote read,
not the old cutover base: after a normal source contribution merges, that base
has moved. The helper requires the old application history and the reviewed
contributor commit to remain ancestors of current main, rebinds plan.old_main,
then emits a plan for full publication regeneration. Approval must be renewed
for changed current main/plan/G/I. This preserves actual contributor authorship
in the integration ancestry as well as explicit attribution in generated
layers. No contributor edits the engineering patch forest. Fixture roundtrips
against actual candidate G/I are test evidence, not production contributions.
This single-commit path does not resolve multiple independently branched PRs
that have already landed; use the explicit aggregate workflow below for that case.

An import's pinned main snapshot must have exactly the reviewed contribution's
complete tree, including repository files and modes. Ancestry alone does not
permit dropping later main changes. If main advances, review/import the complete
newer input or import reviewed deltas in order against matching immutable
snapshots; do not merely rebind an older result to the newer main. Intermediate
plans do not authorize publication: the final plan/base and complete output must
match the independently read live main before fresh G/I approval. No automatic
batch merge or rebase is performed by the importer.

## Synchronizing an independently landed PR backlog

If several ordinary PRs based on the same earlier application main have already
landed, synchronize them as one explicitly reviewed aggregate delta. Let `S` be
the approved plan's `old_main` and `T` the independently read current remote
main tip. `S` must be an ancestor of `T`, and the complete tree at `S` must equal
the plan's `final_tree`. Re-read the remote tip before the final publication
replay; pass that exact value as `--current-main`.

Review the complete binary/mode delta and its digest using the same deterministic
Git options as the importer:

```
git -C APP_REPOSITORY diff --binary --full-index --no-ext-diff --no-textconv \
  --no-renames S T -- > reviewed-backlog.patch
sha256sum reviewed-backlog.patch
python3 source/import_contribution.py --repo APP_REPOSITORY --plan APPROVED_PLAN \
  --aggregate --current-main T --overlay imported-backlog.patch \
  --expected-patch-sha256 REVIEWED_SHA256
cmp reviewed-backlog.patch imported-backlog.patch
```

The helper computes the same `S..T` delta, checks its separately reviewed
digest, applies it in a private index, and requires the complete resulting Git
tree to equal `T`, including every path, blob, mode, symlink, and gitlink. It
writes no overlay and rebinds no plan on a stale/non-descendant tip, base-tree
mismatch, digest mismatch, apply conflict, or extra/missing/different result.
The new layer records `S`, `T`, and the digest as composite provenance. The
final integration keeps `T` as its application-history parent, preserving the
history recorded in that tip. The aggregate tree delta does not establish the
original commit or author for each PR, so generated publication makes no such
individual attribution claim. The importer does not resolve conflicts, merge,
or rebase PRs.
