# Source publication and contributor round trip

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

## Optional local SSH signatures for generated commits

Normal candidate generation remains unsigned and deterministic. Candidate CI
stays credential-free. For a GitHub-verified source candidate, an operator may
take a separately reviewed unsigned G/I pair through `source/sign_publication.py`
on the trusted local publication machine. It creates a signing request that
binds the plan and message digests, current main, unsigned G/I IDs, publication
timestamp, SSH public-key fingerprint, and committer identity. Review and approve
the exact request digest out of band before using the `sign` action. Preparation
does not invoke the signer; signing refuses any mismatch with the approved
request.

The signing helper asks `ssh-keygen` to sign through the already-running local
SSH agent, selecting the supplied public `.pub` key. It does not read a private
key file, capture private-key or passphrase values, or write key material into
the repository. This requires Git 2.34+ and an OpenSSH `ssh-keygen` with agent
signing support. Register a dedicated public key with the GitHub account using
the `signing` key type. Do not reuse an authentication key, the normal GitHub
signing key, or an APK/repository-index key. The committer email is explicit in
the approval request and must be an email GitHub attributes to that account;
the account's private `noreply` address is suitable when selected by the owner.
The tool cannot verify account email ownership or key registration locally.

The signing action reconstructs only generated layer commits and I, preserving
the approved trees, messages, parents, authors, and schema-2 timestamps while
using the approved committer identity. It never signs or rewrites upstream
commits. The signed object IDs differ from the unsigned IDs; the signed I points
to signed G. The output creates no refs and performs no push. Run the candidate
verifier with the independent public key, expected fingerprint, identity email,
and one-key `allowed-signers` file. It checks every generated layer and I in
addition to the existing tree, ancestry, message, and date checks. Do not claim
GitHub verification until the commits have been pushed through the ordinary
review process and GitHub reports each signature as verified. A local signature
check is not publication approval. Schema-1 historical replay cannot be signed
by this path.

The `prepare` action needs the approved plan, message file, current-main SHA,
unsigned G/I IDs, public `.pub` selector, and committer name/email. It reserves a
new signing-request file and reports its SHA-256. Independently review that
request, then supply the same inputs and that exact digest to the `sign` action.
The verifier additionally requires the approved message file/digest and signing
request/digest. It revalidates the request's unsigned G/I against the same plan,
messages, base, and timestamp, checks its author inventory, and compares each
signed commit with its unsigned counterpart for tree, message, author, date, and
translated parents. Keep the unsigned objects available to the verifier; when
they are in a separate object store, pass `--unsigned-repo UNSIGNED_REPLAY`. Its
`allowed-signers` input must contain exactly one entry in the form
`EMAIL namespaces="git" KEY_TYPE PUBLIC_KEY_DATA` matching the requested public
key and identity. These are interface placeholders, not key paths or secret
values.

The command interfaces are:

```
python3 source/sign_publication.py prepare --repo REPLAY --plan PLAN \
  --approved-plan-sha256 PLAN_SHA256 --messages MESSAGES \
  --approved-messages-sha256 MESSAGES_SHA256 --current-main CURRENT_MAIN \
  --source UNSIGNED_G --integration UNSIGNED_I \
  --signing-public-key SIGNER_PUBLIC_KEY.pub --committer-name NAME \
  --committer-email VERIFIED_OR_NOREPLY_EMAIL --output SIGNING_REQUEST

python3 source/sign_publication.py sign --repo REPLAY --plan PLAN \
  --approved-plan-sha256 PLAN_SHA256 --messages MESSAGES \
  --approved-messages-sha256 MESSAGES_SHA256 --current-main CURRENT_MAIN \
  --source UNSIGNED_G --integration UNSIGNED_I \
  --signing-request SIGNING_REQUEST \
  --approved-signing-request-sha256 APPROVED_REQUEST_SHA256 \
  --signing-public-key SIGNER_PUBLIC_KEY.pub --allowed-signers ALLOWED_SIGNERS
```

Signed verification adds `--messages MESSAGES --expected-messages-sha256
MESSAGES_SHA256 --signing-public-key SIGNER_PUBLIC_KEY.pub
--expected-signer-fingerprint SHA256_FINGERPRINT --signing-identity-email
VERIFIED_OR_NOREPLY_EMAIL --allowed-signers ALLOWED_SIGNERS --signing-request
SIGNING_REQUEST --expected-signing-request-sha256 APPROVED_REQUEST_SHA256` to
the read-only `verify_candidate.py` command above. Add `--unsigned-repo
UNSIGNED_REPLAY` when the approved unsigned G/I objects are not present in
`--repo`. No command in this flow
updates a ref or pushes a commit.

The required MollySocket TLS correction is a feature layer for the next release.
Apply its exact reviewed patch from `source/layers/mollysocket.patch` after the
complete source checkpoint recorded in `source/layers/mollysocket.json`; retain
the original commit and author in the generated layer's provenance. Its patch
includes both the trust-manager implementation and regression tests. Do not
copy later permanent-policy documentation from the old application PR.

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
