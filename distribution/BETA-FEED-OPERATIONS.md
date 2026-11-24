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
- A successful `build-release.yml` run (`workflow_dispatch` on `main`) with the
  exact GitHub run title `Build TrailBlazer Beta` built that commit, and its
  `unsigned-release-apk` artifact names that commit. Runs
  from `trailblazer-beta`, another branch, a pull request, another repository or
  another commit are rejected.
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

1. Merge the reviewed Beta source to app `main`. Confirm the merge commit is the
   intended source, then dispatch `build-release.yml` on `main` with `edition=beta`
   and `ref` set to that exact merge commit. Confirm the successful unsigned
   artifact identifies the same commit. This first immutable candidate must exist
   before the release-engineering feed PR dry run can verify it.
3. Sign its `trailblazer-beta.apk` locally with the Beta key, tag that same commit
   with a signed annotated `trailblazer-v<version>` tag, and publish an immutable
   prerelease titled `Collie TrailBlazer Beta <version>` with that one APK.
4. The next `feeds.yml` run verifies the candidate and publishes it (or dispatch
   `feeds.yml` to skip the wait). An already enabled Beta feed rejects a new
   candidate until the policy switch is merged, while continuing to serve its
   existing route. A rejected or failed candidate leaves the
   currently served Beta route in place; it does not block an independent Stable
   update.

The unsigned build artifact expires 14 days after the build. Publish the release
before then, or dispatch `build-release.yml` for the same commit again; the build is
reproducible, so any successful run of that commit is accepted.

## Turning it on and checking it

Keep the repository variable `BETA_FEED_ENABLED` unset or `false` until the
main-source build, signed prerelease and dry-run verification are ready. Set it
to `true` alongside `FEEDS_ENABLED=true` to allow scheduled Beta publication.
Setting it back to `false` stops Beta candidate publication; Stable site rebuilds
continue to preserve the currently served Beta route byte for byte. Every pull
request that changes the feed code runs
[`feeds-dry-run.yml`](../.github/workflows/feeds-dry-run.yml), which performs the
complete Beta check on the newest TrailBlazer release, served or not, and builds both
routes with a throwaway key. It deploys nothing. Dispatch it on `main` to recheck a
release before enabling the variable.

## Retired route

The earlier route (a pre-index archive on an engineering draft release,
`beta-feed-candidate.yml`, schema 2 source/build/release and repository receipts,
`beta_evidence.py` evidence archives, the app's `beta-readiness.yml`, a readiness
GitHub App and a Beta `pages-release.yml` input with `approval.json`) was removed.
It required the Android release to stay a draft until readiness, which immutable
releases make unnecessary, and its readiness run had to run on the exact source
commit, so a verifier pin update on `trailblazer-beta` made an already built Beta
unpublishable. The readiness GitHub App and its `pages-preparation` environment
variable and secret (`COLLIE_READINESS_APP_CLIENT_ID`,
`COLLIE_READINESS_APP_PRIVATE_KEY`) are no longer read by any workflow and can be
deleted in repository settings.
