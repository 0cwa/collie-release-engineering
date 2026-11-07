# Independent native source-input layer review

Reviewed separately from the implementation author, 2026-11-07. This is unsigned
implementation/test evidence, not production authorization, an Android build,
public input availability, or independently reproduced native binaries.

## Immutable inputs

- Engineering implementation: `79badedf5a881803b72cdc6318478ab6b8a39b1e`.
- Previous source G2: `856a394df23168ef08e651b551edb660b79cefb9`;
  full tree `50f5c32b8dd11cac12520bcacb9e7dbb5e2ea74d`.
- Current application source draft PR45 G3:
  `6301e215418c69c8ccab2012fbcc05a76d7081eb`.
- I3: `8b119e357d1c9c6863eff31756660bc07d6cfb8b`;
  complete tree `b5fbed355186be1e728d556a48718e7158e7ab8f`.
- Complete plan SHA256:
  `b81dcc44ccd52bcc3b0e3c7d344cf1244021da68b96969220c02c83aa3368d03`.
- Application base remains `f6f68cdee08fb8d9acb6a866b1673deb10525f20`.

Old source PR43 and version2 refs remain unchanged as prior review evidence.
New inputs require new approval; no public history was rewritten.

## Complete eight-path layer review

PASS: reviewed all changes in BUILD.md, native-artifacts.json,
native-publication.json, host-test-inputs.json, root build.gradle.kts,
gradle.properties, gradle/verification-metadata.xml and settings.gradle.kts.

The old native init script's exclusive repository and six coordinate substitutions
now exist in ordinary tracked settings/root project configuration. Both legacy
im.molly and org.signal coordinates select the same three pinned im.collie.native
modules. Existing catalog pins already select those coordinates and remain
unchanged. Local Maven lookup and the optional external native includeBuild are
removed. The proposed immutable HTTPS origin remains explicitly NOT PUBLISHED.

PASS: the complete tracked verification XML is byte-identical to the accepted
stage, SHA256 `687fb1544cc1600748129ced52932b8ff8867f0e11bc70f008690a94854f5401`.
It preserves other dependency checks and requires native binary and transformed
POM hashes. Native POMs are excluded from the inherited broad POM trust rule.
No lenient mode or bypass was introduced.

Independent review found an initial artifact-only repository fallback that could
accept a missing POM. The reviewed final layer requires Maven POM metadata and
ignores Gradle metadata redirection. The finding is resolved and covered by
actual resolver failures below.

PASS: the actual emitted fixture files compare byte-for-byte with the final
candidate Git tree. Full application/core/library source objects and the version
catalog match G2. In particular app/build.gradle.kts, release JNI exclusions and
the unconditional Android-build exception are unchanged. No property opens that
exception. No binary library or APK is added to Git or rebuilt by this layer.

PASS: the checked-in native publication patch, applied to the exact complete G2
tree in a private index, reconstructs the complete G3 tree. Actual G3/I3 full
checkpoint/tree/parent/ancestry verification also passes.

## Independent execution

Three focused materialization/tamper/gate unit tests pass.

Seven Gradle9.6.0 dependency-resolution cases independently pass using the actual
six accepted cached binary/POM bytes, isolated caches and the exact emitted source
DSL. Only the proposed origin is changed to a file URI in the throwaway fixture;
no native init script, public fetch or Android compilation is used.

1. Both legacy coordinate groups, direct declared coordinates and transitive
   native requests resolve to identical pinned canonical binaries.
2. A second repository containing matching bytes cannot replace a missing module
   from the exclusive native repository.
3. A binary without its required POM is rejected.
4. A Gradle module descriptor without the required POM is rejected.
5. A changed POM containing a metadata redirection marker is rejected.
6. A valid-XML POM with changed bytes fails strict checksum verification.
7. A changed native binary fails strict checksum verification.

Negative logs were checked for the intended missing component/POM or native
POM/JAR verification failures, not an unrelated compilation error. The fixture
clears its dependency metadata cache between cases. Tiny result receipt:
`independent-native-source-receipt.json`.

Recreate against the pinned implementation and actual emitted G3 source:

```
PYTHONPATH=tests:tools/native python3 -m unittest \
  test_native_source_layer.NativeSourceLayerTests -v
agent-heavy -- python3 tests/native_source_resolver_smoke.py \
  --gradle PATH_TO_GRADLE_9_6 \
  --emitted EXPORTED_G3_BUILD_INPUT_FILES \
  --accepted-stage EXACT_SIX_FILE_STAGE --scratch NEW_PRIVATE_FIXTURE_DIRECTORY
```

Export settings.gradle.kts, build.gradle.kts, gradle/verification-metadata.xml
and build-inputs/native-artifacts.json from the exact G3 above for the resolver
fixture. Remove generated native copies, caches and build files after preserving
small text results. The existing accepted input cache is not a public dependency
and is not modified by this fixture.

## Remaining readiness boundaries

Native input declaration preparation is complete for the reviewed six-file
contract; actual publication/HTTPS readback and clean direct Android compilation
remain BLOCKED pending owner approval and public inputs. Keep the unconditional
source gate until separately reviewed acceptance authorizes removal. No new APK,
release version reservation or production source eligibility is claimed.

Host-native JVM regression inputs are separately declared NOT RUN: the historical
local candidate uses an older lock, with no public host binary or complete host
build recipe. Its staging guard is pinned independently. Those inputs must never
enter an APK; the six production Maven files do not prove all-test readiness.
Independent native reproduction, broader account/attachment/locked-database/
physical-device scenarios and the full inherited application semantic audit remain
separately scoped as in the prior independent report.

No signing, production artifacts/indexes, keys, DNS, live policy, merge or
protected-history changes occurred. No browser was used by this reviewer.
