# Actual existing-release client acceptance — 2026-11-07

PASS: official F-Droid 1.23.2 and maintained Droid-ify 0.7.8 each added the
fingerprint-pinned local binary repository, refreshed it, freshly installed RC1
and upgraded the actual production-signed RC3 to RC1. These were already published
APKs, not a newly built source candidate. Exact machine-readable evidence is
`evidence/device-acceptance.json`, SHA256
`9e73478dfc8d57c463b30636aa4d8cafb2298576b6343aaea765bc4eb78c7f3b`.

Device: disposable API35 x86_64 Google APIs image revision9, fingerprint
`google/sdk_gphone64_x86_64/emu64xa:15/AE3A.240806.043/12960925:userdebug/dev-keys`;
Android emulator37.2.13 build16740729. Started through agent-heavy with
`-no-window -no-audio -no-snapshot -gpu swiftshader_indirect -memory4096 -cores4`,
4GB sparse userdata, explicit emulator5580 / dedicated adb5038. No personal device
or account was used. The cached system image was reused. Public emulator egress
was blocked; local feed used adb reverse. No browser or desktop interaction.

The canonical RC3 APK absent from cache was fetched from its exact official release
URL and independently hashed/signer-checked again. Two small client APKs absent
from cache were fetched from pinned official URLs. Their hashes, versions,
certificates and origins are recorded in the JSON; no APKs/keys are in Git.

- Older production APK: v8.33.2-1-rc3, versionCode177203,
  SHA256 `189b0bf0056945bd6dde9a9e9f5a9e664f8aa5e294de3ade77d2850ce7d3a313`.
- Newer existing APK: v8.35.1-1-rc1, versionCode177702,
  SHA256 `5fbd0fe817bc6711c56fbc39b8479cc0a68a7d7fe921a7d9c9fab59333fda9ee`.
- Both use package im.collie.app and existing production signer
  `f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826`.

F-Droid fetched entry.jar/index-v2.json; Droid-ify fetched index-v1.jar. Both showed
Collie metadata/version and performed normal Android install/update confirmation,
including per-client unknown-source permission on this disposable device. Actual
installed base.apk SHA256 was measured, matching the canonical RC1 digest. Upgrade
firstInstallTime was preserved for both clients; update times changed. The final
installerPackageName was com.looker.droidify. RC3 and RC1 launched successfully;
the observed app screen was database-encryption onboarding. No account, messages,
attachments or preferences were created or altered for broader scenario testing.

Test-only repository certificate:
`94f58c117091566ec04d8b36c16cbd8cbfb37c32b4d4c1718918f9ecfb4ebe16`;
public generation
`8701124e4480f5f584d86f271fcc95d00ae33b5e18ebbc8fe7cac5874a0f9062`.
The schema2 repository receipt explicitly retains legacy
`release_receipt_sha256:null`; actual binary/client checks do not manufacture a
new source/build receipt graph or independent native reproduction.

Recreation: run `stage_rc1.py` from OPERATIONS.md with the approved existing
release bytes; run `serve_staging.py` with its actual fingerprint, computed
externally approved generation and legacy approval. Use adb reverse tcp8765,
open each client's fingerprint-pinned fdroidrepo URL (Droid-ify's default HTTPS
rewrite must be changed to HTTP for this loopback-only test), confirm addition and
refresh, then install and update through each client. Before each upgrade install
the approved RC3 on a clean disposable device and record firstInstallTime; after
upgrade record version, update time, installer and on-device base.apk SHA256.

PASS: 23 tests total: 18 binary/HTTP tests plus5 shared receipt-chain adapter tests. New HTTP tests
cover immutable APK cache headers, no-store negative response followed by upload
and successful read-back, mutable-entry freshness, private/traversal rejection and
same-second entry replacement with an old If-Modified-Since header returning200
current bytes. Independent review found and prompted the last conditional-cache
fix. These tests model local HTTP behavior; real CDN/provider behavior remains
NOT RUN. Signed generation mismatch rejection and local interrupted-publication
recovery are covered by the existing signed-index and publication checks.

NOT RUN: account/attachment/locked-database/physical-device scenarios; a new-source
production-signed candidate upgrade; rendered landing acceptance (headless binaries
failed allocator startup); production HTTPS/CDN/provider/key-recovery validation.
No production repository identity, DNS, TLS, cloud policy or APK signing key changed.

Cleanup: task emulator/dedicated adb server and loopback server stopped. Disposable
AVD disks, client/RC3 downloads, staging APKs/indexes/private test key/config and
local dependency environment removed. Preexisting APK caches and SDK image retained.
