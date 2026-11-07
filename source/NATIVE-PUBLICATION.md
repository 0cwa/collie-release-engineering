# Accepted native inputs: verified availability and operator choice

Remote heads were revalidated before continuation: engineering PR2 8604b811 and
application PR41 3c710e45. No production publication occurred.

The existing local native-proposal cache contains all three accepted binaries
and original POMs. Rehashing all six against tools/native/native-lock.json passed:
libsignal-android AAR215587114 bytes, libsignal-client JAR1003356 bytes,
ringrtc-android AAR94240994 bytes; original POMs1981/1967/1850 bytes respectively.
The staging receipt also verifies transformed Collie POM bytes and all ABI entries.
No large artifact downloads were needed. These are pre-existing accepted bytes,
not newly reproduced outputs. RingRTC historical build reused WebRTC/compiler
outputs; libsignal history records host/path-sensitive archive/ELF differences.

Checked public inputs: official Signal Maven responds for all three modules;
its POM bytes differ from every locked original POM. Advertised official binary
SHA256s differ from accepted binaries: libsignal-android865b2cfba4005ba830800021f70144687252c095880171d4cd213e29f989d204,
libsignal-client8a2da96b0bae7ae431fb871ea864e9219bf7d78eacb3e11c7e5986ad7f672274,
ringrtc4e6c65aabddb8bb6cb5ad1e6fac635484290d8a520f9f3d3851f0efc529bceab.
Those advertised hashes are discovery evidence, not downloaded binary hashes.
No upstream substitution was performed. Named tw-hx release has only an APK;
Molly native repositories expose no matching release assets. This bounded search
found no matching public artifact, rather than proving none exists anywhere.

## Minimal operator decision

Approve publishing the exact accepted binary bytes and deterministically
transformed Collie POMs as six immutable Maven inputs on the approved existing
object-storage origin; alternatively require a separately scoped independent
native build/review first. Recommend publishing accepted bytes for continuity
while retaining the explicit trusted-local limitation. Neither action is
performed here; producing source/PRs did not require this approval.

## Reuse the existing staging tool

```
python3 tools/native/stage_artifacts.py --lock tools/native/native-lock.json \
  --input-maven ACCEPTED_INPUT_MAVEN --output NEW_STAGE \
  --verification ORIGINAL_SOURCE/gradle/verification-metadata.xml
python3 tools/native/publication_plan.py --lock tools/native/native-lock.json \
  --stage NEW_STAGE --original-verification ORIGINAL_SOURCE/gradle/verification-metadata.xml \
  --public-repository https://repo.collie.im/native-inputs/v1/maven/ > native-plan.json
```

The URL is a proposed operator destination, not advertised as live. The plan
rehashes/verifies the complete existing stage, exact lock, unchanged original
POMs, transformed POMs, metadata and stage receipt; it copies/uploads nothing.
No new native platform was introduced. Upload only `NEW_STAGE/maven/` using the
same single R2/S3 provider adapter/account approved for distribution, with
immutable object names and long-lived immutable cache headers. Do not upload
native.init.gradle, operator paths/config or private workspace. Index signing
and APK signing are unrelated. A provider with an existing object must compare
hashes and refuse a different body. Keep the plan digest and provider readback.

After approval and actual publication, independently download every plan URL
and hash all six bytes. Pin the approved recipe revision, exact repository URL,
module versions, transformed POM hashes and strict Gradle verification bytes as
a reviewed application publication layer. Replace legacy native init injections
with ordinary settings/project declarations; exclude upstream fallback. Remove
mavenLocal from the production recipe. Construction ends before compilation.
Only then remove the all-task native build block, execute clean direct builds,
inspect packaged ABI libraries and run upgrade/regression acceptance. Do not
remove the block merely because the six local bytes match. The independent
native reproduction follow-up remains separately required before claiming it.
