# Independent source and release-control review

Review performed separately from implementation authors, 2026-11-07. This is
unsigned review/test evidence, not owner approval, a signed attestation, an
Android build, or independent native reproduction.

## Reviewed immutable inputs

- Application baseline: `f6f68cdee08fb8d9acb6a866b1673deb10525f20`.
- Application contracts PR40: `b59731a1e7b843930e41af3970885f6d9e096556`.
- Engineering PR2 implementation: `087fdfb26f4e2ee4167165d761b0baf6197f6fe4`.
- Replacement source PR43 G: `856a394df23168ef08e651b551edb660b79cefb9`;
  I: `4fa701188f3c27cd4af3fa919da2f64ebee5bd6c`.
- Complete published tree: `50f5c32b8dd11cac12520bcacb9e7dbb5e2ea74d`.
- Reconstructed tree: `da5c03ff20ae2a672329eb8257bc33949845b268`.
- Signal: `a74583b01e260944f699c4da03ba4e8ce04de32b`.
- Complete plan SHA256:
  `67ee8c1ed1a04728e2b99b59c5d3ef66fa26189f5fa2019e81898d9c804ccdce`.

A later documentation-only evidence commit does not change these reviewed
implementation inputs. A changed recipe, source, plan or application base needs
new verification. Retained candidate refs still require operator-approved durable
protection and final dependency pins before release eligibility.

## Independent results

PASS: complete checkpoint and G/I tree/parent/ancestry verification, including the
corrected G2/I2 and exact plan digest. These checks examine intended commits,
not GitHub's synthetic pull-request merge.

PASS: complete migration overlay semantic review. Reconstruction→G1 changes21
paths; G1→G2 corrects only app/build.gradle.kts and BUILD.md. No application
Java/Kotlin, app resources, core or library source changes occur in publication.
Canonical title, backup base name and package match the old actual Collie recipe;
FCM-capable default is preserved. Approved assets/LICENSE/NOTICE match prior main;
README changes only the source/build/engineering-location sentence. New workflows
have no signing/deployment credentials. Source layout CI is not build approval.

PASS: actual emitted Kotlin expressions exercised in an isolated offline Gradle
fixture. Explicit release, aggregate assemble and build configurations all refuse
direct compilation while native publication is blocked. Explicit versionCode177703
and fixture name override evaluate correctly; published code177702 is rejected.
This is a synthetic Gradle fixture, not an Android build or version reservation.
Git metadata is now declared, so historical APK byte parity is not claimed.

PASS: an ordinary comment-only application test contribution above actual I2 was
exported, stored with its extended plan in a detached engineering fixture commit,
consumed by the publisher and regenerated with exact full reviewed-tree equality.
Explicit current-main binding preserves contributor ancestry and rejects a stale
base; a different approved patch digest is rejected. Fixture identities are in
`independent-contribution-receipt.json`. No fixture/product refs were pushed.
This uses cached publicly fetched checkpoints, not a second assembler replay.

PASS: independent targeted policy checks cover canonical-only signing before key
access, publishing before API calls, receipt graph substitution, raw final-manifest
vs canonical release-receipt digest separation, and legacy unknown evidence.
Shared policy modules and schema compare byte-for-byte between app40 and eng2.
The legacy repository receipt's null canonical release link cannot pass the
complete new-source graph verifier. Separate synthetic new-v2 ingestion fixture
checks require passing, externally pinned source/build/release inputs; this does
not claim an actual newly built APK.

PASS: five actual publisher-entrypoint fixtures exercise existing/partial draft
resume, lost create/publish responses, already-published exact-byte readback,
alias substitution rejection and refusal to turn a published release into a draft.
Five coordinated-journal and three native-publication-plan tests independently
pass. They execute no remote mutations; plans/reports require external approvals.
The native planner verifies existing accepted inputs and labels NOT PUBLISHED.

## Scope and remaining gates

The full raw transition inventory covers3660 inherited Signal→reconstruction paths
and4482 old-main→integration paths (no rename collapsing). Historical selected
storage, proxy, notification/preferences, restore, view-once, retention composition
and branding safeguards were statically inspected. The entire inherited semantic
application diff, every translation/image/module deletion, and native binary
runtime behavior were NOT fully reviewed. Path inventory or equal trees do not
retroactively approve those semantics. Migration overlay review is complete;
historical product review remains separately scoped.

BLOCKED: public immutable accepted native inputs and clean direct Android build;
final approved durable engineering pins/regeneration; trusted required checks and
retained-source protection. NOT RUN by this reviewer: account/attachment/locked-
database/device runtime scenarios, production-signed new candidate upgrades,
independent native reproduction, production hosting/index signing/deployment.
Client tests reported by the distribution lane are separate evidence.

No production release, merge, APK-key rotation, production repository-key
activation, DNS/live access change, or protected-history rewrite was performed.
No browser was used by this reviewer. Synthetic Gradle build/cache was removed;
small receipts/recreation instructions remain. Temporary fixture Git objects are
not production release inputs.

## Recreation commands

Use the exact implementation revision above, a complete object store containing
retained candidate source history, and an independently reviewed plan digest:

```
python3 source/verify_candidate.py --repo REPLAY --plan source/candidate-plan-v2.json \
  --expected-plan-sha256 67ee8c1ed1a04728e2b99b59c5d3ef66fa26189f5fa2019e81898d9c804ccdce \
  --current-main f6f68cdee08fb8d9acb6a866b1673deb10525f20 \
  --expected-source 856a394df23168ef08e651b551edb660b79cefb9 \
  --expected-integration 4fa701188f3c27cd4af3fa919da2f64ebee5bd6c
PYTHONPATH=tests python3 -m unittest test_publish_resume test_coordinated_promotion -v
PYTHONPATH=tools/native python3 -m unittest tools/native/test_publication_plan.py -v
PYTHONPATH=tools/patches python3 -m unittest \
  test_migration_receipts.Receipts.test_raw_manifest_and_canonical_release_receipt_are_distinct \
  test_migration_receipts.Receipts.test_graph_approvals_and_substitution -v
```

For actual-source contribution recreation, use `source/import_contribution.py`
with a single-parent reviewed child of I2, exact separately approved binary patch
hash and explicit current-main retaining that child. Commit the exported patch
and extended plan into an engineering representation, consume that exact plan
with `source/publish.py`, and compare the complete resulting source/integration
trees to the reviewed child. Fixture receipt names exact original outputs; a
changed contributor identity/input predictably changes generated commit hashes.

For Kotlin fixture recreation, export G2:app/build.gradle.kts and run
`agent-heavy -- python3 tests/publication_gradle_smoke.py --gradle PATH_TO_PINNED_GRADLE
--app-build-script EXPORTED_SCRIPT --scratch NEW_FIXTURE_DIRECTORY`.
Remove the generated fixture directory after preserving the small result.
