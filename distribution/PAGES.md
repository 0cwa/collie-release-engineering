# GitHub Pages distribution and two-phase promotion

For an existing production site without `site-manifest.json`, use the explicit
[inventory-only bootstrap](INVENTORY-BOOTSTRAP.md). Normal Stable publication,
including `force=true`, continues to require a verified existing inventory.

The Stable feed is live at [repo.collie.im](https://repo.collie.im/) and
[`/fdroid/repo/`](https://repo.collie.im/fdroid/repo/), maintained by the scheduled
[`feeds.yml`](../.github/workflows/feeds.yml) workflow in release engineering.
Native inputs share this site and its storage budget; see
[NATIVE-STORAGE.md](NATIVE-STORAGE.md). Keep one canonical unchanged signed APK
per selected version. The old R2 feed planner is historical. A later collie.im
website on an application website branch remains a separate work item; it neither
blocks this feed nor belongs in Android main.

The two-phase [`pages-release.yml`](../.github/workflows/pages-release.yml) route
for approved native/receipt-based promotions remains subject to the exact gates
below. TrailBlazer Beta has a separate candidate/readiness/Pages approval path
and is not live as of 2026-11-17. A successful scheduled Stable publication does
not establish acceptance or approval for a new two-phase wrapper or Beta release.
See the [dual-release policy](../docs/DUAL-RELEASE-POLICY.md) for the product and
source boundaries. For an existing Stable site without a canonical inventory,
see the separately reviewed [inventory bootstrap change (#61)](https://github.com/0cwa/collie-release-engineering/pull/61);
use that procedure only after its implementation is available at the approved
engineering revision.

## Historical infrastructure checks and tool scope

Initial preparation recorded read-only Pages API responses of 404 for the app and
engineering repositories and an empty engineering environments list. Those are
historical observations, not evidence of current site availability, provisioning
permissions or environment protections. Reinspect the current settings when
approving a two-phase activation. The preparation and loopback records later in
this document describe their exact candidate revisions; their pending public
acceptance results do not describe the live scheduled Stable feed.

GitHub's published site limit is1GB and monthly soft transfer limit100GB. Packaging
uses a conservative950,000,000byte ceiling for *each full site and its artifact*,
including every retained APK, index archive, image, receipt and inventory. Nine
RC1-sized APKs alone are873,489,069bytes; actual measured totals decide admission.
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

### TrailBlazer Beta readiness artifact

Beta Pages input is schema 2 and selects only `trailblazer-beta`. Its approved
GitHub Actions run must belong to `0cwa/collieim-android`, use the exact
`trailblazer-beta` branch and `.github/workflows/beta-readiness.yml` workflow,
finish successfully, and point at the same immutable source commit named by the
release receipts. `prepare` reads the run and artifact records from GitHub and
downloads the approved artifact bytes. It checks the downloaded archive SHA256
against both the API digest and `approval.json`; API metadata alone is not proof.

The ZIP must contain exactly one regular file, `trailblazer-beta-readiness.json`,
with no extra files or directories. The JSON schema is version 1 and has exactly
these keys and types:

```json
{
  "schema": 1,
  "kind": "collie-trailblazer-readiness",
  "repository": "0cwa/collieim-android",
  "branch": "trailblazer-beta",
  "workflow_path": ".github/workflows/beta-readiness.yml",
  "run_id": 123,
  "run_attempt": 1,
  "head_sha": "<40 lowercase hex characters>",
  "package": "im.collie.app.trailblazer",
  "version_name": "<validated APK versionName>",
  "version_code": 123,
  "tag": "trailblazer-v<versionName>",
  "title": "Collie TrailBlazer Beta <versionName>",
  "apk_asset": "trailblazer-beta.apk",
  "source_receipt_sha256": "<64 lowercase hex characters>",
  "build_receipt_sha256": "<64 lowercase hex characters>",
  "release_receipt_sha256": "<64 lowercase hex characters>",
  "repository_receipt_sha256": "<64 lowercase hex characters>",
  "release_manifest_sha256": "<64 lowercase hex characters>",
  "apk_sha256": "<64 lowercase hex characters>",
  "apk_certificate_sha256": "<64 lowercase hex characters>"
}
```

The Android workflow must generate this JSON from the validated release inputs
and upload it as the single member of the exact readiness artifact. This
engineering checkout does not contain the app-side generator; the existence
and implementation of the named Android workflow must be verified in its
repository before an approved run is accepted. The current consumer/verifier
is the existing Pages command, with no separate readiness command:

```sh
python distribution/pages.py prepare <bundle-dir> <new-output-dir> \
  --policy <reviewed-40-hex-engineering-revision> --channel trailblazer-beta
```

Before it accepts the readiness manifest, the verifier runs the full selected
Beta source/build/release/repository receipt graph, compares every manifest
receipt hash and release identity against those documents, and compares the APK
hash against the candidate APK bytes. Beta receipt validation accepts only the
Beta package, `trailblazer-beta.apk` alias, and provisioned Beta APK certificate;
the default migration-receipt profile remains stable. Promotion also verifies
the prior signed Beta index and requires the candidate versionCode to exceed its
maximum.

### Beta repository evidence boundary

The repository receipt used to prepare a Beta candidate is explicitly
prepublication evidence. Produce it only after the signed local F-Droid indexes
and APK have passed the pinned verifier, with the Beta-only stage argument:

```sh
python distribution/receipt.py <signed-beta-index-dir> \
  --approval <release-approval.json> \
  --receipt-bundle <source-build-release-receipts-dir> \
  --metadata-revision <40-hex-metadata-commit> \
  --fingerprint <beta-index-certificate-sha256> \
  --generation <approved-generation-sha256> \
  --channel trailblazer-beta --repository-stage prepublication \
  --client-evidence <completed-isolated-client-evidence-file>
```

The receipt records local index verification as `PASS`, isolated client
add/refresh/install/upgrade acceptance as `PASS` only when its evidence file is
provided, and production HTTPS/provider read-back as `NOT RUN` with no digest.
Pages preparation requires the completed client gate and rejects a missing
client result. It accepts the explicitly pending production read-back because
that observation cannot exist before deployment; source, build and release
receipt evidence must still all pass. The shared verifier exposes this narrow
boundary as `--promotion --channel trailblazer-beta --repository-stage
prepublication` (or its equivalent API arguments). Ordinary promotion
verification and stable defaults retain their all-evidence-passed behavior.

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
Beta requires schema 2 and adds exact fields `channel` (trailblazer-beta),
`source_branch`, `source_commit`, `readiness_workflow`, `readiness_run_id`,
`readiness_attempt`, `readiness_artifact_id`, `readiness_artifact_sha256`, and
`readiness_commit`; the source and readiness commits must match. Beta release
approvals and all four receipt documents must be schema 2. Stable schema 1 and
schema 2 remain supported with their existing identity rules.
The signing/publication receipt's raw final-manifest and canonical release-receipt
links remain distinct; legacy RC1 has null canonical source link, never fake
new-source evidence. New-v2 approvals require the approved source/build/release
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
main-only and disabled until the independently approved repository variable
COLLIE_PAGES_PRODUCTION_ENABLED is explicitly set. Dispatch must name the exact
reviewed main policy commit and independently approved public data asset ID/hash.
The preparation job has no deployment/index-signing credentials and does not
execute candidate code. Deployment jobs keep pages:write/id-token:write on the
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

The concrete local test package's plan/tar hashes, size, fingerprint and executed
checks are recorded under distribution/evidence/PAGES-PREPARATION.json. It uses a
disposable index test key and a STAGING landing, not a production deployment
approval. Actual APK bytes/signer are the existing verified RC1. Production needs
its separately approved real repository certificate, newly frozen package and
read-back against the actual endpoint before advertising add instructions.

Retain signed index generations and all referenced APKs through the explicitly
approved cache/client window. At the size guard, halt rather than quietly pruning.
Archive/withdrawal must be a reviewed list with canonical GitHub release references,
exact hashes and preserved historical evidence; no versioned URL may ever serve
different bytes. Defective APKs need higher versions. Key-loss/compromise, access-log
minimization, host migration and APK-vs-index-vs-HTTPS identity runbooks remain in
OPERATIONS.md. No production hosting, domain, key, native input or APK changed here.

## Executed local package evidence

Implementation policy commit3da1b0946c6a5b8e76eb0eb43b135cf2e4a661e2 produced
real RC1-based, disposable-index-key artifacts. Phase1 tar97,116,160bytes SHA256
78a4eb370dd5d595d41ab71046a9cbe3f67f144e3ed4c7c402db7376d2c4ed24;
phase2 tar97,187,840bytes SHA256
9b242a538497afa577b0ba641700056241c482c7047c07d13efb682ee00e9450.
Input tar97,146,880bytes SHA256
a5e4c1290c1eeb77a1e55b7ff8dd221b9721c925485f3c093a741b0ab3f3e043.
These are concrete tested staging objects, not a request to deploy this test key.
Both snapshots were served/read back fully over loopback HTTP with all expected
hashes, and phase1 retry retained the same journal. No public HTTPS/Pages/CDN check
or deployment occurred. The test journal's injected loopback transport is labeled;
it is not a production readback attestation. Private test key and bulky artifacts
are removed after independent inspection; only text hashes/receipts persist.

Forty-three standalone tests pass, including explicit repository/generation/previous-site
substitution rejection, current-live manifest mismatch, every previous URL
verification, cache-failure retry, mixed generation failure, byte-identical output
from the same approved bundle in different directories, safe extraction, restricted
public paths and manual-only workflow/action pins. Actual initial Pages activation
and endpoint client acceptance remain NOT RUN.

The downloader rejects existing/dangling output and partial paths before network
access, removes only its own created partial on failure, and uses exclusive
publication so a concurrently created destination is preserved. Regression tests
cover both preexisting and raced paths; the actual Pages artifact bytes above are
unchanged by this download-helper correction.

## Native-v1 admission and initial native-only deployment

The initial plan now explicitly cohosts only the six policy-fixed native-v1 files
at their original URLs. `native-v1.json` binds exact paths/hashes/sizes; the outer
approval separately binds raw `native-manifest.json` SHA256. Feed input schema2
adds required `native_manifest_sha256` (null only when no native files have ever
been published). Schema1 remains the historical feed-only format. Once native-v1
is present, every approved bundle must carry all six unchanged files and the
manifest approval; omission, changed bytes, extra files or new generations fail.

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

Native-only actual package acceptance: policy001c2c93b94baf9946512e4acd184e235c0b8a97,
manifest SHA256637e69803bdec6aa117c9f3b85d966ef055041a2c066e4cb28447ecd29296838.
Both tar artifacts are310,855,680bytes SHA256
38aa1feae9b6aad9e6690f9b25f4e610e34752b51ef8dd1460fc366e186fa5d4;
the actual six cached inputs total310,836,447bytes and each complete site is
310,838,280bytes. The input archive was extracted with the production data-only
parser; both snapshots contain exactly the six native files, preparation page
and public inventory, with no APK/index/add link/key. Actual loopback HTTP full
readback and retry passed. Fifty-one distribution tests pass. Evidence is
NATIVE-PAGES-PREPARATION.json plus native-pages-plan/journal under evidence/.
Canonical public HTTPS/Pages/CDN and independent native reproduction remain
NOT RUN. These preparation hashes authorize no production operation; freeze and
approve the final merged policy/bundle before any operator deployment.

New native URLs are checked absent-or-identical even when an existing feed
predates native hosting; complete prior native inventories are verified as part
of the retained site. The native-only placeholder names native inputs and the
inactive feed without claiming signed indexes. Ingestion also keeps the externally
approved APK digest pinned after signer/metadata inspection, rejecting mutation
before the target URL is staged. Both integrity regressions pass.

An independent agent audited code390443afeaf7bad8c0aee58fa37cf6ced2d6c95a,
ran19focused Pages/ingestion checks, hashed both real archives and every member,
and matched all six native inputs to the existing source plan. The native-only
page was also rendered and visually checked in an isolated cached headless TEST
runtime without networking: native inputs named, feed inactive, no add link or
signed-index claim. Unsigned observations and raw/canonical receipt references
are preserved in native-independent-verification.json and
native-maintenance-headless.json. The cached browser is not an application build
dependency or public hosting proof. Existing Android testing JNI inside the
accepted AAR remains unchanged; absence of test JNI was checked separately on
the production APK, not asserted for these native inputs. No extra host-JVM
artifact or private payload is included. Large generated package copies were
removed after review; exact native policy inputs and documented commands recreate
the package for a newly approved final policy. Independent native reproduction
and canonical public HTTPS/Pages/CDN acceptance remain NOT RUN.
