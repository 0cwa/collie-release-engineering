# Candidate acceptance and cutover gates

This is reviewable code, not a release authorization. Application draft PR
https://github.com/0cwa/collieim-android/pull/41 is **BLOCKED**. Application contracts
PR40 precedes engineering extraction PR1 then publisher PR2. Distribution PR3 is
independent of source rebuilding and uses verified existing RC1 bytes. Engineering
input recipe 36779887ffc0bb1ecff55a87c350740662570039 is a staging pin; merge reviewed
dependencies and regenerate against durable approved refs before eligibility.

| Check | Result |
|---|---|
| Fresh official Signal/Molly fetch + checked checkpoint replay | PASS; merged-main complete reconstruction tree da5c03ff20ae2a672329eb8257bc33949845b268 |
| G/I full tree/checkpoints + exact parents | PASS; source/candidate-plan.json, source/candidate-source-receipt.json |
| Publisher regression tests | PASS; 12 tests including hostile Git, binary/mode/symlink, order/parent/stale-base and synthetic upgrade |
| Entire transition path inventory | PASS; 4482 changed paths classified; complete semantic review NOT RUN |
| Released vs main repository difference | PASS; only README.md and .github/workflows/molly-patch-release.yml differ |
| Released reconstruction determinants | Same complete config/patch/native/tool bytes as main; same declared Collie tree da5c03ff; separate second replay NOT RUN |
| Direct Android build | BLOCKED public immutable native origin and reviewed repository/verification overlay |
| Independent native reproduction | NOT RUN; accepted bytes are trusted-local |
| Physical device/account/attachment/locked database upgrade | NOT RUN |
| Production-signed new artifact | NOT RUN; no new APK built/signed |
| Native/device regression acceptance | NOT RUN |
| Trusted required-check/ruleset administration | BLOCKED operator review/configuration; no existing protection claimed |

The publication overlay changes no application Java/Kotlin logic after complete
reconstruction. Changes are Gradle metadata/explicit gate, documentation/approved
assets/locks and workflow relocation. Whole-tree path inventory is not semantic
security approval. The Gradle metadata function replacement checks an exact
known tail before replacing it; a newly added build block fails construction.

## Operator cutover

1. Review application PR40 and engineering PR1/PR2 (plus distribution independently).
   Keep the existing application local release route during these merges. No
   tag/asset/release metadata changes are needed. Re-read actual application main.
2. Publish exact accepted native AAR/JAR and POM bytes at approved immutable public
   URLs (new operator authorization required). Independently read back hashes
   against build-inputs/native-lock.json; preserve APK signing identity. Add
   narrow exclusive Maven configuration and strict verification as another
   reviewed publication layer. Do not allow upstream fallback or hidden init files.
3. Complete clean direct-source compilation with controlled fresh build/cache
   settings; prove tracked source unchanged, inspect SDK/ABIs/native libraries,
   package/version/debuggable/test JNI, and execute required regression/upgrades.
   Declare native trust limitation; reproduction is a separate bounded follow-up.
4. Regenerate from approved durable engineering revisions. Review every Signal→G
   and old-main→I path, then run externally approved exact-head verifier using
   freshly read remote main and approved plan digest/G/I. G and I full trees must
   match. G source build input cannot be GitHub's synthetic merge. A moving base
   invalidates affected approval. Update the candidate PR rather than force main.
5. Separately approve retained/protected source refs and trusted required checks.
   Publisher/source PR credentials cannot bypass those rules. Merge only after
   all blockers are resolved and owner authorizes cutover. No merge is performed
   by this migration assignment.
6. For future releases reserve versions above all published/reserved codes,
   build once, sign once with existing app identity, validate five equal aliases,
   bind complete receipt graph and operator authorization before promotion.
   Index signing and deployment are separate permissions/keys. An interruption
   resumes approved existing bytes rather than rebuilding.

## Recovery

Before cutover, abandon candidate promotion and retain old main/local route.
After an eventual cutover, use a reviewed ordinary revert/integration commit that
preserves both histories. Never reset protected main, remove Signal ancestry,
move release tags or replace immutable released APKs. Defective binaries require
a higher version. Repository feed publication/rollback is a separate runbook in
distribution; never rotate APK keys to recover a feed failure.

No production release, APK key rotation, repository production key generation,
DNS/TLS/live access change or protected-history rewrite has been performed.

## Reviewed continuation candidate v2

Original PR41/refs remain immutable review evidence. Replacement candidateG
856a394df23168ef08e651b551edb660b79cefb9 andI
4fa701188f3c27cd4af3fa919da2f64ebee5bd6c share full tree
50f5c32b8dd11cac12520bcacb9e7dbb5e2ea74d; plan-v2 digest
67ee8c1ed1a04728e2b99b59c5d3ef66fa26189f5fa2019e81898d9c804ccdce.
The source release gate now blocks every Android configuration independent of
CLI task spelling; actual emitted Kotlin Gradle fixture tests explicit release,
aggregate assemble/build and source-defined releaseVersionCode/name PASS.
Fixture177703 is not a production allocation/build.18 publisher/journal tests
and14 native stage/plan tests PASS. Actual full application contributor commit
roundtrip and independent I-based contributor-ancestry check PASS, with no fixture
refs pushed. A second original patch assembler run was not performed; publication
regeneration consumed cached publicly fetched checkpoint trees retained in Git.

Native exact accepted six binary/original-POM hashes were rechecked locally;
bounded official/fork discovery finds different upstream hashes/metadata, not
matching public inputs. No large binary download/substitution occurred.
See NATIVE-PUBLICATION.md for the concrete operator decision and existing-stage
publication plan; no artifact is uploaded by that helper. The proposal URL is
not advertised as live. COORDINATED-PROMOTION.md binds one manifest/artifact graph
to GitHub and repository readback using a durable credential-free journal.

Source receipt-v2 is post-generation implementation evidence, not owner approval
or signed attestation. A changed recipe/current base still requires new approval
and complete regeneration. Direct Android compilation, device/account/attachment
upgrades, independently reproduced native outputs and live required-check setup
remain BLOCKED/NOT RUN as above.
