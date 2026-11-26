# Collie binary repository

GitHub Pages hosts the feed and native inputs. See [PAGES.md](PAGES.md) for
publication and [NATIVE-STORAGE.md](NATIVE-STORAGE.md) for native storage.

This is a custom binary feed, not an application build or official F-Droid catalogue
submission. One `im.collie.app_<versionCode>.apk` is indexed for each approved release.
The GitHub filenames containing `foss` do not select another product. Do not run
`fdroid publish`, zipalign, APK signing or APK rewriting in this route.

## Inputs and verification

Install the hash-pinned `requirements.txt` in a fresh Python 3.12 environment using
`uv pip sync --require-hashes requirements.txt`. The selected official fdroidserver
is 2.4.5. Its `update` command produces standard v2 `entry.jar` + `index-v2.json`
and compatibility `index-v1.jar`; the official library verifies both signatures.
`index-v1.jar` uses the library's legacy algorithm verifier, as required by that
format. Do not enable legacy verification for v2.

For a receipt-based Pages promotion, the operator approves the release ID, tag,
raw release manifest SHA256, APK SHA256, package, versionCode/name and signer
SHA256. The approval parser rejects unknown or duplicate fields and unequal
aliases. The Stable APK certificate is pinned in the metadata and approval
parser. See [PAGES.md](PAGES.md#trusted-approved-input-bundle) for the bundle
format.

Metadata declares optional FCM facts without asserting that client code is closed
source. Additional anti-features require inspecting the applicable client/service
implementation and fdroidserver taxonomy; no unsupported classification is added.

## Repository index key

Create the production repository index key only after approval. Keep it separate
from the existing APK identity and HTTPS certificate. Generate offline using
`fdroid init` on an operator-controlled signer; store the keystore/passwords
outside source and origin, with two encrypted offline backups in separate places.
Test recovery in an isolated directory before publishing the public certificate.
Record the real DER certificate SHA256 and pin the add link/QR to that value.
Publish that fingerprint in the application repository only after approval.
The operator reviews the production landing after endpoint read-back. Never
advertise a test key.

## Publication and recovery

1. Freeze approval, generation manifest, raw release-manifest digest, metadata
   commit, fdroidserver pin, exact APK digest and repository certificate.
2. Validate indexes and all referenced APKs. Serialize signing and deployment;
   local `publish-local` uses a file lock, and Pages uses the deployment controls
   in [PAGES.md](PAGES.md). No PR code receives signing or deployment credentials.
3. Upload immutable APKs/assets before mutable indexes; entry.jar is last. Never
   overwrite a versioned APK URL. Conflicting existing digest stops publication.
   Keep old generation files and immutable diff payloads while clients may refer
   to them. The local promotion journal is durable across process restarts; keep
   it outside the public origin. On retry resume the same approved generation.
4. Mutable indexes/entry: `no-cache,max-age=0,must-revalidate`; versioned APKs:
   `public,max-age=31536000,immutable`. CDN 404 responses: `no-store`, negative TTL
   zero. Purge any old cached 404 before exposing references. Configure these
   rules at the actual origin/CDN and verify headers; scripts cannot enforce a
   provider's negative-cache settings.
5. Multiple HTTP requests are not atomic. A client fetching old entry and new
   index must reject the SHA256 mismatch and retry refresh. APK URLs never change
   bytes, so an old index remains safe. Do not weaken signature/hash validation.
6. Read back all public bytes and verify v1/v2 repository signatures with pinned
   fingerprint; compare the APK digest and approved manifest. Write schema-2
   repository receipt with metadata commit, index
   digests and actual test evidence. Preserve receipts beyond CI retention.
7. Client acceptance: add fingerprint-pinned URL, refresh, install in disposable
   device, then upgrade from supported earlier APK. Official F-Droid and one
   maintained compatible client are required. Record exact client/device/APK
   identities. Library verification alone does not establish device acceptance.

Before promotion is approved, verify the landing page, canonical download and
source/verification links at the actual endpoint; do not claim endpoint live.
On interruption preserve generation and journal. Resume exact approved bytes,
never rebuild. If mutable indexes cross generations, refresh restores consistency;
keep old signed generation snapshots outside public entrypoints for recovery.
A rollback may restore approved metadata, never overwrite old APKs or claim a
lower app version is an update. Defective apps require a higher versionCode.

Archive selected old versions with original signed bytes and receipts. Withdrawal
may remove a version from the active signed index after review but retain immutable
APK/evidence in archive according to the published retention policy. Key loss:
restore tested offline backup; without backup a new repository identity requires
explicit user re-addition. Compromise: stop signing, preserve audit evidence,
announce through existing trusted channels and application repository, provision
new repository identity after approval; never rotate APK key as a repository fix.
Host migration preserves URLs/certificate and verifies mirrors before DNS switch.
Disable or minimize access logs (no IP/user-agent retention unless required), keep
operational counters, monitor TLS expiry, index age/signatures and immutable hashes.

## Retention policy on the Pages site

The Pages site has a 950,000,000-byte ceiling (`SITE_LIMIT`). A Stable or Beta APK is
about 96 MB, so the site cannot keep every old APK. Each whole-site update (`feed.py`,
`retained_artifacts.py`) keeps:

- Per channel, the APKs in the newly signed index plus the newest
  `RETAINED_PREVIOUS_APKS` (1) older APK URL. A client holding the cached index of the
  previous release can still download it. A client whose cached index is older than that
  gets a 404, refreshes its index (F-Droid does this on every update check) and takes the
  current APK. Older versions stay downloadable from the GitHub release pages. APK URLs
  never change bytes, and an APK is never replaced.
- A preserved Beta route (a Stable-only update) is copied byte for byte, so its signed
  index still names every APK it holds; the previous-APK limit applies when the Beta route
  is rebuilt for a new Beta.
- Native Maven files only while a current release plan names them: the plan of the release
  being published and the plan of the served Beta (`--keep-native-plan`, exported from the
  Beta tag by `beta_inputs.py native-plan`). A native generation no current release uses is
  dropped from the site, which means that old tags can no longer be rebuilt from repo.collie.im
  alone; the native inputs are reproducible from their sources.
- Signed-index and receipt archives (`_generations/`), diff files, versioned icons and listing
  artwork, which are small.

With two APKs per channel and the native files of the current release the site settles near
700 MB, leaving room for several more releases of each channel. Raising
`RETAINED_PREVIOUS_APKS` by one costs about 192 MB (one APK in each channel); check the
headroom before doing that. Retirement happens on the next whole-site update after a
change, so a forced `feeds.yml` run applies a changed policy to the same releases.

## Official references

- https://f-droid.org/en/docs/Setup_an_F-Droid_App_Repo/
- https://f-droid.org/en/docs/Build_Metadata_Reference/
- https://f-droid.org/en/docs/Signing_Process/
- https://f-droid.org/en/docs/Reproducible_Builds/
- https://fdroid.gitlab.io/fdroidserver/fdroidserver.index.html

## Approval binding at promotion

Use a separately reviewed trusted verifier checkout to inspect the canonical APK,
its signature/package/version and the exact selected release approvals before any
signing/deployment credentials are introduced. A candidate cannot replace this
policy/verifier. The operator records the full public generation digest computed
by `python distribution/binary_repo.py generation-digest <repo>` alongside the
selected approval file hashes and repository certificate. The digest covers the
complete allowed public file set, not selected source directories. This manual
approval is external to the candidate. Do not compute and auto-approve it inside
a privileged candidate job.

`publish-local` requires `--approved-generation <sha256>` and one
`--approval <release-approval.json>` for each selected version. A valid repository
signature alone is insufficient. Every indexed release must equal an explicit
approved package/version/name/hash/certificate tuple, v1 and v2 must agree, and
unexpected public paths (including disguised private JSON) are rejected before
publication commands. Receipt generation recomputes the digest and rejects a
supplied mismatch. Approval becomes invalid when any public file, release approval
or repository signer changes.

The trusted local promoter freezes each approved public file hash before copying,
checks temporary bytes against that hash before exposing a file, checks read-back
against the same pinned hash and verifies final signed indexes before completing
the journal. It never compares against a possibly changed candidate source as the
approval authority. Standard fdroid `status/` outputs contain nonsecret local tool
paths/command metadata; include them only after reviewing that privacy exposure.

## Raw historical manifests and new receipt graphs

The existing strict approval schema 1 is a **legacy** binary import. Its
`release_manifest_sha256` always means SHA256 of raw final-manifest file bytes.
A legacy repository receipt carries `release_receipt_sha256: null`; it can describe
verified historical APK bytes but cannot pass complete new-source graph promotion.
Historical source/build evidence is UNKNOWN, never manufactured.

Approval schema 2 retains the raw manifest digest and adds required
`release_receipt_sha256` (canonical shared schema-2 release receipt digest) plus
`receipt_approvals`, an exact source/build/release canonical digest map approved
outside the candidate. Pass `--receipt-bundle <directory>` containing source.json,
build.json and release.json to ingestion and receipt generation. The shared
`migration_receipts.verify_release_inputs` checks external digest approvals,
source/build/release links, raw manifest digest and completed evidence before
schema-2 ingestion; package/version/APK/signer must also match the approval. The
repository receipt carries both distinct digest fields. Missing bundles, changed
raw/canonical identities and UNKNOWN evidence fail; legacy cannot supply a fake
bundle. `fixtures/v2-release-input.json` is synthetic test input, never release
evidence or production signer approval.

`receipt.py --client-evidence <recorded-json>` records the operator's client
evidence digest. The digest binds the claim to the recorded file.
