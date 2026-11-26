# GitHub Pages distribution and two-phase promotion

For an existing production site without `site-manifest.json`, use the explicit
[inventory-only bootstrap](INVENTORY-BOOTSTRAP.md). Normal Stable publication,
including `force=true`, continues to require a verified existing inventory.

The Stable feed is live at [repo.collie.im](https://repo.collie.im/) and
[`/fdroid/repo/`](https://repo.collie.im/fdroid/repo/), maintained by the scheduled
[`feeds.yml`](../.github/workflows/feeds.yml) workflow in release engineering.
Native inputs share this site and its storage budget; see
[NATIVE-STORAGE.md](NATIVE-STORAGE.md). Keep one canonical unchanged signed APK
per selected version.

The two-phase [`pages-release.yml`](../.github/workflows/pages-release.yml) route
for approved native/receipt-based promotions remains subject to the exact gates
below. It publishes Stable only. Since 2026-11-22 TrailBlazer Beta is published by
the scheduled `feeds.yml` route ([BETA-FEED-OPERATIONS.md](BETA-FEED-OPERATIONS.md)),
and the Pages route only retains the served Beta files.
See the [dual-release policy](../docs/DUAL-RELEASE-POLICY.md) for the product and
source boundaries. For an existing Stable site without a canonical inventory,
see the separately reviewed [inventory bootstrap change (#61)](https://github.com/0cwa/collie-release-engineering/pull/61);
use that procedure only after its implementation is available at the approved
engineering revision.

## Hosting limits and tooling

GitHub's published site limit is1GB and monthly soft transfer limit100GB. Packaging
uses a conservative950,000,000byte ceiling for *each full site and its artifact*,
including every retained APK, index archive, image, receipt and inventory. Measure
the complete snapshot and archive before admission.
Snapshots are additive: no old immutable URL is deleted/overwritten automatically.
Before reaching the ceiling the operator must approve an archive/retirement or
hosting decision, with originals/receipts retained on immutable GitHub release
refs and a measured client/cache grace period. Merely moving files to another
Pages directory does not reduce this site's budget. No unbounded capacity claim.

Official references, inspected for this implementation:
- https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
- https://github.com/actions/upload-pages-artifact
- https://github.com/actions/deploy-pages

Python3.12.14 and hash-pinned fdroidserver2.4.5 remain verification tools; APK/index
signing is outside Pages. Official action commits are pinned in both workflows.
The two-phase workflow uses the official upload-pages-artifact composite at an
immutable commit, with `include-hidden-files: true` to preserve any approved hidden
paths if present. The recorded native-only preparation inventory has 15 paths and
does not include `.nojekyll`. Its nested upload-artifact action is also pinned in
that composite.
Local policy preparation still audits each exact site manifest and archive; the
wrapper differential changes only artifact construction and retains the same
per-file payloads. These preparation records do not establish hosted compatibility
for a new two-phase wrapper revision. That claim requires real acceptance evidence
for the exact approved policy, artifact and deployment; the gate procedure below
remains in force.

## Trusted approved input bundle

An operator prepares a data-only tar archive outside source Git:

```
approval.json
repository-receipt.json
releases/<selected-release>.json
bundles/<release-id>/{source,build,release}.json  # new-v2 approvals only
candidate/<exact public fdroid repo files>
previous/<complete last approved published site>
landing/{index.html,add-repository.svg}
native-manifest.json  # optional schema2 feed approval; required native-only
native/native-inputs/v1/maven/<exact six paths>
```

Production endpoint and provider-cache observations are completed only after
the approved snapshots are deployed. They are recorded and checked through the
Pages journal's canonical preflight and phase1/phase2 read-backs; they are not
written back as future `PASS` entries in the frozen repository receipt. A
missing or incomplete journal therefore still blocks completed-publication
verification.

The complete archive SHA256 and explicit engineering release-asset ID are approved
outside the candidate. No latest scraping, arbitrary remote URL, signing key,
configuration/password, source injection or script execution. The downloader uses
the fixed engineering repository API, checks asset ID/repository/size/hash, and
never sends a token to the public download redirect. Extraction rejects duplicate,
traversal, link and special members and caps size/file count. Candidate data never
replaces the checked-out trusted verifier.

Stable `approval.json` schema 1 has exact fields: kind collie-pages-input,
origin https://repo.collie.im/, policy_revision (reviewed Git commit),
previous_site_sha256 (canonical inventory digest of complete previous snapshot),
previous_manifest_sha256 (raw published site-manifest.json SHA256; explicit null
only for first empty origin), candidate_generation_sha256 (existing fdroid repo
inventory digest), repository_certificate_sha256, repository_receipt_sha256
(canonical shared schema2 receipt digest), release_approvals (JSON basenames),
landing_sha256 (canonical landing inventory digest), maximum_bytes<=950000000.
Stable schema 1 and schema 2 remain supported with their existing identity rules.
The signing/publication receipt's raw final-manifest and canonical release-receipt
links remain distinct. Legacy receipts have a null canonical release receipt
digest. New-v2 approvals require the approved source/build/release
bundle and full shared verifier. Production APK certificate stays pinned f8cc0c...;
repository test/production index identities are separate.

A previous snapshot must match the complete authoritative previously published
site-manifest.json. That public inventory lists every other site file; the outer
approval/plan binds the manifest's raw hash, avoiding self-reference. Missing an
old asset or substituting an incomplete locally invented inventory fails. Before
phase1, live preflight compares that manifest and every previous file at canonical
URLs. First publication requires explicit null prior manifest, an operator-approved
fresh origin, and404 for both manifest and active entry.jar. Absence of a manifest
alone does not establish that an unmanaged existing site is empty: bootstrap or
import its authoritative complete prior deployment artifact before approving.

Hold an exclusive publication lease for the whole operation: only this reviewed
workflow may publish while the operator approves the plan. Workflow concurrency
serializes these runs but cannot prevent an administrator using an unrelated
publisher. An external deployment changes the approval's previous-state premise;
stop and prepare/reapprove against the new complete state. Do not treat the
preflight as a provider-level conditional-write transaction.

Both production publishers also create a durable GitHub deployment record in
the `collie-pages-remote-deploy-guard` transient environment before calling the
Pages deployment API. This closes the runner-loss gap that the Actions
`collie-pages-production` concurrency group cannot cover after a job ends. The
guard record remains active until the Pages deployment status endpoint reports
one of the verifier's terminal success/failure states; a cancel `204` alone is
not treated as terminal. A later run checks this record before creating another
Pages deployment and can reconcile an earlier attempt only when the record
contains its Pages deployment ID and the Pages API reports terminal status.

If the Pages creation response is lost, or the runner stops before its Pages ID
is durably recorded, the active guard intentionally blocks both Stable and Beta
publishers. The current documented Pages API offers create, lookup by deployment
ID or commit SHA, and cancel, but no list endpoint for recovering an unknown
Pages ID. There is no automated clearing path for that case: preserve the hold
until an operator obtains authoritative provider terminal/reconciliation evidence
and resolves it under a separately reviewed recovery procedure. Do not infer
terminality from a cancel response, workflow completion, timeout, or public-site
readback. Creating these guard records uses only the token's `deployments: write`
scope in addition to the existing Pages deployment permissions. GitHub documents
that deployment records dispatch deployment events. This repository has no
workflow trigger for those events, and a read-only hook inventory returned no
repository hooks during review; recheck external integrations before production.
GitHub's [deployment API](https://docs.github.com/en/rest/deployments/deployments)
defines the persistent records and statuses used here. The [Actions concurrency
contract](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
documents one running and one replaceable pending run by default, so that group
is not the durable hold.

The deploy step repeats the safety checks inside the shared
`collie-pages-production` workflow lock, immediately before reserving a new
guard and sending the Pages creation request. Its order is: reconcile every
older guard against authoritative Pages status, compare the exact baseline
captured for this prepared candidate, create the durable reservation, then
POST the uploaded Pages artifact. A missing baseline is a CLI error. Phase 1
rechecks the prepared `previous_files` and raw prior `site-manifest.json` hash
(including the explicit empty-origin checks for first publication). Phase 2
rechecks the already-read-back phase 1 manifest carried by the `after-phase1`
control artifact. It never compares phase 2 against itself or adopts a newer
public readback as the baseline for an old prepared artifact. This also applies
when a failed job is rerun after another publication has advanced the site.

The scheduled Stable publisher captures and validates the complete served
inventory, including signed Stable and Beta indexes and every indexed byte,
before it downloads/builds the candidate. Its deploy step compares the live
canonical inventory digest with that fixed capture after reconciling older
deployments. If a pending Beta deployment finishes after Stable capture, the
inventory differs and the stale Stable site is rejected before a new durable
reservation or Pages POST. A transient read failure, unavailable inventory,
changed route or mismatched byte is a hard failure. The comparison is not a
provider-side conditional write: unrelated publishers that ignore this shared
workflow lock remain outside this guarantee.

## Two complete deployment snapshots

1. `pages.py prepare BUNDLE OUTPUT --policy REVIEWED_COMMIT` verifies the full
   approved repository generation, signatures, v1/v2 parity, production signer,
   receipt links, landing fingerprints, previous inventory and capacity. Both
   site artifacts are complete snapshots; all previous immutable URLs remain.
2. Phase1 adds new APK/assets and immutable signed-index previews under
   `_generations/<fdroid-generation>/`, retaining previous *active* indexes and
   landing. First publication instead shows preparation text without an add link
   or active feed. Preview index/receipt hashes can be read publicly without
   exposing the new active entry. APK bytes are stored once at canonical URLs.
3. The operator approves the protected phase1 deployment. Fresh canonical URL
   read-back checks every expected hash/size, including new APK and index previews,
   and records observed Cache-Control/ETag/Last-Modified/Age/X-Cache. Preflight sends
   no-cache revalidation headers and uses HEAD responses for fresh absence checks;
   existing expected files are GET-hashed. It rejects nonzero Age, `X-Cache: HIT`,
   or an HTTPS downgrade by default. Unknown/existing inventory, mixed bytes or
   interrupted requests do not advance the journal. Retry the same prepared plan;
   do not rebuild or re-sign APK.
4. Only successful phase1 read-back permits the separately protected phase2 job.
   It publishes the same immutable assets with the newly signed active indexes and
   approved landing. Final read-back checks the complete snapshot again. Public
   site-manifest.json changes as a mutable entry; no manifest embeds its own hash.

Pages offers snapshots, not atomic multi-request client transactions. Old/new
entry/index crossings must fail signature/hash checks and recover after refresh.
This tool does not assume custom cache headers on Pages. Local interruption,
wrong-generation, cached404 and retry tests model safety; actual Pages headers,
CDN convergence and official/compatible client recovery remain activation gates.
A cached404 at a canonical URL must resolve before phase2; a successful cache-busted
alternative is not sufficient. Do not weaken F-Droid signature/hash validation.

### Owner-authorized first native publication recovery

The exact native-only first-publication plan is a narrow exception to cached-negative
preflight failure. It is eligible only when the prepared approval binds the empty
previous-site digest and null previous manifest, the plan has no previous files, the
fixed six-file native manifest is exact, and both phase manifests are the same exact
15 approved paths/hashes/sizes. In that case, preflight records cached 404s as
`cached_negative_unverified_origin` and journals the explicit
`owner-authorized-first-publication-unverified-origin` classification. It keeps
`previous_verified=false`; Age 0 and X-Cache MISS are also not described as proof of
origin contact. A 200 at a planned native URL (or the planned site manifest) must be
GET-verified against exact approved size/hash; any other status or differing bytes
fails without advancing the journal. A 200 at the unapproved feed sentinel fails.

This classification only permits the already authorized initial phase to proceed.
It does not satisfy publication acceptance: phase 1 must still return HTTPS 200 and
exact bytes/SHA for all 15 canonical URLs before phase 2, and phase 2 must pass the
same full readback. In this exact recovery mode a returned 200 may carry cached
headers; the readback still GETs and verifies every byte and does not treat cache
metadata as origin proof. Streaming stops at the planned file size plus one byte
and never exceeds the 950,000,000-byte site budget. Default existing-inventory
checks, stale-journal rejection, HTTPS downgrade rejection, deployment/run
inventory checks before dispatch, and cleanup of the temporary production flag
remain fail-closed.

## Workflow permission and activation gate

pages-validation.yml runs credential-free PR tests with contents:read only and no
production environment, deployment job, signing key, pages:write or OIDC grant.
No pull_request_target/workflow_run path exists. pages-release.yml is manual-only,
main-only. Normal publication requires the independently approved repository
variable `COLLIE_PAGES_PRODUCTION_ENABLED=true`. Dispatch must name the exact
reviewed main policy commit and independently approved engineering asset ID/hash.
Preparation uses the `pages-preparation` environment with `deployment: false`,
has contents read only,
and executes the reviewed engineering policy against data assets.

For hosted proof of an approved bundle, dispatch `pages-release.yml` on reviewed
`main` with the exact reviewed policy commit and Pages bundle asset ID and SHA-256;
record the run and attempt with those identities. Deployment jobs keep pages:write/id-token:write on the
github-pages environment and use the artifact ID emitted by the pinned official
Pages uploader to create a deployment through GitHub's documented Pages API. The
local poller obtains its OIDC token from the Actions runtime and registers the
issued token with the runner's log masking before sending it to the Pages API.
Both credentialed clients reject redirects, and request errors omit response
bodies and credential values. Initial API/OIDC endpoints remain trusted runner
configuration; GitHub validates the OIDC claims and artifact ownership.

Only an observed `succeed` before the 30-minute polling deadline permits the
deployment step to succeed. The five terminal failure statuses match the pinned
official action. Other statuses remain pending; HTTP, transport, protocol and
malformed-response errors consume a cumulative ten-error budget. An interruption,
exhausted error budget, or final deadline attempts cancellation of the known
deployment ID and fails the step. Both deployment commands use `exec` so the
runner's signal to the step process reaches Python directly. Signals interrupt
blocking I/O and poll sleeps immediately. They are deferred while creation results
are handed to the monitor, so a received deployment ID remains available for
cleanup. Cleanup uses a five-second socket timeout and defers all signals during
that attempt, including the first signal if cleanup began for another failure. Socket timeouts are not a hard wall-clock
guarantee, and cancellation
is best effort: an accepted cancellation request does not establish the remote
final state or rollback. A lost creation response can leave an unknown deployment;
the client never retries creation automatically or cancels by the shared build SHA.
Read-back jobs have contents:read and no protected environment credentials;
phase2 remains gated on the complete phase1 canonical read-back.

Before enabling, provision and inspect: Pages Actions source, correct custom
hostname/TLS, domain verification, github-pages required independent reviewers,
prevent self-review, branch restriction main and rulesets preventing unreviewed
workflow changes. These are required operator actions, not existing protections
proved by this PR. APK signing and repository-index signing stay operator-controlled
and separate. No production key is generated by the Pages workflow.

The per-run artifact names are fixed; consumers read only the current manual
workflow's artifacts, never an untrusted workflow_run or a PR's executable output.
Do not enable the variable until the exact final merged policy and provisioning
are reviewed. Retarget/revalidate after dependency PR merges; a moving input
invalidates approval. No protected/default branch write is part of deployment.

## Resumption, retention and operator approval package

plan.json binds both complete per-file inventories, tar hashes, candidate receipt,
repository signer, policy commit and input-approval digest. journal.json is written
atomically/fsynced outside the public site, bound to that plan, and advances only
after successful live read-back. Repeating a verified phase with the same plan is
idempotent; a different plan cannot reuse its journal. Preserve these files, input
bundle, both artifact hashes and approval *outside CI retention* in the operator's
durable release evidence archive before phase2 approval. Download the final journal
as well. Artifact retention90days is a transport convenience, not durable storage.

Use rerun-failed-jobs for an interrupted manual workflow, retaining its successful
phase and artifacts. Do not restart an already completed rollout with an older
phase1 snapshot: that would temporarily restore old active metadata. After artifact
expiry or host migration, restore the approved artifacts/plan/durable journal and
run the required read-back phase under a newly reviewed operator deployment;
never silently reset the journal, rebuild/re-sign or substitute new bytes.

Historical local Pages preparation reports are preserved at the
[`retired/release-tools` tag](https://github.com/0cwa/collie-release-engineering/tree/retired/release-tools/distribution/evidence).

Retain signed index generations and all referenced APKs through the explicitly
approved cache/client window. At the size guard, halt rather than quietly pruning.
Archive/withdrawal must be a reviewed list with canonical GitHub release references,
exact hashes and preserved historical evidence; no versioned URL may ever serve
different bytes. Defective APKs need higher versions. Key-loss/compromise, access-log
minimization, host migration and APK-vs-index-vs-HTTPS identity runbooks remain in
[OPERATIONS.md](OPERATIONS.md).

The downloader rejects existing/dangling output and partial paths before network
access, removes only its own created partial on failure, and uses exclusive
publication so a concurrently created destination is preserved.

## Native-v1 admission and initial native-only deployment

The initial plan now explicitly cohosts only the six policy-fixed native-v1 files
at their original URLs. `native-v1.json` binds exact paths/hashes/sizes; the outer
approval separately binds raw `native-manifest.json` SHA256. Feed input schema2
adds required `native_manifest_sha256` (null only when no native files have ever
been published). Schema1 remains the historical feed-only format. Once native-v1
is present, every approved bundle must carry all six unchanged files and the
manifest approval; omission, changed bytes, extra files or new generations fail.

Additional native generations now require explicit registration in
`native-generations.json`, with a checked-in manifest pinned by raw SHA-256.
The registry is initially empty. A registered generation preserves the six
native-v1 entries and specifies every additional binary and POM at immutable
versioned coordinates. Supply that exact registered manifest and full file set
through the existing approval/readback flow. Old native-v1 bundles remain valid
for feed updates and retain all already published generations. See
[NATIVE-STORAGE.md](NATIVE-STORAGE.md#admitting-a-new-immutable-native-generation)
for the format and admission rules. Initial empty-site recovery remains native-v1
only; registration does not itself authorize publication.

For deployment before any production repository-index key exists, approval schema1
kind `collie-pages-native-input` has only: origin, policy_revision,
previous_site_sha256, previous_manifest_sha256, native_manifest_sha256,
maximum_bytes, schema and kind. It needs no candidate repo, release approval,
repository receipt, key or landing. Both snapshots add the six immutable files
and preserve the complete prior site. On an empty site both show preparation
text without an active feed/add link. The same workflow/reviewer/readback gates
apply. Initial native URL conflicts fail preflight. A later separately signed
feed bundle retains native-v1 forever; all bytes count toward950MB. No production
settings, uploads, live feed, index key or new source build are authorized here.

New native URLs are checked absent-or-identical even when an existing feed
predates native hosting; complete prior native inventories are verified as part
of the retained site. The native-only placeholder names native inputs and the
inactive feed without claiming signed indexes. Ingestion also keeps the externally
approved APK digest pinned after signer/metadata inspection, rejecting mutation
before the target URL is staged.
