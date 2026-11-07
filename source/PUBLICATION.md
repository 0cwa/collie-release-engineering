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
