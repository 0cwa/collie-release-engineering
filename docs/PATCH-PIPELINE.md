# Molly releases as patches on a Signal base

The tracked application source on these branches is the exact Signal release.
The build reconstructs Molly in a separate worktree. Review the pipeline,
the original Molly snapshot, and any compatibility changes as separate commits.

## Source and review boundaries

| Input | Immutable source |
| --- | --- |
| Signal 8.19.2 | `c47a74ba0961903eeaeda6d22347a990056c3e83` |
| Official Molly 8.19.2-4 | `0e202dda90785771ff8c87526eaaf30c655f54f3` |
| Official Molly source tree | `3cd0bb01cf840e4b702187085432020d163d80ce` |
| Signal 8.33.2 | `6e9dddc01f1b26e608ac683807c51e139c141ed2` |
| Pipeline and historical export origin | [PR #7 at cef8e04](https://github.com/0cwa/collieim-android/tree/cef8e044f55d1a47c5634f500e01b3956ee31f4f) |

`molly-build.json` selects the release. Its Signal commit must be an ancestor
of the branch. The assembler rejects changes to the tracked Signal source
outside the explicitly named pipeline, documentation, patch, and CI paths.
An application edit cannot disappear behind a broad source projection.

`patches/molly` contains the original 65 mailbox patches in 16 review groups,
covering 3,178 changed paths. Their bytes, source pins, ordering, binary
payloads, executable modes, and original whitespace are retained from PR #7.
They represent the released Molly snapshot, including maintained merge
resolutions and third-party attribution already present in that snapshot.
The patch grouping was organized by AI; it adds no application logic to this
historical source. Grouping does not make features independently buildable.

The README inside that unchanged snapshot retains its original PR #7 paths.
Use the commands in this guide for this branch; the old `tools/827` pilot,
pilot-specific source adaptations, reports, and CI jobs are not imported.

The pipeline and its tests are AI-assisted infrastructure. Compatibility
mailboxes under `patches/ports` are explicitly identified as AI-generated
application adaptations. They never replace or silently regenerate the
official Molly mailboxes.

## Reconstruct and prove the source

Use Git with `merge-tree --write-tree --merge-base` support and Python 3.10+.
The port was generated with Git 2.51.1; its exact mechanical-tree gate detects
changes in Git's merge behavior. Git and Python alone are sufficient for
source verification. A complete, non-shallow history is required for ancestry
checks.

```sh
git fetch --no-tags https://github.com/mollyim/mollyim-android.git \
  0e202dda90785771ff8c87526eaaf30c655f54f3
python3 -m unittest discover -s tools/patches -p 'test_*.py' -v
python3 tools/patches/release.py --repo . --config molly-build.json \
  --worktree /tmp/molly-release --report /tmp/molly-source-proof.json
```

The destination must be new and outside the source checkout. A failed
explicit destination is retained for inspection. The assembler does not
rewrite the checkout, move its branch, or modify patch files.

Every build first reconstructs the **official historical Molly tree** from
the pinned old Signal commit. The complete verifier replays both legal
ready-node orders, checks provenance and unique path ownership, and requires
exact whole-tree equality. It also checks the materialized index and files;
ignored leftovers and sparse omissions are not accepted as a clean build.
The source-whitespace exception is conditional on this exact tree equality.

For Signal 8.19.2 this verified source is the Android build input. A configured
native hotfix is a separate, mandatory native-source build input, described
below. The historical Android tree proof alone does not establish that an APK
contains the fixed native library.

## Line movement is mechanical

Ordinary mailboxes are applied with `git am --no-3way --whitespace=nowarn`.
Git locates hunks using their context, so adding or deleting unrelated lines
earlier in a file needs no patch edit. The runner explicitly disables ambient
whitespace-ignore matching, automatic whitespace correction, hooks, signing,
and rerere. Changed context stops the replay; it is not discarded as fuzz.

Regression tests cover movement in both directions, different offsets for
multiple hunks, unchanged mailbox bytes, changed context, and ambient Git
settings that would otherwise hide a conflict. Do not refresh hunk headers
or regenerate every mailbox just because an upstream line number changes.

An ordered series can also contain checked JSON records for whole-file
deletions and translation generation. The assembler checks record and input
hashes, generated output hashes, and the before and after trees. Translation
generation follows Molly's transform and matches all 816 pinned native witness
outputs; the pipeline itself does not execute Gradle. The `please_wait`
relocation is validated separately.

For a new Signal release, the assembler uses a separate deterministic
three-way integration step. It first proves the original Molly patches,
then merges that exact snapshot with the new Signal base using the old
Signal commit as the explicit merge base. This runs in an isolated Git
directory with fixed merge options. Stable `MOLLY` and `SIGNAL` labels prevent
commit-ID changes from introducing needless conflict-marker edits.

Clean automatic merges, context relocation, and Git's configured rename
detection happen in that mechanical step. No AI runs during reconstruction.
The pinned attribute files must be present before merging; missing partial-clone
objects cannot silently disable an attribute driver. The complete resulting tree is pinned. A different result fails with a
request to inspect the inputs and Git behavior, rather than silently updating
the expected hash.

## Review the Signal 8.33.2 adaptations

The first two commits on the upgrade branch contain the pipeline and original
Molly snapshot with **no AI compatibility patches**. This is an intentionally
unadapted review checkpoint: source assembly reports the conflicts and refuses
to label the result buildable.

Subsequent commits add ordinary mailbox resolutions, grouped by application
feature. Their parent source is Git's mechanical merge, so they show changes
that remain after deterministic integration. The initial groups reproduce
the existing PR #7 source merge
`90a0bb917ecc49a62236d539f4e7faae01b1d9fe`; eight subsequent layers retain
the preference-import, NetCipher SDK DSL, broader integration/regression, CDN
test-fixture, linked-device reminder, backup preference precedence, donation
completion, and legacy backup-notification cleanup corrections as individual
review steps. Each mailbox
records its source commit and AI-generation status.
Previous mailboxes do not receive new provenance headers when a later fix is
added.

The port has 764 conflict paths. Initial resolution patches change 763 of
them; the retained `CorrectedPreferenceFragment.java` modify/delete conflict
is recorded as an explicit keep-Molly decision in the configuration. A
content diff is unnecessary for that choice because Git already leaves
Molly's version in the conflicted tree. The choice is still reviewed as an
AI compatibility decision. Every conflict must be covered by a resolution
or an explicit unchanged-content decision.

The final source must equal tree
`59fa2b2af25d82ffd11d0198d5e9e8f5b5b3eadf`, the tree of synthetic source
commit `a069fdf625d924e517eb6f2aeb0cbd607db3fcdb`. It extends donation
checkpoint `0e7a018870f034d547db10e8e4856c515bbe2b80` (tree
`c9e7ee6396f5109c8d29e5e87f551cd4e4b9c68d`) with channel-checked cleanup of
Molly's old backup alerts at IDs `120500` and `120501`; new backup alerts keep
Signal's current IDs. The donation checkpoint extends backup checkpoint
`266d0185d021b2b6a98882205f6b6242152d146e` (tree
`febafe3aa084d0ca6219e4f10a27ee66fbecfe4e`) with the restored donation badge
acknowledgement, eligible monthly-sustainer link, and a completion-sheet
loading-state fix plus Compose coverage. The backup checkpoint extends
re-registration checkpoint `d3c917a5be7db6116c83b7e29e09f145e7f38825` (tree
`0e5c581b7db3f4429256630011790f3ed4ad141f`). That checkpoint extends
`cb8173a9ed5a4965d433e10e1a0a64e241b2ce4c` (tree
`2533afec2fe2d7f9fc682f699fc21d7624521889`) with the explicit
re-registration marker, linked-device reminder assignment after successful
primary-device multi-device registration, and marker test. These settings
restore Molly's prior reminder behavior while leaving new registrations
unchanged. This remains a development port, not an official Molly release.

Each synthetic source commit is stored in an incremental bundle. The reminder
bundle requires `cb8173a9ed5a4965d433e10e1a0a64e241b2ce4c`; the backup bundle
requires `d3c917a5be7db6116c83b7e29e09f145e7f38825`; the donation bundle
requires `266d0185d021b2b6a98882205f6b6242152d146e`; and the backup-notification
bundle requires `0e7a018870f034d547db10e8e4856c515bbe2b80`. Each prerequisite must
already be present in the object database. For a fresh local repository,
materialize and verify the chain in order:

```sh
git cat-file -e cb8173a9ed5a4965d433e10e1a0a64e241b2ce4c^{commit}
git rev-parse cb8173a9ed5a4965d433e10e1a0a64e241b2ce4c^{tree}
git bundle verify patches/ports/signal-8.33.2/followups/reregistration-linked-devices-reminder/source-checkpoint.bundle
git fetch patches/ports/signal-8.33.2/followups/reregistration-linked-devices-reminder/source-checkpoint.bundle \
  refs/heads/source-checkpoint/reregistration-linked-devices-reminder:refs/port-source/reregistration-linked-devices-reminder
git rev-parse refs/port-source/reregistration-linked-devices-reminder^{tree}

git bundle verify patches/ports/signal-8.33.2/followups/legacy-backup-precedence/source-checkpoint.bundle
git fetch patches/ports/signal-8.33.2/followups/legacy-backup-precedence/source-checkpoint.bundle \
  refs/heads/source-checkpoint/legacy-backup-precedence:refs/port-source/legacy-backup-precedence
git rev-parse refs/port-source/legacy-backup-precedence^{tree}

git bundle verify patches/ports/signal-8.33.2/followups/donation-completion/source-checkpoint.bundle
git fetch patches/ports/signal-8.33.2/followups/donation-completion/source-checkpoint.bundle \
  refs/heads/source-checkpoint/donation-completion:refs/port-source/donation-completion
git rev-parse refs/port-source/donation-completion^{tree}

git bundle verify patches/ports/signal-8.33.2/followups/backup-notification-id-upgrade/source-checkpoint.bundle
git fetch patches/ports/signal-8.33.2/followups/backup-notification-id-upgrade/source-checkpoint.bundle \
  refs/heads/source-checkpoint/backup-notification-id-upgrade:refs/port-source/backup-notification-id-upgrade
git rev-parse refs/port-source/backup-notification-id-upgrade^{tree}
```

The tree commands must print, in order, `2533afec2fe2d7f9fc682f699fc21d7624521889`,
`0e5c581b7db3f4429256630011790f3ed4ad141f`,
`febafe3aa084d0ca6219e4f10a27ee66fbecfe4e`,
`c9e7ee6396f5109c8d29e5e87f551cd4e4b9c68d`, and
`59fa2b2af25d82ffd11d0198d5e9e8f5b5b3eadf`. The bundles do not carry their
prerequisites; a fresh clone cannot verify or fetch a later bundle until the
earlier checkpoints are materialized.

When changing a resolution, update only the affected mailbox and its reviewed
target-tree pin. Leave unrelated mailbox envelopes and the historical forest
alone. `export_complete.py` remains available for exporting a new official
source snapshot; it is not a command to refresh unchanged patches for line
offsets. An upstream release with genuinely changed APIs still requires
explicit compatibility work and review.

## The microphone fix is a native source cherry-pick

The post-hangup media teardown fix is Signal RingRTC commit
[`4b1b6fa53c9f30c87c8ea78e44675d4a13e1e4d3`](https://github.com/signalapp/ringrtc/commit/4b1b6fa53c9f30c87c8ea78e44675d4a13e1e4d3),
"Ensure calls terminate completely in corner cases," discussed in
[Molly issue #843](https://github.com/mollyim/mollyim-android/issues/843).
It belongs to RingRTC's native Rust source, which Android consumes as an AAR.

On the historical branch, the final commit adds `native-ringrtc.json` and
the mandatory native build integration. The native helper checks out Molly
RingRTC v2.69.5-1 (`8d81a87adcb08b77446b691be3692ff7ed53d580`) and **directly
cherry-picks the Signal commit**, preserving its author and upstream trailer.
There is no microphone hotfix `.patch` in either Molly forest.

All production hunks apply unchanged. The only backport adjustments merge two
test import blocks while preserving the older test infrastructure. The helper
checks exact preconditions, the whole resulting native tree
`f310c13efdb82ff6123f05df6e232a74babb7d33`, and equality of the production
patch ID with Signal. Native production behavior and the six regression tests
come from Signal; no replacement native implementation is generated.

This preserves Molly's native API, proxy support, and WebRTC fork. Jumping the
old Android app straight to RingRTC 2.72/2.73 would require unrelated calling
API migrations. Keeping the old published AAR would omit the fix. The build
therefore produces a distinct local version and substitutes that exact
artifact through an exclusive repository. Missing native output is fatal;
the original Molly dependency pin cannot downgrade the resulting build.

The native helper exposes separate source, build, and integration stages:

```sh
python3 tools/native/ringrtc.py prepare --lock native-ringrtc.json \
  --source-dir /tmp/ringrtc-source --proof-dir /tmp/ringrtc-proof
python3 tools/native/ringrtc.py build --lock native-ringrtc.json \
  --source-dir /tmp/ringrtc-source --proof-dir /tmp/ringrtc-proof \
  --maven-dir /tmp/ringrtc-maven
python3 tools/native/ringrtc.py integrate --lock native-ringrtc.json \
  --app-dir /tmp/molly-release --proof-dir /tmp/ringrtc-proof \
  --maven-dir /tmp/ringrtc-maven
```

Use the generated `/tmp/ringrtc-proof/ringrtc.init.gradle` for every Android
Gradle invocation on this branch. The checked-in build driver enforces that
requirement. Only the exact locally produced component receives new artifact
hashes in the generated worktree's verification metadata; existing dependency
verification remains strict. The native source/build proof and artifact hashes
travel with the CI result. A hash calculated from a local build records that
build's output; it is not an independent reproducible-build attestation.

Signal 8.33.2 already contains the fix through its RingRTC update. The port's
Molly RingRTC 2.73.0-1 dependency includes the same upstream native commit, so
the older-API native backport is confined to the historical branch.

## Build gates and limits

`.github/workflows/molly-patch-release.yml` runs a source-proof job and a
dependent Android job on standard Ubuntu runners. It replaces the inherited
Signal APK and APK-diff PR jobs, which would otherwise build the unpatched
Signal checkout and request unrelated premium runners. Repository permissions
are read-only; the workflow does not publish releases or sign production APKs.

The Android job reconstructs the verified source again, prepares the native
library when configured, installs the exact source toolchain, assembles the
Website debug APK, and runs the configured test tasks. The 8.33.2 profile
retains the complete network suite and focused integration regression gate
from PR #7. The 8.19.2 profile uses tests present in the official old source;
it does not import AI-authored port tests. Native source reconstruction and
native compilation/test results are reported separately.

Source equality, successful compilation, unit tests, reproducible binaries,
production signing, and device/live-service behavior are distinct claims.
Keep a PR in draft until its required native and Android gates have actually
passed. Neither a source-tree proof nor an earlier candidate's CI result is
a fresh APK validation. No native binary checksum, build success, or device
acceptance is assumed by these patches.

## Local stack scope and port report

For a stack boundary, pass its explicit base to the preflight, for example
`tools/patches/preflight.sh --scope pipeline --base stack/0-molly-8.19.2-4`.
Use `switch`, `release`, `followups`, `additions`, or `features` for the subsequent boundaries.
For the feature branch, use `--scope features --base stack/5-behavior-additions`.
For the fifth branch, use `--scope additions --base stack/4-behavior-followups`. The
scope check validates the base ancestry, source projection, approved workflow
paths, Molly forest identity, and profile pins. The pipeline branch has no
Android profile, so its preflight runs the tool tests only.

`port_report.py` measures physical Git additions and deletions for the Molly
patchset, the mechanical merge, and AI-assisted compatibility changes. Its
preview marks missing hunk evidence as unmeasured. `--check` verifies current
output; `--ready` additionally requires measured evidence for the final gate.
These counts alone do not establish authorship or necessity.

Report metadata may share descriptions through `groups`: each group has an
explicit `hunks` array of current IDs and a `description` containing only
`type`, `reason`, `molly_equivalent`, and `searched_branches`. Groups may not
claim gate results or overlap. Individual `hunks` entries record `revert_gate`,
`gate_input`, `outcome`, and `evidence` for the exact measured check. Missing
fields remain unmeasured; readiness requires every field, exclusion evidence,
and completed minimization.

For removed changes, `action_chains` records a `start_tree` and ordered
`actions`. Each action names a `path`, tracked one-file `patch`, `input_blob`,
`output_blob`, `gate_input`, `gate`, `outcome`, and `evidence`. The renderer
replays the patches in a private index, checks the blob IDs and single changed
path, and displays the reported gate separately from current-hunk evidence.
The input description must identify the actual tested state; a passing build
cannot establish an individual revert failure. Historical patches must be
committed so replay works in a fresh checkout. Starting trees that a fresh clone lacks are
shipped as one chain of synthetic commits in `report-evidence/action-start-trees.bundle`,
declared by `start_tree_bundle` in the report metadata and imported before
the report is rendered. The clean-merge measurement omits
whole conflicted files, including any clean portions within those files.

## Pinned upstream inputs

The tools refuse inputs that differ from the pinned releases, so their upstream
commits, trees, port directory, fork release and source URLs are compiled in.
`tools/patches/pins.py` lists every such pin with the files that contain it and
the upstream whose next release changes it; `python3 tools/patches/pins.py`
prints the table. `test_pins.py` fails when a tool or workflow contains an
unlisted pin, or when a listed pin is missing from a file it names. GitHub
Action pins are not upstream inputs and are not listed. Data pins in
`molly-build.json` and the series provenance change with each port.

## Updating to new upstream releases

Signal, Molly and the optional fork release on their own schedules. These
runbooks keep the four sources (Signal, the Molly snapshot, the fork and our
AI-assisted compatibility changes) consistent through an update. They use the
mechanism described above; no step is automatic unless it names a tool.

### Choose the inputs first

1. List the candidate Signal release, the latest Molly release with its Signal
   base, and the latest fork release with its Signal base, each as a commit.
2. Supported updates: our own change on the current inputs; a new Signal with
   the same Molly; a new Molly with the same Signal target; a new Molly and a new
   Signal together. When the fork has no release for the target Signal, fork
   profiles stay on their current inputs and are reported as not built for the
   new target. Any other combination is unsupported: stop and ask.
3. Update Molly first, then Signal, then the fork, then our own changes.
4. The contributor approves the input set. That approval covers every pin the
   tools then compute: trees, hashes, bundle prerequisites.
5. `python3 tools/patches/pins.py` lists each compiled pin, its files and the
   upstream that changes it.

### Rebuild rule

A new Molly or Signal changes the mechanical tree, so every checkpoint is
rebuilt, from the initial source commit through the last layer, PR3 included.
Keep a layer's application diff when it still applies and is still needed, but
regenerate its envelope: source commit and parents, bundle and prerequisite,
trees, hashes, and translation and deletion records. Never edit a mailbox only
to refresh offsets, and never edit a hash by hand. Replaying the source commits
is manual; `reexport_current.py` re-exports a series once they exist and does
not create a port or insert layers. Keep the previous series and forest
unchanged until the new chain passes every gate.

### New Signal release

1. `python3 tools/patches/port_impact.py --base NEW_SIGNAL` shows the
   structural impact of the Molly forest on the new base. It does not size merge
   conflicts; the mechanical merge does.
2. Create `patches/ports/signal-NEW/` and its configuration, change the Signal
   pins, then inspect and pin the new mechanical tree.
3. Replay the old layers in order and keep each one's stage: PR3 compatibility,
   PR4 fix, PR5 addition. For a PR3 layer, first look for Molly's own fix of
   the same conflict on its other upstream branches and prefer it. Record the
   Molly equivalent or the search result. Run revert checks where the report
   rules require them; otherwise mark the hunk unmeasured. A failed PR4 or PR5
   layer stays a PR4 or PR5 change and is reviewed as such.
4. Re-pin `target_tree`, regenerate the port report, measure each stage with
   `line_origin.py`, and record its change from the previous port.
5. Build the new stack from the stack-2 baseline (`stack/3-signal-NEW` and up),
   keep the old branches until the new ones pass, then archive them. Do not
   rewrite shared history.

### New Molly release

1. Export the new snapshot with `export_complete.py` into a new forest and
   prove the byte-identical rebuild on a new stack 2. The Molly pins, the
   pipeline anchor and its source-projection checks change together. Keep the
   previous forest unchanged.
2. Rebuild the whole chain under the rebuild rule, whether or not Molly's
   Signal base reaches the port's target.
3. When Molly's Signal base is newer than the target, choose the target with
   the contributor. For each of our layers, record whether Molly now has an
   equivalent; drop layers Molly made unnecessary only after asking.
4. Re-measure; Molly lines not carried restart from the new Molly.

### Our own change

1. Classify it as compatibility (PR3), behavior-preserving fix (PR4) or
   behavior addition (PR5). A change to fork behavior belongs to the fork
   patchset, never the port series. Ask when the class is unclear.
2. Add it at the end of its group (a PR4 fix goes before the PR5 layers) and
   apply the rebuild rule from that point. App tests go in the same layer.
3. Scope changes are tool changes on stack 1, merged upward: a PR4 or PR5 layer
   ID joins `BEHAVIOR` or `ADDITIONS` in `scope.py`; a new PR3 layer's paths
   join `RELEASE_PATHS`, without which the release scope check rejects it.
4. Profile boundaries are named by layer ID; move one only when the new layer
   ends its group.

### Gates and stop points

For app, patch, profile, and build-tool changes, run `preflight.sh proof` for
each affected branch with its scope and base, and `full` for each release
profile in both Firebase modes. For a workflow-only change, compare the candidate
to the exact previously pushed head and verify that every changed path is under
`.github/workflows/`; review and validate the workflow diff without rerunning
unchanged source/profile proofs on each propagated branch. A change to any
other path uses the normal applicable checks. CI runs only after publication is
authorized. Stop and ask before choosing the
input set, changing layer order or profile composition, dropping one of our
layers because Molly has an equivalent, changing behavior to make a replayed
layer apply, or changing a credit mode or author.

## Measure the lines the compatibility changes wrote

The port report opens with one measurement: which lines of each stage's
reconstructed source come from neither of its inputs. `line_origin.py` records
the stage's tree as a synthetic merge whose parents are the released Molly
commit and the new Signal commit, then reads Git's combined diff
(`git diff-tree --cc -M`):

- **written**: lines in no parent (every prefix column is `+`);
- **removed**: lines in every parent but not in the result (every column is `-`).

A line taken unchanged from either input, including either side of a merge
conflict, is not counted, and a path equal to one input is skipped. This makes
conflict side choices and Git's own clean merge free, so the count is the
review surface of the AI-assisted compatibility changes. Synthetic commits go
to a private object directory; the repository is not modified.

Counts are split into fixed classes: production (shipped source, default-locale
resources and build scripts), translations (`res/values-<locale>/strings*.xml`),
tests (test, screenshot-test and benchmark source sets), generated
(`gradle/verification-metadata.xml`) and repository (patches, tools, docs, CI).
Production is the headline. The report also counts **Molly lines not carried**:
Molly's own non-blank lines (old Signal to Molly) missing from the same path.
That count is textual, so rewritten or moved code counts as missing.

Every port checkpoint is measured. Initial layers own the paths their mailboxes
and stage records touch, and split the initial result by path. Each follow-up
row is that follow-up's physical diff from the previous checkpoint (added /
deleted, with renames), so it reads as the lines the follow-up changes. A
stage's total is its last checkpoint, and the stage of a follow-up comes from the scope check's
layer sets.

Fork layers need the fork's head as a third parent, so lines the fork itself
wrote are not counted as ours. Fork layers kept in the port series have no fork
input; their stage row is labelled as including the fork's lines and is not a
measure of our changes. When `molly-build.json` declares named release
profiles, the report adds one row per profile: profiles with fork layers load
the fork's upstream bundle into the private object directory and use its head
as the third parent, and **from the fork** counts the production lines the fork
wrote that the profile carries. With `--config`, the command verifies source bundles
and prepares profile trees first; this requires writable Git objects and checkpoint
refs in the target repository. The measurements themselves use a private object
directory.

```sh
python3 tools/patches/line_origin.py port --markdown [--config molly-build.json]
python3 tools/patches/line_origin.py tree TREE --parent MOLLY --parent SIGNAL \
  [--parent FORK_HEAD] [--molly-old OLD_SIGNAL MOLLY]
```

Comparisons with the mechanical tree, patch-file sizes and whole-file deletion
totals remain in the report as raw physical diffs. They include conflict
markers and bookkeeping and are not a measure of the compatibility changes.

## Re-export an existing current-format series

After replaying the source commits separately, use `reexport_current.py` to
produce an isolated candidate. It does not choose changes or replay source code.
The output directory must not exist; it contains a Git repository for validation
and only the profile, resolution series, provenance and checkpoint bundles, so
review these artifacts as an overlay on the existing release tree.

```sh
python3 tools/patches/reexport_current.py --repo /path/to/release \
  --output /path/to/new-candidate \
  --checkpoint OLD_FULL_COMMIT=NEW_FULL_COMMIT \
  --checkpoint OLD_FOLLOWUP_COMMIT=NEW_FOLLOWUP_COMMIT \
  --replacement initial/example/0001-example.patch=/path/to/replayed.patch
```

Repeat the checkpoint and replacement options as needed. Checkpoint keys are
source commit IDs in the existing provenance; omitted IDs stay unchanged.
Replacement keys are paths relative to the series directory. Supply changed
initial and unbundled follow-up mailboxes explicitly; their diff bytes are retained.
Bundled follow-up mailboxes use `git format-patch --stdout --abbrev=8 -1`
for the specified commit. Their parent trailer is added or normalized to the
actual parent, followed immediately by the source-commit trailer; other message
and diff bytes come from Git. If supplied, a bundled replacement must match
the generated mailbox exactly. The command checks the old
series and bundle chain first, then validates generated deletion and translation
preimages and output, reconstructed trees, commit parents and new bundles. It
fails if a later source checkpoint was not replayed onto its new parent. It
leaves the input repository and its index untouched.

Initial and unbundled follow-up `mailbox_bytes` and `paths` are historical
pre-conversion export metadata and remain unchanged. Bundled follow-up
`mailbox_bytes` records the current mailbox size. Candidate verification does
not replace the applicable branch preflight, port report update or build tests.

## Named release profiles and fork checkpoints

`molly-build.json` may contain a `profiles` object keyed by `molly-minimal`,
`molly-continued`, `molly-foss-continued`, `collie`, and `collie-foss`. Each
entry contains exactly `base_series` (the port series path), `base_checkpoint`
(full source commit), `base_tree` and `target_tree` (full tree IDs),
`fork_series` (ordered repository-relative series paths) and `firebase_free`
(boolean). Pins are supplied by the release configuration, not compiled into
the tools. A profile's tests are derived, not listed: they are `android.tests`
plus every unit test class that a selected fork layer's mailbox adds or
changes (`<module>/src/test/{java,kotlin}/...`), added to the filters of that
module's existing test task. A task that already runs unfiltered is left as
is; a fork test in a module without a test task, or an unsupported test source, is an error.
Instrumented sources are recorded separately for the device test gate. The derived tests and Firebase selection
are part of the source proof fingerprint.

All five profiles select the verified port prefix ending at
`followups/view-once-open-guard`, followed by the same six fork layers:
`firebase-free-build`, `collie-icon`, `collie-branding`, `collie-visuals`,
`restore-picker`, and `notification-default`. The identity layers provide
Collie launcher artwork, app identity and visual styling. The restore picker
recognizes timestamped backups in a selected folder, and the final layer changes
the fallback for an unset notification-content preference.
For profile-specific source proofs, the two FOSS profiles set the Firebase-free
build flag; the other three retain FCM-capable build configuration. The
single-APK release mode described below uses one FCM-capable profile. All use the `im.collie.app` package and keep
their existing profile-specific backup file names (`release_profiles.APP`).
The former retention-control layers are not selected.

Fork profiles also require `fork_source` in `molly-build.json` with the original
fork `url` and `head` pins. The independent `patches/forks/signal-foss/provenance.json` records schema 1,
source URL and head, base commit and tree, and ordered `layers`. It also records
`upstream_bundle`, `upstream_bundle_sha256`, `upstream_bundle_bytes`,
`upstream_bundle_prerequisite`, and `upstream_bundle_ref`; the declared ref is
`refs/heads/upstream-fork/signal-foss-8.33.2.0-FOSS`, its commit is the fork
head, and its prerequisite is the pinned Signal source. The bundle supplies the
original fork commits for provenance checks only; its workflow is never applied. Each layer
records `id`, `series`, `mailbox`, `mailbox_sha256`, `mailbox_bytes`,
`source_commit`, `before_tree`, `after_tree`, `source_bundle`,
`source_bundle_sha256`, `source_bundle_bytes`, `source_bundle_prerequisite`,
`source_bundle_ref`, `upstream_commit`, `upstream_author`, `author`, and
`upstream_credit`. `author` is the adapted commit's author. An `adapted` layer
is mostly the fork's code and keeps the upstream author. An `inspired` layer is
our own code that follows a fork commit: we are its author, and its message
carries `Inspired-by: <upstream_author>` and names the upstream commit. An
`ours` layer holds our fixes to the `adapted` layer directly before it, named by
its `fixes` field; it has no upstream commit or author and is never authored as
the fork author. File
paths in this document are relative to the fork root. Each series contains one path to
its layer's mailbox, relative to that series file. Checkpoint bundles must
advertise the declared `refs/heads/source-checkpoint/` ref and require exactly
the previous source commit. The selected base checkpoint must match the
verified port layer. Replay checks the resulting tree after each fork layer.

Use `release.py --profile NAME` for a profile-bound source proof, then pass the
same `--profile NAME` to every `build.py` phase, or use
`preflight.sh full --scope forks --base stack/5-behavior-additions --profile NAME`.
Named builds explicitly pass the selected `mollyFirebaseFree` property.
Omitting `--profile` preserves the existing workflow. The `forks` scope allows
only the independent fork artifacts and named configuration profiles, and
requires the port tree and original base configuration to remain unchanged,
apart from adding `profiles` and `fork_source`.
The existing Molly CI workflow does not yet select the named product matrix.

### New fork release

The fork patchset and its tools exist only on the private release line.

1. Check the new fork head's ancestry and history shape first. `fork_profiles.py`
   rejects a retention origin that does not descend from the pinned Signal and
   expects the current two-commit upstream history; anything else is
   unsupported until the tool changes.
2. Create the upstream bundle (prerequisite: the pinned Signal commit), then
   change `fork_source.head`, the bundle ref name and the provenance.
3. Import the fork's changes: diff each new upstream commit against the
   previous one and carry the changes into our layer. Separately, adapt a layer
   where a changed Molly or Signal base, or one of our own fork-behavior fixes,
   requires it. Replay both layers in order and keep the recorded behavior
   decisions.
4. Credit comes from the source commits. A layer that carries the fork's code
   is `adapted` and keeps the fork author. A layer that only follows the fork's
   idea is `inspired`: we are its author and its message carries
   `Inspired-by: <author>` and names the upstream commit. `line_origin.py`
   counts are supporting evidence only. Keep every credit the upstream commit
   carries, and get the contributor's confirmation before changing a credit mode
   or author.
5. Fixes we make to an `adapted` layer go in a separate layer that we author,
   placed directly after it, so the fork author's commit stays theirs. Fixes to
   an `inspired` layer go into that layer. A new fork layer, whether a fix or a
   new feature, needs the contributor's approval and changes
   `scope.allowed('forks', …)`, the `fork_profiles.py` layer order,
   `release_profiles.POLICY` and `pins.LAYER_NAMES`.
6. Gates: the `forks` scope proof, all five profile proofs, and `full` for Collie
   (Firebase on) and the FOSS profiles (Firebase off).

## Local and optional hosted instrumentation

Run instrumentation locally using the real debug app and test APKs built from
the reconstructed source, on a disposable emulator with synthetic data. Select
the exact release-line commit first; a test on another commit is not evidence
for it. The selected commit must configure the `collie` and `collie-foss`
profiles. You need JDK 21, the configured Android SDK/build tools, platform
tools, the emulator and an API 35 `google_apis;x86_64` system image. Use a
scratch directory outside the checkout with space for the source, build and
emulator. The release source destination must not already exist.

For each profile (`collie`, then `collie-foss`), reconstruct the exact source
and run the same default and IMO gate used by CI. Set `SCRATCH`, `PROFILE`, and
`ANDROID_HOME` for your environment; the base below is for the 8.33 release
line, so use the reviewed base and scope for a later port:

```sh
(
set -euo pipefail
: "${SCRATCH:?Set SCRATCH to a new scratch directory}" "${ANDROID_HOME:?Set ANDROID_HOME}"
PROFILE=${PROFILE:-collie}
case "$PROFILE" in collie|collie-foss) ;; *) echo 'Invalid profile' >&2; exit 1 ;; esac
mkdir -p "$SCRATCH"
git fetch --no-tags https://github.com/0cwa/collieim-android.git refs/heads/private/release-base-5
BASE=$(git rev-parse FETCH_HEAD)
git fetch --no-tags https://github.com/mollyim/mollyim-android.git 0e202dda90785771ff8c87526eaaf30c655f54f3
python3 tools/patches/fetch_reviewed_profiles.py https://github.com/0cwa/collieim-android.git
python3 tools/patches/prepare_port_report.py
python3 tools/patches/release.py --repo . --config molly-build.json \
  --worktree "$SCRATCH/source-$PROFILE" --report "$SCRATCH/$PROFILE/source.json" \
  --scope forks --base "$BASE" --profile "$PROFILE"
for phase in native-prepare native-build native-integrate sdk; do
  python3 tools/patches/build.py "$phase" --worktree "$SCRATCH/source-$PROFILE" \
    --report-dir "$SCRATCH/$PROFILE" --profile "$PROFILE"
done
export PATH="$ANDROID_HOME/platform-tools:$PATH"
export ANDROID_AVD_HOME="$SCRATCH/avd"
mkdir -p "$ANDROID_AVD_HOME"
source tools/patches/emulator_startup.sh
check_avd_disk_space
echo no | "$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" create avd \
  -n "collie-$PROFILE" -k 'system-images;android-35;google_apis;x86_64'
"$ANDROID_HOME/emulator/emulator" -avd "collie-$PROFILE" -port 5582 \
  -no-window -no-audio -no-boot-anim -gpu swangle &
EMULATOR_PID=$!
trap 'kill "$EMULATOR_PID" 2>/dev/null || true; wait "$EMULATOR_PID" 2>/dev/null || true' EXIT
export ANDROID_SERIAL=emulator-5582
wait_for_emulator_boot "$EMULATOR_PID"
tools/patches/instrumentation_gate.sh "$SCRATCH/source-$PROFILE" "$SCRATCH/$PROFILE" "$PROFILE"
)
```

Choose a free emulator port. The exit trap stops the emulator on failure too.
Repeat with `PROFILE=collie-foss` and a fresh source destination. Preserve the gate's
logs and results, and record the commit, profile, test counts, failures and
commands. The gate uses the debug keystore and creates test APKs; do not ship
them. On a shared machine, run resource-intensive commands with your local
resource limits and remove task-created emulator and build directories afterward.

Hosted runs are optional: dispatch **Molly patch release** or **Build unsigned
release**, select the reviewed branch and set `instrumentation` to `both`,
`collie` or `collie-foss`. The default `off` does not start an emulator;
ordinary PR runs also skip hosted instrumentation. The patch workflow's existing
scope rules still apply to the selected branch. A requested run reports test
failure normally and uploads its diagnostics. Local tests and optional hosted
tests do not block unsigned APK collection; disclose failures and missing runs.

## Publishing a release

Current privileged collection/signing/publication requires an explicit independently
approved release plan. Follow [the runtime adapter](migration/RUNTIME-RECEIPTS.md)
to prepare a `legacy` plan for this historical route or the approved source/build
receipt graph for `new-source`. Pass `--release-plan` and
`--approved-release-plan-sha256` to the commands below; missing receipts never
select legacy. Privileged signing/publication also retain the durable version
ledger and independently authenticated current release inventory described in
[version reservation](migration/CONTRACTS.md#version-reservation-and-local-operator-migration). Historical rehearsal/dry-run remains available without production
authorization, but cannot admit new-source evidence. The historical five-profile
hosted instructions below are not the current production recipe.

For the Signal 8.35.1 single-Collie release, use the reviewed local build path:
reconstruct and prove the `collie` profile, stage the pinned native artifacts,
and build one unsigned release APK with the explicit trusted-local native option.
This uses the local builder and caches; it is not an independent reproducibility
claim. Run `release_collect.py --single-collie` with that file named
`unsigned/collie.apk`. The resulting manifest identifies `collie` as the sole
source profile and records the five compatibility filenames as aliases. The
signer checks and signs the one input once, copies the signed bytes to all five
names, and verifies each copy. The publisher verifies identical hashes and the
signed-to-unsigned provenance before creating a draft. The commit-bound
instrumentation assessment described below remains required for signing.

All five aliases contain the same FCM-capable Collie APK, including filenames
that formerly denoted FOSS variants. This path does not migrate notification
preferences or legacy backup names; users must be told about those differences.
The hosted five-profile workflow below is retained for historical releases and
remains blocked for the 8.35.1 port.

This flow builds five unsigned APKs on CI and signs them offline. The additional
`.github/workflows/molly-unsigned-release.yml` is a new Signal-tree workflow
exception and requires contributor approval before propagation. The release
line must already contain the shared `im.collie.app` profile change. The flow
must be rehearsed and a real release build checked before production use.

Create the `unsigned-release` GitHub environment and protect it with required
reviewers and restricted deployment branches. Protect the release tag pattern
with a repository ruleset, limit who can create tags and releases, and enable
immutable releases in repository settings. Use a separate credential with only
the required repository release/tag permissions for publishing; the build uses
read-only repository permission and no signing secrets. The immutable-release
setting applies when a draft is published; check the current repository settings
before every release.

Keep the release keystore offline. Record the certificate SHA-256 through a
separate trusted channel. Store encrypted backups of the key and recovery
instructions on separate media, and periodically rehearse restoring the backup
and checking its certificate without using it to sign a release. Never place
keys or passwords in the repository or CI. Key access is supplied to the signer
as `MOLLY_KEYSTORE`, `MOLLY_KEY_ALIAS`, and, optionally,
`MOLLY_KEYSTORE_PASSWORD` and `MOLLY_KEY_PASSWORD`; absent passwords are prompted.

1. Approve the full release-line commit, version name and a version code higher
   than every previously published code, and the expected certificate digest.
   The validator rejects Collie codes at or below the verified RC3 code
   `177203`. This fixed floor does not check later releases: compare against
   all published releases, including prereleases, before approving a code.
   Dispatch **Build unsigned release** on that commit with these inputs. The
   dispatch requires the workflow revision and selected commit to match. Review
   the environment approval and wait for all five source proofs, assemblies and
   regression tests to pass. Hosted default and IMO instrumentation is optional
   and independent of APK collection; choose `both`, `collie` or `collie-foss`
   in the `instrumentation` dispatch input to run it. The default is `off`.
   Record and disclose any failure or incomplete run. The workflow checks each APK's
   package, version and unsigned state. Record the run ID and attempt.

   When building a release locally instead, run and inspect both instrumentation
   suites for both reconstructed profiles from the exact commit to be signed when
   feasible, without treating their results as a release blocker,
   including all selected theme layers. Record the commit, profile, test counts,
   ignored tests, failures and commands before signing. A run on an earlier PR
   head is not evidence for a later release commit. Use a disposable emulator
   and synthetic data; never distribute the IMO test APK.
2. From the authenticated GitHub run page, record the SHA-256 digest of the
   `unsigned-release-<run>-<attempt>` artifact. Download the artifact ZIP over
   an authenticated connection and verify its SHA-256 against that separately
   recorded digest **before** extracting it. Calculate SHA-256 of the extracted
   `manifest.json` and carry that digest, the commit, run ID and certificate
   fingerprint to the offline signing machine through a trusted channel. Review
   the manifest, including workflow revision, pins and all five APK hashes.
   If a GitHub provenance attestation is available, the signer can additionally
   verify it using `--attestation-repo OWNER/REPO`; configure gh's trusted root
   and any offline verification material beforehand. The current workflow does
   not itself create attestations, so the authenticated artifact digest is
   required.
3. On the offline signing machine, prepare an `instrumentation.json` checklist
   for the exact approved commit. Set `status` to `passed`, `failed`, or
   `not run`. For `passed`, supply evidence identifying the tested profiles,
   suites and results. For `failed` or `not run`, supply the maintainer's reason
   for accepting the gap; include evidence if available. The signer displays
   this maintainer-provided record before signing and copies it into the final
   manifest. It does not independently verify the evidence. Do not include
   secrets or private user data in this record, which may be published.
   The publisher also checks this record against the approved commit before
   creating or publishing a draft. Already-published historical releases are
   unaffected. An older draft whose final manifest lacks the record must be
   replaced with newly generated, signed artifacts and manifest; do not alter
   existing immutable release assets.

   ```json
   {"commit":"FULL_APPROVED_COMMIT_SHA", "status":"not run", "evidence":"", "reason":"Document the accepted test gap"}
   ```

   Use the manifest's pinned Android SDK build-tools directory and run:

   ```sh
   python3 tools/patches/release_sign.py \
     --manifest unsigned/manifest.json --manifest-sha256 MANIFEST_SHA256 \
     --approved-commit COMMIT --approved-run RUN_ID --certificate-sha256 CERT_SHA256 \
     --apks unsigned --output signed --build-tools "$ANDROID_HOME/build-tools/VERSION" --scratch SCRATCH \
     --instrumentation-checklist instrumentation.json \
     --release-plan legacy-plan.json --approved-release-plan-sha256 PLAN_SHA256 \
     --version-ledger VERSION_LEDGER --published-inventory VERIFIED_INVENTORY \
     --published-inventory-sha256 INVENTORY_SHA256
   ```

   For an existing local wrapper that does not forward this flag, explicitly
   set `COLLIE_INSTRUMENTATION_CHECKLIST` to the same JSON file before starting
   the wrapper. The command-line flag takes precedence if both are supplied.
   A missing record is never treated as a passed run.

   The output directory must not already exist. The signer verifies hashes and
   metadata, aligns and signs the APKs, checks the certificate and v2/v3
   signatures, and records the mapping in `signed/final-manifest.json`. Use a
   private scratch directory and add `--rehearse` to exercise
   the signer with a disposable key. Rehearsal output is marked and rejected by
   the publishing tool; never use that output for a real release.
4. Transfer the signed directory and the original manifest as provenance to the
   publishing machine. Review and independently approve the SHA-256 of
   `signed/final-manifest.json`. Supply a release notes file, an existing private
   scratch directory, and the separate publishing credential. Preview with
   `--dry-run`; the following `draft` command creates a tag at the approved
   commit as a signed annotated SSH tag, requires GitHub to verify its signature,
   creates a draft and uploads the APKs, checksums, final manifest,
   provenance and notes. The draft check and the pre-publication check verify
   the exact GitHub asset IDs, names, sizes and server-computed SHA-256 digests.
   If digest metadata is absent, unsupported or malformed, the publisher falls
   back to downloading and hashing the assets. After publication it downloads
   every asset and compares its bytes with the approved digests.

   ```sh
   python3 tools/patches/release_publish.py draft --repo OWNER/REPO --tag vVERSION \
     --approved-commit COMMIT --final-manifest signed/final-manifest.json \
     --final-manifest-sha256 FINAL_MANIFEST_SHA256 --provenance unsigned/manifest.json \
     --notes RELEASE_NOTES --scratch SCRATCH --signing-key SSH_PRIVATE_KEY \
     --tagger-name NAME --tagger-email VERIFIED_GITHUB_EMAIL \
     --ssh-askpass OPTIONAL_KEYCHAIN_HELPER --dry-run \
     --release-plan legacy-plan.json --approved-release-plan-sha256 PLAN_SHA256 \
     --version-ledger VERSION_LEDGER --published-inventory VERIFIED_INVENTORY \
     --published-inventory-sha256 INVENTORY_SHA256
   ```

   Supply `--ssh-askpass` when an askpass helper is needed; it is optional.
   The tagger email must be verified on GitHub. Signing options are required
   only for `draft`; `publish` verifies the existing signed tag again.
   For alpha or beta builds, add `--prerelease` to both `draft` and `publish`;
   the tool marks the draft as a prerelease and verifies that setting before
   publishing. Remove `--dry-run` only when ready to create the tag and draft. A failed
   attempt may leave a tag or draft behind; inspect and resolve that state
   explicitly. Publishing is a separate command with the same arguments, using
   `publish` in place of `draft` (without the signing options). It rechecks
   the signed tag, draft and exact asset metadata immediately before publishing.
   The final full-byte readback is required after the release is public. Add
   `--phase-report FILE` to write a separate, exclusive mode-0600 JSON report of
   publisher phase durations and asset-file byte counts; it contains no CLI
   arguments or environment values and is not uploaded with the release.

If a published immutable release is bad, issue a new tag with a higher version
code. Before shipping the replacement, check that database migrations and data
written by the bad version are compatible with the replacement. Do not rely on
an Android downgrade as recovery.

Signal 8.35.1 release assembly is deliberately blocked in `tools/patches/build.py` until the native integration and complete port report have been reviewed. Debug assembly and non-release tests remain available. The block applies to every profile and scope through that build driver; direct Gradle commands (including the inherited Docker reproducibility workflow) do not use the driver and must not be treated as approved release artifacts. Removing the block requires a reviewed change that establishes the native and report evidence.

A Signal 8.35.1 candidate PR targets `private/release-variants` at the pinned `7d486880ddc991af0d36f1e4709030255ef4dbad` base. Its patch workflow selects `next-port`, checks the pinned profile composition and reconstructs all five products. Android build, native preparation and instrumentation jobs remain skipped until native integration is ready. Other PRs keep their existing scope routes. This route does not authorize release assembly; the 8.35.1 release stop above still applies. Moving the candidate to `main` requires a separately reviewed ancestry and projection plan.
