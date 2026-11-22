# Selected upstream sources

Collie merges Signal and Molly upstream history and reviews individual features
from other public forks. [`sources.json`](sources.json) records these source
routes and their evidence locations. It is a review index, not a build input or
an inventory of current app features. Application code continues to land in
ordinary PRs to
[`0cwa/collieim-android`](https://github.com/0cwa/collieim-android), as described in
the [development cycle](../docs/DEVELOPMENT-CYCLE.md).

| Source | Route | Update boundary |
| --- | --- | --- |
| [Signal](https://github.com/signalapp/Signal-Android) | `upstream-merge` | Merge an explicitly selected Signal tag; the verified upstream parent is recorded in app history. |
| [Molly](https://github.com/mollyim/mollyim-android) | `upstream-merge` | Merge an explicitly selected Molly branch at a resolved commit; the branch and verified upstream parent are recorded in app history. |
| [Johan's fork](https://github.com/johanw666/Signal-Android) | `selective-feature` | Review custom `*-JW` snapshots, with the corresponding Signal base pinned separately, and port individual features. |
| [Signal-FOSS](https://github.com/tw-hx/Signal-Android) | `historical-only` | Preserve the archived snapshot and `patches/forks/signal-foss/provenance.json`; no new discovery or import route. |

## Source-route contract

Registry schema 2 keeps the existing source IDs. Every entry has `id`,
`repository`, `route` and `discovery_ref`. The latter is one ref pattern for
finding candidates, or `null` for historical sources; it never selects the exact
revision for an operation. The route determines the remaining fields:

| Route | Required evidence | Incompatible fields |
| --- | --- | --- |
| `upstream-merge` | `provenance` points to the app's upstream-merge verification contract. | `snapshot`, `ledger`; no handwritten applied-source pin. |
| `selective-feature` | `snapshot` has `ref`, full `commit`, `ref_origin: upstream`, and a separately pinned `signal_base` (`ref`, full `commit`); `ledger` is relative to this directory. | `provenance`; no archived-bundle snapshot. |
| `historical-only` | `discovery_ref: null`, the original `provenance` path, and an exact `snapshot` with `ref_origin: archived-bundle`. | `ledger`, `snapshot.signal_base`; no active discovery. |

This replaces schema 1's `status`, `strategy`, `tracking` and ambiguous `baseline`
fields. Historical snapshot pins remain unchanged. An archived-bundle ref names
the preserved bundle's ref, not a ref to look up in the live upstream repository.

### Signal and Molly operations

Use the app's `tooling/upstream/merge-upstream.py` and
[verified upstream merge provenance](https://github.com/0cwa/collieim-android/blob/main/docs/VERIFY.md#verify-an-upstream-merge).
The signed merge has two parents, Collie first and the exact upstream commit
second, with `Layer: upgrade` and an `Upstream:` trailer. The app's
[`collie_check_layer_trailers.py`](https://github.com/0cwa/collieim-android/blob/main/tooling/git-hooks/collie_check_layer_trailers.py)
checks that parent against the upstream repository. App history remains the
authority; this registry does not duplicate its applied revisions.

- Signal's `refs/tags/v*` discovers releases. Select a specific tag, resolve its
  commit (peeling an annotated tag), and verify that it is the merge's second
  parent. The trailer is `Upstream: signalapp/Signal-Android <tag>`.
- Molly's `refs/heads/*` discovers branches. Choose the appropriate branch for
  each operation after inspecting its source and Collie's existing merge
  history, then resolve its full commit SHA. The trailer is
  `Upstream: mollyim/mollyim-android <branch>@<full-sha>`; the app check verifies
  both the second-parent identity and that the commit belongs to that branch.
  [The merge of `molly-8.34`](https://github.com/0cwa/collieim-android/commit/5f450a992a71cc3e89a1671d47a01a81f4288b7b)
  is historical evidence of that selection. Neither `main` nor `molly-8.34` is a
  permanent default for future operations.

Discovery refs can move; an operation's selected revision must be exact.
Preserve recorded provenance rather than refreshing old pins to today's heads.

## Johan source selection

The initial review pins tag `v8.36.0.0-JW` to commit
`8f4ad8577344e844945ee8e73d95fd1fcc7208bc`, with clean Signal `v8.36.0`
at `b8544a813786e2442f145735e500e78df6a33014`. Use the customized `-JW`
snapshot: the fork's `main` branch follows the clean Signal source and does not
represent the full custom release. Neither the branch name nor the tag name
alone is an immutable input.

The [review](johanw666/REVIEW.md) and
[feature review ledger](johanw666/review.json) describe decisions at that
snapshot. Its scope is selective feature review, not a security assessment of
every reachable commit. Review progress and historical app integration are
separate: neither a reviewed head nor a past merge proves present inclusion.

Keep independent features in independent app PRs. Stack PRs only when one needs
another's implementation. Each feature gets a stable `Layer:` value so its
commits can be found together and exported with `git format-patch` when needed.
Do not add the app changes to the historical `molly-build.json`, patch composer
or generated source publication chain.

## Reviewing the next release

1. Find a custom `*-JW` release and record its tag and full commit SHA. Fetch it
   into a distinct local ref without moving app branches. Fetch its clean Signal
   base separately. Resolve both refs to commits and compare with the proposed
   pins before reviewing the diff. A changed tag must be reviewed explicitly;
   do not silently refresh its SHA.
2. Compare the new custom snapshot with its own Signal base. Also compare the
   previous and new custom deltas: a raw old-to-new fork diff contains Signal
   upgrades and is not by itself a list of custom features. Do not assume every
   future custom release is one commit or has its clean base as its only parent.
3. Read each candidate in context, including settings, persistence, existing
   tests and changed security behavior. Check current Collie and Molly first
   for equivalent implementations. Avoid importing fork branding, build identity,
   update servers, credentials, signing configuration or unrelated features.
4. Port each selected feature onto its chosen current app target branch. Record
   the original feature commits, even when a release snapshot is also needed to explain its
   current implementation. Preserve source copyright and license notices. Mark
   direct copies, adaptations and independent inspired implementations honestly.
5. Open focused app PRs with the benefit, behavior, provenance and relevant
   checks. Record their URLs in the selected feature's `application.pr`. Run the
   app checks required by the development cycle; source review alone is not
   compilation or device evidence.
6. Update the engineering ledger through a PR. After an app PR merges, verify
   its actual target branch and merge commit and append an `integration_history`
   entry with the source snapshot and attribution used for that integration.
   Rejected and deferred candidates keep their reasons. Advance `reviewed_head`
   only with the completed review scope, independently of integration history.
   Preserve earlier historical entries even when a later review changes its
   decision, source snapshot or implementation plan.

For the pinned initial snapshot, the source comparison is:

```sh
git fetch --no-tags https://github.com/johanw666/Signal-Android.git \
  refs/tags/v8.36.0.0-JW:refs/upstream-review/johan/8.36.0.0-JW
git fetch --no-tags https://github.com/signalapp/Signal-Android.git \
  refs/tags/v8.36.0:refs/upstream-review/signal/v8.36.0
git rev-parse refs/upstream-review/johan/8.36.0.0-JW^{commit}
git rev-parse refs/upstream-review/signal/v8.36.0^{commit}
git diff --stat b8544a813786e2442f145735e500e78df6a33014 \
  8f4ad8577344e844945ee8e73d95fd1fcc7208bc
```

Compare the printed commits with `sources.json` before using this diff. A later
review should preserve the earlier ledger in Git history and use new local refs.
Never merge the custom release wholesale into Collie.

## Review ledger and integration history

Ledger schema 2 uses `status` for the decision at `reviewed_head` within `scope`:
`selected`, `deferred`, `rejected` or `already-present`. The reviewed head and
date describe review progress, not an app baseline. `already-present` is an
observation made during that review, not a continuing claim about current code.

Only `selected` entries carry planning metadata: `integration` is `direct`,
`adapted` or `inspired`, and `application` has a `layer` and optional `pr` URL.
That URL associates an app proposal; it does not assert that the PR is currently
open or merged. Schema 2 replaces the former `proposed`/`adopted` distinction
with a review decision and separate historical events.

Any decision may have a nonempty `integration_history` list. Each event contains
exactly these fields:

| Field | Meaning |
| --- | --- |
| `pr` | The merged Collie app PR URL. |
| `target_branch` | Its actual target as a concrete short branch name, such as `main` or `trailblazer-beta`. |
| `merge_commit` | Full commit SHA reported for that merge. |
| `source_head` | Full SHA of the reviewed source snapshot used for that integration, not a claim about where the feature was first introduced. |
| `layer` | The application layer used for that integration. |
| `integration` | Its `direct`, `adapted` or `inspired` relationship to the source. |

Historical entries retain their own source, layer, category and PR even if the
latest review or proposal changes. They need not match the latest `reviewed_head`
or planning metadata. Recording a merge does not complete another source review,
establish a passing build, or prove that a feature still exists on any branch.
Inspect the chosen app target and its current code for present inclusion; this
ledger does not replace that evidence with an engineering adoption database.

## Attribution and commit boundaries

Each app commit has one `Layer:` trailer. Use `Upstream:` for copied/adapted
changes, naming the source repository and full original commit. Preserve upstream
authorship for direct cherry-picks. When a port is authored as a Collie change,
credit the original contributor with `Co-authored-by:` and retain their notices.
Use `Inspired-by:` to distinguish a new implementation based on a feature idea
from an imported code change. AI-assisted commits also carry
`Assisted by: ChatGPT` as a separate paragraph before the final Git trailers.
The space in `Assisted by` prevents Git from treating that line as a trailer;
putting it in the final trailer paragraph can hide `Layer:` from the app check.

For example, an adapted feature's trailers are:

```text
Assisted by: ChatGPT

Layer: <feature-id>
Upstream: johanw666/Signal-Android <full-original-commit>
Co-authored-by: <original contributor name and email from that commit>
```

The exact source identity comes from Git metadata; never invent an author address.
PR descriptions begin with a concise disclosure that ChatGPT prepared the PR,
then explain the benefit and evidence in ordinary language.

## Registry checks

```sh
python3 tools/upstreams/validate.py
python3 -m unittest discover -s tests -p 'test_upstream_sources.py' -v
```

The validator is offline and does not fetch, check out or apply code. It checks
unique sources and features, route-compatible fields, exact SHA syntax, ref
selection, archive semantics, ledger/source agreement and the shape of historical
integration records, including target branches. Existing engineering test
discovery runs the ledger check on each PR. It does not
authenticate remote tags, prove merge state or validate the feature code; those
claims require the source comparison and app PR evidence described above. It
also cannot prove that historical records have been preserved across edits;
review those changes against the earlier ledger. Keep remote evidence gathering
separate from this offline validation.
