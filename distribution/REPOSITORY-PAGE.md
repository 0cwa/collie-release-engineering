# Repository page preparation

This feature is separate from the reviewed hosting/native preparation. Merging
preparation code does **not** publish F-Droid, native inputs, a website or a badge.
The owner confirms DNS is not configured; no production deployment/activation or
key was created. The later collie.im website branch is a separate task.

The page uses the unchanged approved Collie icon, the owner's golden/orange/bark
palette and self-hosted Manrope body350/400 plus Sora headings300. Molly inspired
simplicity only; Matrix was a light detail reference, not the page theme. There
is no project pitch, new badge design or external font request. ASSETS.json pins
font binaries/licenses to an official google/fonts commit and hashes the icon,
CSS and unmodified official F-Droid badge. The renderer fails changed asset pins.

Generate a private preview (it grants no publication authority):

```
python distribution/site_page.py NEW_OUTPUT --state preview \
  --repo-url https://repo.collie.im/fdroid/repo/ \
  --repository-fingerprint VERIFIED_TEST_FINGERPRINT \
  --download https://github.com/0cwa/collieim-android/releases/download/v8.35.1-1-rc1/collie.apk
```

Default `inactive` has no add-link or QR. `native` names native preparation and
the inactive feed. A preview explicitly says it is not published. Active output
is generated only by the separately checked first-link activation adapter.
The primary scheme is fingerprint-pinned `fdroidrepos://` (HTTPS), registered by
F-Droid1.23.2 and Droidify0.7.8 manifests. A selectable HTTPS address and official
fdroid.link universal fallback are available; the primary link needs no redirect.
Source inspection is not a new runtime client acceptance claim.

The QR is an opaque RGB black/white PNG, four-module quiet zone, on a white island
with only-light color scheme and forced-color adjustment disabled. Actual
headless tests use dark media emulation and forced auto-dark, plus a control page
that visibly inverts, then independently decode QR pixels from full screenshots.
Arbitrary browser extensions are not guaranteed. The cached browser is a local
TEST runtime, never an application build input or production endpoint check.

## First live add-link and later updates

1. Provision domain/HTTPS/production index key/protected Pages approvals only with
   separate operator authorization. Prepare and manually deploy the ordinary
   two-phase feed with an INACTIVE landing. Canonical indexes/APK may be tested by
   explicitly entering the repository URL without advertising an active button.
2. Complete the real canonical HTTPS phase2 readback. Run official F-Droid and
   Droidify add/refresh/install/update on isolated test data against that actual
   origin and production repository certificate. Preserve reports beyond CI
   retention. Test-key/staging/local/preview evidence cannot activate links.
3. Approve a data-only `collie-pages-link-activation` bundle via the existing
   manual workflow's explicit engineering asset ID and complete raw archive SHA.
   `preparation_mode=activate-links` checks the approved completed feed plan,
   journal, repository receipt, production certificate and both client reports,
   plus the full authoritative previous published inventory. It verifies signed
   v1/v2 indexes again. Production labels are operator assertions bound by the
   externally approved bundle; these JSON records are not signed attestations or
   independent reproductions. The shared repository receipt's historical NOT RUN
   observations remain historical; new canonical/client evidence is separate.
4. The adapter creates one link-only snapshot, changing only index.html, QR and
   public inventory. Every existing APK, native object, index and static asset
   hash must stay identical. Existing capacity rules still apply. The preparation
   job reads the OLD canonical snapshot before the protected one-snapshot deploy;
   final canonical readback uses the existing plan-bound journal. No APK rebuild,
   re-signing or key change occurs. CDN requests are not globally atomic; hash or
   signature mismatches fail closed and retry the same approved bytes.
5. Preserve completed link-activation.json plus plan/journal/reports outside CI
   retention. Only then prepare the inert application README badge patch using
   readme_badge.py and its externally approved raw record hash. The unchanged
   official badge goes immediately after Obtainium at the same height80 and links
   to OUR custom repository landing, never an invented official catalogue entry.
   Apply it later through an ordinary reviewed app PR/publication layer. Current
   README/Matrix/donation links are not edited by this feature.

Already-active feeds may retain their active landing while normal two-phase
index updates roll out. Ordinary prepare refuses first-link activation, preview
input and accidental removal of an existing active link. Source/native receipts,
APK identity and optional FCM behavior are unchanged. A defective APK still needs
a higher-version release; link metadata never authorizes replacing old binaries.

## Activation bundle contract

`approval.json` has exact schema1 fields: kind=collie-pages-link-activation,
environment=production, origin=https://repo.collie.im/, policy_revision (current
trusted main), feed_policy_revision (the exact prior policy), feed_plan_sha256,
feed_journal_sha256, repository_receipt_sha256, repository_certificate_sha256,
client_acceptance_sha256, previous_site_sha256, previous_manifest_sha256,
maximum_bytes<=950000000 and explicit versioned GitHub collie.apk download_url.
Plan/journal/client/previous inventory digests are canonical JSON SHA256; the
previous public manifest uses its RAW file SHA256; shared receipt digest uses its
existing canonical helper. The complete transport archive has an independently
approved raw SHA256. Inputs are feed-plan.json, feed-journal.json,
repository-receipt.json, client-acceptance.json and previous/ complete site.

Client report exact schema1 fields are environment=production, origin,
repository_certificate_sha256, apk_sha256 and clients. clients has exactly
org.fdroid.fdroid and com.looker.droidify; each has version, durable text
evidence_sha256, and tests add/refresh/install/update all PASS. Unknown or missing
evidence is rejected. Tests use explicitly synthetic metadata and mocked index
signatures; they do not claim a production run. No credential is present in input
bundles, public artifacts or these reports.

Official references:
- https://f-droid.org/docs/Badges/ (CC-BY-SA3.0; guidance prefers QR landing for custom repos)
- https://gitlab.com/fdroid/fdroidclient/-/blob/1.23.2/app/src/main/AndroidManifest.xml
- https://github.com/Droid-ify/client/blob/v0.7.8/app/src/main/AndroidManifest.xml
- https://fdroid.link/ and its official repository-link normalization code
- https://github.com/google/fonts/tree/b31870aff700ab7a1d74fa0c6887d95beb9e0037/ofl
