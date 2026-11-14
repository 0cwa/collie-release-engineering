# Selected upstream sources

Collie takes its application base from Signal and selected changes from other
public forks. [`sources.json`](sources.json) records the source roles and review
locations. It is a review index, not a build input or permission to import a whole
fork. Application code continues to land in ordinary PRs to
[`0cwa/collieim-android`](https://github.com/0cwa/collieim-android), as described in
the [development cycle](../docs/DEVELOPMENT-CYCLE.md).

| Source | Role | Update boundary |
| --- | --- | --- |
| [Signal](https://github.com/signalapp/Signal-Android) | Application base | Tag-to-tag diff; the applied version remains `canonicalVersionName` in the app. |
| [Molly](https://github.com/mollyim/mollyim-android) | Selected app changes | Fork-owned commits after the last taken commit in the development cycle. |
| [Johan's fork](https://github.com/johanw666/Signal-Android) | Selected app changes | Reviewed `*-JW` release snapshots, with the corresponding Signal base pinned separately. |
| [Signal-FOSS](https://github.com/tw-hx/Signal-Android) | Historical attribution | The original reviewed snapshot remains in `patches/forks/signal-foss/provenance.json`; no new update tracking is enabled here. |

Signal and Molly already have authoritative baseline records. This index points
to those records instead of creating competing version pins. Historical bundle
refs are explicitly distinguished from refs that exist in upstream repositories.

## Johan source selection

The initial review pins tag `v8.36.0.0-JW` to commit
`8f4ad8577344e844945ee8e73d95fd1fcc7208bc`, with clean Signal `v8.36.0`
at `b8544a813786e2442f145735e500e78df6a33014`. Use the customized `-JW`
snapshot: the fork's `main` branch follows the clean Signal source and does not
represent the full custom release. Neither the branch name nor the tag name
alone is an immutable input.

The [review](johanw666/REVIEW.md) and [feature review ledger](johanw666/review.json) describe decisions at that
snapshot. Its scope is selective feature review, not a security assessment of
every reachable commit. A reviewed source head is also not an applied app
baseline: only an `adopted` feature records a merged Collie commit.

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
4. Port each selected feature onto current app `main`. Record the original
   feature commits, even when a release snapshot is also needed to explain its
   current implementation. Preserve source copyright and license notices. Mark
   direct copies, adaptations and independent inspired implementations honestly.
5. Open focused app PRs with the benefit, behavior, provenance and relevant
   checks. Record their URLs as `proposed`. Run the app checks required by the
   development cycle; source review alone is not compilation or device evidence.
6. Update the engineering ledger through a PR. After an app PR merges, change
   its feature to `adopted` and record the actual app commit. Rejected and deferred
   candidates keep their reasons so the next review does not repeat the same
   decision. Advance the reviewed head only with the completed review scope.

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
unique sources and features, exact SHA syntax, ref selection, historical/active
tracking consistency, ledger/source agreement and adoption records. Existing
engineering test discovery runs the ledger check on each PR. It does not
authenticate remote tags, prove merge state or validate the feature code; those
claims require the source comparison and app PR evidence described above.

Ledger states are `proposed`, `adopted`, `deferred`, `rejected` and
`already-present`. Only proposed/adopted entries have an application layer and
integration category. Proposed entries can link their app PR but cannot claim a
merged commit; adopted entries need both a Collie app PR URL and full commit SHA.
