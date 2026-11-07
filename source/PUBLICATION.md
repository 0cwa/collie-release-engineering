# Source publication and contributor round trip

The application is intended to contain the code Gradle compiles. Engineering
construction is a maintainer operation before compilation. Publication is
currently a draft until public native inputs and direct build acceptance pass.

## Identities and approval

1. Review the engineering recipe/input commit and fresh reconstruction report.
2. Pin a complete publication plan: exact official Signal commit, current
   application main, all complete intermediate trees, reconstruction endpoint,
   standalone-build and publication overlays, and complete final tree. A plan
   contains no generated commit hash, avoiding a self-reference.
3. A trusted operator/verifier outside the candidate computes and approves the
   plan file SHA-256 and re-reads current remote main. Run the trusted revision of
   `source/publish.py --repo REPLAY --plan PLAN --approved-plan-sha256 SHA256
   --current-main CURRENT`. Candidate jobs receive no credentials and cannot
   supply their own trusted verifier/policy. The tool produces deterministic G
   and I, where I has parents [old main, G] and its complete tree equals G.
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

The publication layer receives `application_base=plan['old_main']` and the same
approved plan. That exact immutable application commit supplies `README.md`,
`LICENSE`, `NOTICE`, and `assets`; these user-facing files are preserved without
rewriting the README. `publish.py` and candidate verification compare their
complete Git entries, including asset modes and symlinks, to `plan.old_main`.
When an ordinary source contribution becomes part of application main, bind
the regenerated plan and publication layer to that newly reviewed main tip. A
stale baseline is rejected instead of silently restoring old user content.

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

An import's pinned main snapshot must have exactly the reviewed contribution's
complete tree, including repository files and modes. Ancestry alone does not
permit dropping later main changes. If main advances, review/import the complete
newer input or import reviewed deltas in order against matching immutable
snapshots; do not merely rebind an older result to the newer main. Intermediate
plans do not authorize publication: the final plan/base and complete output must
match the independently read live main before fresh G/I approval. No automatic
batch merge or rebase is performed by the importer.
