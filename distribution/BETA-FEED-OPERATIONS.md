# TrailBlazer Beta feed

Collie and TrailBlazer Beta share [the Collie F-Droid repository](https://repo.collie.im/fdroid/repo/).
The scheduled [`feeds.yml`](../.github/workflows/feeds.yml) workflow selects the
newest eligible release of each edition and rebuilds the whole Pages site.
TrailBlazer publication requires a maintainer-signed tag and a Beta-signed APK
on an immutable GitHub prerelease.

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
through the Git data API, and generates one index for both packages. A new Beta
versionCode must exceed the one in the served signed index. The repository uses
the existing repository key (`b0c5dcba…`), and each APK uses its own pinned key.
When there is no new verified Beta, the builder carries forward the exact APK
from the frozen signed index and checks its digest against the immutable GitHub
release. This works after the original CI build artifact expires.

A failed Beta check never blocks a Stable update; the run shows the failure and the
next scheduled run tries again.

## Releasing a Beta

1. Merge the reviewed Beta source to app `main`. Confirm the merge commit is the
   intended source, then dispatch `build-release.yml` on `main` with `edition=beta`
   and `ref` set to that exact merge commit. Confirm the successful unsigned
   artifact identifies the same commit.
2. Sign its `trailblazer-beta.apk` locally with the Beta key, tag that same commit
   with a signed annotated `trailblazer-v<version>` tag, and publish an immutable
   prerelease titled `Collie TrailBlazer Beta <version>` with that one APK.
3. The next `feeds.yml` run verifies the candidate and publishes it (or dispatch
   `feeds.yml` to skip the wait). Beta can ship by itself or alongside a Stable
   verification prerelease or full release. If Beta verification fails, the
   served Beta APK stays in the repository and an independent Stable update can proceed.

Complete release publication and feed verification within 14 days of the build,
while the unsigned artifact is available. A fresh dispatch can rebuild the same
commit while it is still the tip of `main`. After `main` advances, re-run the
original Beta workflow within GitHub's
[30-day window](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
to keep its source SHA and ref. Verify that the run has exactly one artifact
named `unsigned-release-apk` and that it is unexpired. If a usable artifact
cannot be recovered, prepare a new Beta release with a higher version code.

## Turning it on and checking it

Set `BETA_FEED_ENABLED=true` alongside `FEEDS_ENABLED=true` to allow scheduled
Beta publication.
Setting it back to `false` stops Beta candidate publication; Stable site rebuilds
continue to include the same signed Beta APK.

[`feeds-dry-run.yml`](../.github/workflows/feeds-dry-run.yml) runs the distribution
tests, builds both listings with a throwaway key, and measures the site and Pages
tar against the 950,000,000-byte limit. It checks a new Beta against its CI build,
or carries the already verified Beta forward. Use `force_beta_verify` to recheck
a served Beta while its CI artifact is still available.

For the first migration, verify that the published TrailBlazer release comes
from app source containing the shared updater URL. Record the SHA-256 of its
exact verified APK as `migration_beta_sha256`. Dispatch `feeds.yml` from
reviewed `main` with that digest and `dry_run=true` to collect signed indexes
and exact byte counts. This dispatch does not create a deployment or upload a
Pages artifact. The first live migration requires a separate manual dispatch
with `publish_unified=true` and that same digest after approval.

With that publication, `/fdroid/beta/repo/` stops serving indexes and APKs. The
current Beta APK and at most one retained Beta APK move to `/fdroid/repo/`.
Users who added the old F-Droid source should remove it and add the shared source.
Older direct-download TrailBlazer builds should update through F-Droid or GitHub
to a build that checks the shared address.

## Historical candidate workflow

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
