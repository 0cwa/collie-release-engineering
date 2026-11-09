# GitHub Pages binary-feed rollout — prepared, not activated

GitHub Pages in collie-release-engineering is the selected host for
https://repo.collie.im/ and /fdroid/repo/. Keep one canonical unchanged signed APK
per selected version. The old R2 feed planner is inactive/historical. The existing six-file native-v1 generation shares this Pages budget by explicit
initial decision; see
NATIVE-STORAGE.md. A later collie.im website on an application website branch is a
separate work item; it neither blocks this feed nor belongs in Android main.

## Checked infrastructure and tools

Read-only GET /repos/0cwa/{collie-release-engineering,collieim-android}/pages returned
404. That is not proof that credentials can provision Pages or that no unseen
configuration exists. Engineering environments API returned an empty list; no
required reviewer/ruleset is claimed present. No Pages/DNS/TLS/environment setting
was changed. The repository is not advertised live.

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
The production workflow uses the official upload-pages-artifact composite at an
immutable commit, with `include-hidden-files: true` to preserve any approved hidden
paths if present. The approved native-only inventory has 15 paths and does not
include `.nojekyll`. Its nested upload-artifact action is also pinned in that composite.
Local policy preparation still audits each exact site manifest and archive; the
wrapper differential changes only artifact construction and retains the same
per-file payloads. Hosted Pages deployment compatibility remains NOT RUN until a
separately approved activation.

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

The complete archive SHA256 and explicit engineering release-asset ID are approved
outside the candidate. No latest scraping, arbitrary remote URL, signing key,
configuration/password, source injection or script execution. The downloader uses
the fixed engineering repository API, checks asset ID/repository/size/hash, and
never sends a token to the public download redirect. Extraction rejects duplicate,
traversal, link and special members and caps size/file count. Candidate data never
replaces the checked-out trusted verifier.

approval.json has exact fields: schema1, kind collie-pages-input,
origin https://repo.collie.im/, policy_revision (reviewed Git commit),
previous_site_sha256 (canonical inventory digest of complete previous snapshot),
previous_manifest_sha256 (raw published site-manifest.json SHA256; explicit null
only for first empty origin), candidate_generation_sha256 (existing fdroid repo
inventory digest), repository_certificate_sha256, repository_receipt_sha256
(canonical shared schema2 receipt digest), release_approvals (JSON basenames),
landing_sha256 (canonical landing inventory digest), maximum_bytes<=950000000.
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
   and records observed Cache-Control/ETag/Last-Modified/Age. Cached404, mixed bytes
   or interrupted requests do not advance the journal. Retry the same prepared
   plan; do not rebuild or re-sign the APK.
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
local poller obtains its OIDC token from the Actions runtime, polls until success,
a terminal failure, interruption, or its 30-minute deadline, and cancels pending
deployments on interruption, repeated API errors, or that final deadline. It never
prints either token. Read-back jobs have contents:read and no protected environment
credentials; phase2 remains gated on the complete phase1 canonical read-back.

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
