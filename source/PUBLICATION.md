# Source publication and contributor round trip

The application repository contains the source Gradle compiles, but its authoring
authority remains the canonical layered patch recipe in engineering. Published
application source is a generated view: never manually edit it, on any branch or
worktree, and never merge a source change first and export a patch afterward.
Engineering construction precedes publication and compilation. Publication is
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

Source receipts are emitted *after* G exists and live outside G. Receipt hashes
are implementation evidence unless separately signed; they do not prove
independent reproduction. Neither equal trees nor upstream ancestry proves equal
APKs: Git history, paths, timestamps and dependency/native inputs can change
build metadata. Old RC1 reconstruction and merged-main reconstruction are
separate identities. A new G is never retroactively called RC1's original input.

## Patch-first application changes

1. Resolve the approved recipe and inputs. Classify the change in the existing
   compatibility, behavior-fix/addition or attributed-fork layer structure;
   pipeline/tool changes remain separate. Follow `docs/PATCH-PIPELINE.md` rather
   than choosing a new layer position to avoid replaying later checkpoints.
2. Diagnose and implement only in a disposable reconstruction. A reviewed
   single-parent scratch commit may be exported with `round_trip(repo, before,
   reviewed, patch_path)` from `source/publish.py`. This records complete
   before/after trees and patch digest. The binary full-index/no-renames delta
   preserves binary files, modes, symlinks, deletions and renamed-file content.
3. Commit the patch, source provenance and tests in its proper canonical
   engineering layer before proposing a published application change. Preserve
   immutable upstream mailboxes and credits; replay affected downstream layers
   and compute their checkpoint trees, bundle prerequisites and hashes. Do not
   hand-edit digests or regenerate unrelated layers.
4. Run the applicable scope/provenance, complete-tree, report/line-origin,
   native-input, build and test gates, reporting missing evidence explicitly.
   Append or update the reviewed publication plan only after the canonical
   layer chain is established; regenerate G/I and compare complete trees.
5. Publish the generated source update with its bound engineering-layer and
   receipt references. No manually authored application-code PR is the primary
   contribution. A parity mismatch blocks publication; code review, a successful
   APK or layout-only CI does not replace this proof.

The published root `AGENTS.md` comes from
`source/policies/application-AGENTS.md` via `source/layers/publication.py`.
Change generated policy/build/verification guidance in that producer as well;
regeneration must preserve the patch-first rule and the user's README wording.
A changed layer or plan requires fresh externally bound approval under the
identities and approval rules above. Future upgrades integrate old Collie main
and the new Signal-based generated endpoint while checking both histories and
exact final-tree equality.

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

## Historical source-first recovery

`source/import_contribution.py` is retained to validate or recover historical
source-first contributions. It does not authorize new code-first application
work. Any exceptional use needs an explicit user decision, rather than an
inference from the helper's existence. It consumes a reviewed single-parent
application commit and exports/replays the exact binary/mode delta against the
complete approved source tree. Supply `--current-main` from an independent remote read,
not the old cutover base: after a normal source contribution merges, that base
has moved. The helper requires the old application history and the reviewed
contributor commit to remain ancestors of current main, rebinds plan.old_main,
then emits a plan for full publication regeneration. Approval must be renewed
for changed current main/plan/G/I. This preserves actual contributor authorship
in the integration ancestry as well as explicit attribution in generated
layers. The immutable historical Molly forest remains unchanged; new work
belongs in its documented canonical layer. Fixture roundtrips against actual
candidate G/I are test evidence, not production contributions.
