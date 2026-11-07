# GitHub Pages binary-feed rollout — prepared, not activated

GitHub Pages in collie-release-engineering is the selected host for
https://repo.collie.im/ and /fdroid/repo/. Keep one canonical unchanged signed APK
per selected version. The old R2 feed planner is inactive/historical. Native Maven
inputs have a separate proposed origin requiring operator approval; see
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
The official upload-pages-artifact@v3 composite invokes a floating nested
upload-artifact@v4, so this adapter creates the equivalent artifact.tar and directly
pins upload-artifact@v4. The archive contains only regular files, with deterministic
order/mode/uid/gid/mtime, no symlinks/hardlinks and verified per-file bytes. Hosted
Pages deployment compatibility is NOT RUN until separately approved activation;
local tar structure/digests and official format requirements were checked.

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
execute candidate code. Deployment jobs execute only the pinned official deploy
Pages action with pages:write/id-token:write, through github-pages; read-back jobs
have contents:read and no protected environment credentials.

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
