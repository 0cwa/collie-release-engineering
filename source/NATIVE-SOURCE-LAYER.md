# Tracked native source inputs, still NOT PUBLISHED

`source/layers/native_repository.py` and native-repository.patch replace the
remaining production native build injections before compilation. No dependency
repository, public artifact or Android acceptance is activated by this draft.

The exact existing proposed origin is
https://repo.collie.im/native-inputs/v1/maven/ (NOT PUBLISHED). All three pinned
`im.collie.native` coordinates, their binary bytes and deterministically
transformed POMs retain the same hashes/sizes as native-publication-candidate.json.
The source catalog already names those exact versions and is structurally
validated. The new source changes:

- settings.gradle.kts contains exclusiveContent for precisely the three native
  modules at that origin; mavenPom-only metadata prevents missing-POM fallback,
  and ignoreGradleMetadataRedirection prevents .module substitution.
- build.gradle.kts contains six ordinary project dependency substitutions for
  im.molly and org.signal historical coordinates, matching the old native init.
- gradle/verification-metadata.xml is the exact verified stage XML, SHA256
  687fb1544cc1600748129ced52932b8ff8867f0e11bc70f008690a94854f5401.
  All six native binary/POM hashes remain strict; broad non-native POM trust stays
  unchanged and explicitly excludes im.collie.native. No trust is widened.
- mavenLocal and external libsignalClientPath includeBuild are removed. There is
  no production native init script or temporary verification-file replacement.
- build-inputs declares the complete six URL/hash/size plan and blocked status.
  app/build.gradle.kts, package/version identity, ABI coverage and release test-JNI
  exclusions are unchanged. The unconditional Android exception stays in place.
  No property/environment flag can activate this draft's Android build.

The source patch reproduces the full published tree without possessing opaque
local native binaries. The optional layer generator verifies an existing stage
before producing the reviewed source changes; Gradle never runs that generator.
Normal compilation consumes only published tracked source/recipes/dependencies.

## Executed resolver fixture

The isolated offline Gradle fixture consumes the exact six accepted cached
binary/POM files and emitted Kotlin declarations, changes only the proposed HTTPS
origin to a fixture-local file URI, and uses a clean dependency cache. It uses no
init script and does not invoke/modify the blocked Android application.

```
agent-heavy -- python3 tests/native_source_resolver_smoke.py \
  --gradle PINNED_GRADLE --emitted EMITTED_G3_FILES \
  --accepted-stage VERIFIED_STAGE --scratch NEW_FIXTURE
```

PASS: normal direct Signal, Molly, declared Collie and POM-transitive requests all
resolve exactly the three approved native binaries. PASS: missing native module
cannot fall back even to identical bytes in another repository; missing POM,
Gradle .module without POM, POM metadata-redirection marker, valid-XML POM tamper
and binary tamper all reject. POM/JAR tampering fails Gradle strict verification.
This proves isolated resolver behavior, not a full Android build, public readback,
independent native reproduction, account/device upgrade or all-test readiness.
Remove the fixture's own Maven copies/build/cache after retaining the small result.

## Separate host test input

Historical JVM regression uses a separate Linux x86-64 testing JNI resource,
libsignal_jni_testing_amd64.so, injected by the old test-only host-jni.init.gradle.
It is NOT among the six production Maven files and must never enter release APKs.
build-inputs/host-test-inputs.json records its identity and the immutable public
staging-guard recipe (engineering808ee635, tools/native/host_jni_guard.py SHA256
038f9553916d93baf2a6919d19c9146f6486ccfec1dacf8eee78b64d595db538).

The cached candidate rehashes to
b3f94489c68433abfb00883c9a4579565cfd6db820fcbd006b1ca581beaf9d8e,
583617656 bytes. Its historical receipt names the OLD native lock9866196d and
prepared libsignal tree de930179286b4fe68875475c1b0678615e180908. It is not current
host-test acceptance. No public host-test binary or complete public host builder
is supplied; native JVM regression is NOT RUN, not skipped or passed. Bounded
follow-up: review a pinned Linux testing-JNI build from that public libsignal
source/patch recipe, lock the toolchain/output, materialize the existing classpath
conflict/hash guard as tracked test-only build logic, then execute relevant tests.
Publishing the six production files alone does not meet that acceptance.

## Remaining operator steps

Owner authorization is still required for actual publication of accepted native
inputs or for choosing independent reproduction first. After publication,
independently read back all six exact URL bodies and hashes, review a separate
activation/acceptance layer, then execute clean direct Android compilation and
package/native/runtime/upgrade checks. The repository declarations and strict
verification are already implemented; no additional hidden native init setup is
needed. Keep hosted signing/deployment disabled and preserve the existing local
operator release route during review. Never change hashes to accommodate stock
fallbacks. No public artifact, repository index, key, DNS or default branch was
changed by this source-layer preparation.
