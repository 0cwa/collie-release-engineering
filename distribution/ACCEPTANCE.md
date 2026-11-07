# Executed staging acceptance, 2026-11-07

Input application baseline: `f6f68cdee08fb8d9acb6a866b1673deb10525f20`.
Explicit existing GitHub release ID `437221260`, tag `v8.35.1-1-rc1`, peeled source
`698902e405975bbd87789a2e376f80b564031738`; this route does not claim a new build.

PASS: Actual downloaded canonical APK ingested unchanged, SHA256
`5fbd0fe817bc6711c56fbc39b8479cc0a68a7d7fe921a7d9c9fab59333fda9ee`.
APK package `im.collie.app`, versionCode `177702`, versionName `8.35.1-1-rc1`,
certificate SHA256 `f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826`.
Raw final-manifest SHA256
`4adb60596a2d9c96b19b39e74f98deebd46902a763e873bebae53699c0fd8b11`.

PASS: `stage_rc1.py` with official fdroidserver 2.4.5 generated and verified v2
and v1 signed indexes using disposable repository certificate
`10a8b224f8e4a5e5c056f80c8d558382a5cc8dab45c393603df21f8a4763b22e`.
This fingerprint is **not approved for production**. Exact first acceptance indexes:

- index-v2.json: `e89dee1279f9d4d6c3d7fd088c6e9b39ac532d886ed26e6780bfdab544d866b3`
- entry.jar: `6a5c198fff6091401715e5e99cfa95a1d3720f69dd4b254c2e4d623d02ddcfe2`
- index-v1.jar: `bc8c34af481cb3ff53488fc08629a01b196ac7628df18a16c09392a72e7467ce`

PASS: `accept_staging.py` verified actual signatures, rejected wrong repository
fingerprint and modified v2 JSON (including crossed entry/index generations), then
published/read back locally twice with identical generation
`048cfd211689218f665f62bcf7af4ab1d2fc32d23e31552c7b0c953590e40457`.

PASS: `python -m unittest discover -s distribution -p 'test_*.py'`: 8 tests,
covering immutable APK conflicts, duplicate approvals, signer verifier failure,
interrupted promotion/resume, changed-generation rejection, symlinks unknown
approval fields and R2 plan ordering/cache/preflight command generation. `python -m py_compile distribution/*.py` passed.

NOT RUN: official F-Droid and compatible Android client add/refresh/install/update;
actual production repository signing/deployment, provider cache behavior, key
recovery, HTTPS read-back and production-signed new candidate upgrade. Unit tests
exercise interruption semantics and digest mismatch; they do not prove CDN/client
network behavior.

BLOCKED: read-only `curl -I --max-time 20 https://repo.collie.im/` could not resolve
hostname. Provider and DNS ownership remain unchecked. No endpoint advertised live.

Generated indexes/APKs/test key are disposable staging artifacts kept out of Git;
these commands recreate them from the fixture and original approved bytes. Test
key, private configuration, temporary browser profiles and APK copies are removed
at handoff; only text receipts/hashes remain.

NOT RUN: rendered landing-page acceptance. The installed trivalent launcher strips
headless flags; direct binary inside agent-headless failed allocator initialization.
No desktop interaction or personal profiles were used. Generated HTML/QR only
passed static checks; no screenshot or rendered layout claim.

## Independent review corrections

Three valid findings fixed: promotion now requires externally approved complete
public generation SHA256 plus explicit selected-release approval records; receipts
recompute the generation identity; v1/v2 release tuples must agree on code, name,
APK digest, filename and certificate; R2 and local publication share an exact
public-path allowlist. Approval instructions explicitly use a separately reviewed
trusted verifier before privileged operations.

PASS: 12 unit tests, including stale generation rejection, signed-but-unapproved
release refusal, v1/v2 crossed hash/code/certificate/filename rejection, unexpected
private-file rejection and R2 plan generation without network mutation.

PASS: repeated actual staging with cached dependencies and original verified APK;
new disposable repository certificate SHA256
`f8521fd5c24e39f4ca79c801ae7e5985bc3e61572b6594b43b1fff613b891bc9`.
Actual v1/v2 parity, wrong-key and crossed v2 rejection, approved-generation local
promotion/readback/retry passed. New public generation SHA256:
`aa541a1de0388d43d25a0917832e5ad342ae26fd68e33baee76d02d2f6c2bd45`.
Exact signed entry.jar:
`ce93f39a1e376f30af089a848dc0b95e1100d682a1900a47d87cffd7866de954`;
index-v1.jar `ca24b30cea5d033c16bd6d89a487b0a14949d5fb03b47da383af0e768cb7e945`;
index-v2.json `b8dc82234de9287c7a3089ee37b2b8aa55ee3c41e6bf271df29eaa9599c983db`.
Original acceptance hashes above remain historical test evidence, not approvals
for this revised implementation or production. All previously noted NOT RUN
browser/device/provider acceptance gaps remain.
