# Collie development cycle

Decided 2026-11-10. Where this conflicts with older docs here or with app [`docs/security/VERIFY.md`](https://github.com/0cwa/collieim-android/blob/main/docs/security/VERIFY.md), this file wins.

## Day-to-day work

- App `main` (`0cwa/collieim-android`) is the source. Change app code directly in ordinary PRs; do not re-import app changes as engineering layers.
- Maintainer app commits carry a `Layer: <feature>` trailer where required by the app's PR checks. External contributors can submit PRs without `Layer:` trailers or signing keys. Maintainers can squash-merge them with a signed landed commit. See the app's [`.github/CONTRIBUTING.md`](https://github.com/0cwa/collieim-android/blob/main/.github/CONTRIBUTING.md).
- The app workflow runs fast lint, unit tests, an unsigned production build and the aggregate `Security readiness` check.
- `main` in both repos takes PRs with green required checks and signed commits, with force pushes disabled. Agents self-merge routine app code PRs once the evidence below is complete.
- CI, workflow, release and build-input changes covered by [`.github/CODEOWNERS`](../.github/CODEOWNERS) require maintainer review.
- The app's path-based `tooling/features.toml` and `tooling/ci/feature_diff.py` report Collie's feature differences from Molly. [Feature map](https://github.com/0cwa/collieim-android/blob/main/tooling/features.toml) · [Diff tool](https://github.com/0cwa/collieim-android/blob/main/tooling/ci/feature_diff.py)

## Merging PRs

When an agent merges a PR, it needs required checks plus each line below that the PR's changes match, and nothing more. A job the PR itself breaks gets fixed first. Upgrade PRs follow "Upstream upgrades".

- CI, tooling and tests: the job the PR changes or that runs the changed tests, green on the PR head (if that job runs on PRs).
- App code, Gradle files and dependency versions: `Unit tests` green on the PR head.
- UI: the API 35 screenshot from app `AGENTS.md`.
- Keys, signing, permissions, updater, backup and database migrations, encrypted push, the proxy/network path, release or publishing workflows, workflow `permissions:`, secrets or `pull_request_target`: the PR's one review is the security review (`$sol-loop` in Codex).

Document PR implementation and review evidence in the GitHub PR description or comments, not under `docs/`. Do not commit screenshots, logs or acceptance transcripts as review records. Keep images only when they are actively used by the website, maintained documentation or Fastlane; keep active automated test baselines in test directories.

Then:

- Merge each PR as soon as it has its evidence, unless it depends on another PR.
- PRs need not be up to date with `main`. Update a branch only for a conflict, or when commits that landed since its last green run touch its files, Gradle files, dependency versions or shared test fixtures. When unsure, merge `main` locally and compile instead of re-running everything.
- A known out-of-memory or race failure gets one re-run. A failure with a shared cause (on more than one PR or on `main`) is fixed once in its own PR and merged first; then update only the PRs that failed on it.
- A landing task doesn't build new checks; note them for later. A check added during a task doesn't hold PRs that were already open, but a real bug it finds in a PR's own code gets fixed in that PR.
- Before each later merge in a task, look at `main`'s latest finished `PR checks` run (`gh run list --branch main --workflow pr.yml --limit 3`) without waiting for a running one. If a merged PR turned it red, fix or revert that in a PR before merging other app code.

## Upstream upgrades (Signal / Molly)

Decided 2026-11-21: upstream code lands as real Git merges, so Git records what Collie has taken and later merges start from the last one. Take every Signal tag, and Molly's branch when it moves, as one ordinary app PR. To see what is waiting and get the exact command, run `python3 tools/upstreams/status.py` (read-only; see the [source registry](../upstreams/README.md#whats-waiting-upstream)):

```
python3 tooling/upstream/merge-upstream.py signal <new Signal tag>
python3 tooling/upstream/merge-upstream.py molly <Molly branch, e.g. molly-8.34>
```

- The script fetches the tag or branch, runs `git merge --no-commit`, keeps Collie's reviewed donation deletions (`tooling/upstream/signal-donation-removals.json`) and writes the merge message. `--join` records a release Collie already has without taking its changes; 8.36.3 was joined this way because 8.35 and 8.36 went in as diffs.
- Upstream commits stay unsigned and keep their own authors. The merge commit is signed and carries `Layer: upgrade` and `Upstream: signalapp/Signal-Android <tag>` or `Upstream: mollyim/mollyim-android <branch>@<full sha>`. The app `Layer trailers` check verifies that parent against the upstream repository and skips only commits reached through it; the app `main` signature ruleset lets the maintainer merge upstream-merge PRs.
- Resolve conflicts toward the newer Signal code plus Molly's and Collie's own changes. Collie stays as close to Molly as it can: keep Molly's code, strings and paths unless Collie's version is a real improvement.
- Read what merges cleanly but doesn't belong, and remove it or adapt it: network calls that skip the proxy code, new Google dependencies (Firebase Messaging over `core/gms` stays), strings that say "Signal", donation or payment entry points. Donation UI stays out; donation strings stay as Molly has them.
- If the merge changes the libsignal or ringrtc version in `gradle/libs.versions.toml`, the native libraries must be rebuilt (`tools/native`) and published first, or the app build fails.
- Molly is often on an older Signal than Collie. Merging its branch then brings only Molly's own work, since Collie already has those Signal commits.
- Agents may merge upgrade PRs once required checks and `Unit tests` are green and any required maintainer review is in.

### Additional selected sources

The [upstream source registry](../upstreams/README.md) tracks selected changes from
other public forks, including `johanw666/Signal-Android`. Review custom release
snapshots against their own Signal base, then port individual features in app PRs
with `Layer:` and upstream attribution. Keep review decisions and merged app
references in the source ledger. The registry does not revive the historical
patch composer or make third-party changes automatic build inputs.

## Releases

Product and source policy: [DUAL-RELEASE-POLICY.md](DUAL-RELEASE-POLICY.md).
The steps below describe the Stable product. Its process is app
`docs/RELEASING.md`; a Stable verification prerelease and the separately installed
TrailBlazer Beta product have different purposes and publication routes.

- The release APK is built by this machine or GitHub, whichever is faster, with app `tooling/reproducible-builds/build-release.sh`.
- The maintainer signs it locally (APK key) and tags it locally (publication key, `WtFs…`, listed in app `.github/release-signers`).
- One `collie.apk` is uploaded. App CI checks it, uploads the profile copies (`collie-foss`, `molly-continued`, `molly-foss-continued`, `molly-minimal`) and publishes a prerelease within minutes.
- A full release is the same APK, promoted only after a rebuild on the other side (GitHub or this machine) matches it.
- App `main` is the single Stable source branch. `release/stable/8.35` is retired.
- App CI reads `docs/release-notes/<version>.md` from the exact signed tag commit and requires notes before publishing. The release's schema 1 `release.json` carries the canonical release metadata. In-app notices ship as ordinary app PRs.
- The Stable F-Droid feed carries verified full Stable releases only. TrailBlazer
  Beta has a separate package, APK signer and feed; its GitHub releases remain
  immutable prereleases, and `feeds.yml` publishes the newest one once its APK
  matches its CI build ([BETA-FEED-OPERATIONS.md](../distribution/BETA-FEED-OPERATIONS.md)).
  Pages must preserve both feeds and native inputs when either feed changes.

## Security fuzzing pilot

The focused Socket/Android pilot and its evidence expectations are documented in [SECURITY-FUZZING.md](SECURITY-FUZZING.md). It is a development and release confidence measure; it does not change release approval or branch enforcement.

## Historical release tooling

The historical app-source composer and patch forest are documented in
[archive/README.md](../archive/README.md). Use that pinned history only to
reproduce patch-built releases.

The retired release helpers and their [migration contracts](https://github.com/0cwa/collie-release-engineering/blob/retired/release-tools/docs/migration/CONTRACTS.md)
are available at the `retired/release-tools` tag. The current Pages release
workflow validates its receipt inputs with `distribution/migration_receipts.py`.

Keep native-input delivery (`repo.collie.im/native-inputs`) and Pages/F-Droid
publishing; `pages-release.yml` stays until native files have another publishing
route.
