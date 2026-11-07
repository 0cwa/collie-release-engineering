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

PASS: `python -m unittest discover -s distribution -p 'test_*.py'`: 7 tests,
covering immutable APK conflicts, duplicate approvals, signer verifier failure,
interrupted promotion/resume, changed-generation rejection, symlinks and unknown
approval fields. `python -m py_compile distribution/*.py` passed.

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
