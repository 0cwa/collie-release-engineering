# Source publication migration baseline

Frozen 2026-11-07. Main/PR38 merge: `f6f68cdee08fb8d9acb6a866b1673deb10525f20`.
PR38 head: `10820822a3fc9b481085010924f2842535263796`; GitHub reports merged.
No discovery-anchor differences. Preserve all historical tags and assets.

Release ID 437221260, tag v8.35.1-1-rc1, immutable, published, not prerelease.
Tag object `06b45f65aa838466bc9de4b226f77a7c84957ea0` peels to
`698902e405975bbd87789a2e376f80b564031738`; target_commitish is not evidence.
Signal input: `a74583b01e260944f699c4da03ba4e8ce04de32b` (8.35.1).

All five actual downloaded release APKs independently hash to
`5fbd0fe817bc6711c56fbc39b8479cc0a68a7d7fe921a7d9c9fab59333fda9ee`.
Each has 97,054,341 bytes. Canonical aapt2/apksigner checks report package
im.collie.app, versionCode 177702, versionName 8.35.1-1-rc1, minSDK27,
targetSDK36, one RSA4096 signer with v2/v3 signatures and certificate SHA256
`f8cc0c0c052e5635c26fcd293c2b0af3e87d61ff7f2df3602a9af6e91f73c826`.
No application-debuggable badging entry. Physical-device upgrade NOT RUN.

Historical release inventory has four published releases. Previous manifests
report 177201 (8.33.2 RC1), 177202 (RC2), 177203 (RC3). All three actual archived Collie APKs were downloaded, hashed against their
respective manifests, and independently inspected with aapt2/apksigner: all
three have package im.collie.app and the same production certificate. See the
verified published-inventory fixture for hashes. Current verified
production floor is 177702; production reservations can raise it further.

Fixtures preserve the actual RC1 final manifest and provenance, including the
explicit instrumentation NOT RUN assessment. No historical source/build receipt
or independent native-reproduction evidence is manufactured by the adapter.

## Actual release path

`tools/patches/release.py`: checkpoint reconstruction; `build.py`: source build,
branding/version init scripts and reviewed local native substitutions;
`release_collect.py`: unsigned evidence; `release_sign.py`: offline operator key,
one signing operation followed by copies in single-collie mode;
`release_publish.py`: signed tag and draft/readback/manual publish.
`tools/native`: native recipes, checks and artifact lock. `.github/workflows/`
contains patch-layout checks, old five-profile unsigned workflow and upstream
sync/tag workflows. Hosted 8.35 building remains blocked pending native review;
this migration does not remove that integrity block.

The existing source path injects native dependencies through an external init
script and uses trusted-local accepted artifacts. Direct checkout build readiness
is BLOCKED until reviewed publication layers replace every injection with public
pinned inputs. `build.py --accept-trusted-local-native` is an explicit trust
limitation, not independent reproduction. app/build.gradle.kts reads Git timestamp,
HEAD and tags; equal trees with different histories do not establish equal APKs.
