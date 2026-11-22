# TrailBlazer Beta feed

Decided 2026-11-22. TrailBlazer Beta reaches `https://repo.collie.im/fdroid/beta/repo/`
the same way Stable reaches `/fdroid/repo/`: the scheduled
[`feeds.yml`](../.github/workflows/feeds.yml) workflow picks the newest published
release and rebuilds the whole Pages site. The publication step is the release
itself: a maintainer-signed tag and a Beta-signed APK on an immutable GitHub
prerelease. Nothing is approved by hand after that, and there are no receipts,
evidence archives or readiness runs to prepare.

## What the workflow checks

The `beta` job holds no secrets. It runs `distribution/beta_inputs.py` and
`distribution/apk_match.py` and, when the feed does not yet serve the newest
TrailBlazer release, requires all of this:

- The newest published `trailblazer-v<version>` release (drafts are ignored) is a
  prerelease, is immutable, is titled `Collie TrailBlazer Beta <version>` and has
  exactly one `trailblazer-beta.apk` whose GitHub SHA-256 matches the downloaded bytes.
  `0cwa/collieim-android` has immutable releases enabled, so a published
  release's tag and assets cannot change afterwards.
- The tag is an annotated tag with a verified signature, pointing at a commit.
- A successful `build-release.yml` run (`workflow_dispatch` on `trailblazer-beta`)
  built that commit, and its `unsigned-release-apk` artifact names that commit.
- The published APK equals that unsigned build plus its signing records, byte for
  byte (the app's `tooling/release/verify-apk-match.sh` method, with its two helper
  files pinned by commit and SHA-256).
- The APK has package `im.collie.app.trailblazer`, the pinned Beta signer
  `a515c034…80fe98` and the tag's versionName.

The `deploy` job (the `github-pages` environment, which holds the repository key)
downloads the same release APK again, requires the digest the `beta` job verified,
exports the listing from `fastlane/metadata/trailblazer-beta` at the tag commit
through the Git data API, and builds both routes in one site. The Beta versionCode
must exceed the one in the served, signature-checked Beta index. The Beta index is
signed with the Stable repository key (`b0c5dcba…`); the separate Beta APK key
never leaves the maintainer's machine. Older Beta APK URLs stay downloadable; only
the new release enters the index. A Stable update while Beta is unchanged keeps the
served Beta route byte for byte (`--preserve-beta`).

A failed Beta check never blocks a Stable update; the run shows the failure and the
next scheduled run tries again.

## Releasing a Beta

1. Merge the Beta source to `trailblazer-beta`.
2. Dispatch `build-release.yml` on `trailblazer-beta` with `edition=beta` and the
   head commit as `ref`.
3. Sign its `trailblazer-beta.apk` locally with the Beta key, tag the commit with a
   signed annotated `trailblazer-v<version>` tag, and publish a prerelease titled
   `Collie TrailBlazer Beta <version>` with that one APK.
4. The next `feeds.yml` run publishes it (or dispatch `feeds.yml` to skip the wait).

The unsigned build artifact expires 14 days after the build. Publish the release
before then, or dispatch `build-release.yml` for the same commit again; the build is
reproducible, so any successful run of that commit is accepted.

## Turning it on and checking it

The `beta` job runs only with the repository variable `BETA_FEED_ENABLED=true`
(alongside `FEEDS_ENABLED=true`). Every pull request that changes the feed code runs
[`feeds-dry-run.yml`](../.github/workflows/feeds-dry-run.yml), which performs the
complete Beta check on the newest TrailBlazer release, served or not, and builds both
routes with a throwaway key. It deploys nothing. Dispatch it on `main` to recheck a
release before enabling the variable.

## Retired route

The earlier route (a pre-index archive on an engineering draft release,
`beta-feed-candidate.yml`, schema 2 source/build/release and repository receipts,
`beta_evidence.py` evidence archives, the app's `beta-readiness.yml`, a readiness
GitHub App and a Beta `pages-release.yml` input with `approval.json`) is no longer
used for the Beta feed. It required the Android release to stay a draft until
readiness, which immutable releases make unnecessary, and its readiness run had to
run on the exact source commit, so a verifier pin update on `trailblazer-beta` made
an already built Beta unpublishable. Those files are removed in a follow-up after
their remaining consumers are checked.
