# Tracked native source inputs, still NOT PUBLISHED

`source/layers/native_repository.py` and native-repository.patch replace the
remaining production native build injections before compilation. No dependency
repository, public artifact or Android acceptance is activated by this draft.

The proposed origin is
https://repo.collie.im/native-inputs/v1/maven/ (NOT PUBLISHED). All three pinned
`im.collie.native` coordinates, their binary bytes and deterministically
transformed POMs retain the same hashes and sizes as
`native-publication-candidate.json`. The source catalog already names those
exact versions and is structurally validated. The source changes:

- `settings.gradle.kts` contains exclusive content for precisely the three
  native modules at that origin; POM-only metadata prevents missing-POM
  fallback, and Gradle metadata redirection is ignored.
- `build.gradle.kts` contains six ordinary dependency substitutions for the
  historical `im.molly` and `org.signal` coordinates.
- `gradle/verification-metadata.xml` is the exact verified stage XML, SHA-256
  `687fb1544cc1600748129ced52932b8ff8867f0e11bc70f008690a94854f5401`. All six
  native binary and POM hashes remain strict; broad non-native POM trust is
  unchanged and explicitly excludes `im.collie.native`.
- `mavenLocal` and external `libsignalClientPath` includes are removed. There is
  no production native init script or temporary verification-file replacement.
- `build-inputs` declares the complete six-URL/hash/size plan and blocked status.
  App identity, ABI coverage, release test-JNI exclusions, and the unconditional
  Android build exception are unchanged. No property or environment flag can
  activate this draft's Android build.

The source patch reproduces the full published tree without embedding native
binaries. The optional layer generator verifies an existing stage before
producing the reviewed source changes; Gradle never runs that generator. Normal
compilation consumes only tracked source, recipes, and dependencies.

## Executed resolver fixture

The isolated offline Gradle fixture consumes the six accepted cached binary/POM
files and emitted Kotlin declarations. It changes only the proposed HTTPS origin
to a fixture-local file URI and does not invoke or modify the blocked Android
application. Run it with the pinned Gradle distribution and fixture inputs:

```sh
python3 tests/native_source_resolver_smoke.py \
  --gradle PINNED_GRADLE --emitted EMITTED_G3_FILES \
  --accepted-stage VERIFIED_STAGE --scratch NEW_FIXTURE
```

PASS: normal direct Signal, Molly, declared Collie and POM-transitive requests
all resolve exactly the three approved native binaries. PASS: a missing native
module cannot fall back even to identical bytes in another repository; missing
POM, Gradle `.module` without POM, POM metadata-redirection marker, valid-XML
POM tamper and binary tamper all reject. POM/JAR tampering fails Gradle strict
verification. This proves isolated resolver behavior, not a full Android build,
public readback, independent native reproduction, account/device upgrade or
all-test readiness.

## Native JVM test readiness

The native testing JNI is separate from the six production Maven files and must
never enter release APKs. No public host-test binary or complete public host
build recipe is supplied, so native JVM regression is NOT RUN. Production
artifact verification and the resolver fixture do not establish native JVM
test readiness.

## Acceptance limits

The proposed native artifacts are NOT PUBLISHED, and direct Android build
acceptance remains blocked in this source-layer candidate. The repository
contains the pinned coordinates, hashes and strict verification metadata, but
this document does not claim public artifact readback, independent native
reproduction, direct Android build acceptance, runtime/upgrade acceptance or
release readiness. The unconditional source gate and release JNI exclusions
remain in place.
