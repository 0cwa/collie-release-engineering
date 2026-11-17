# Stable feed release inputs

`feeds.yml` and `feeds-dry-run.yml` stage the selected **published full Collie
release** by its exact tag. TrailBlazer releases and regular prereleases are not
Stable feed inputs. A long run of Beta releases cannot hide the selected full
release behind the first page of GitHub history.

`release_inputs.py releases` requires an immutable release with exactly one
canonical `collie.apk`. It records the release ID, asset ID, size, GitHub SHA256,
annotated tag object, and source commit. Downloads are streamed, bounded and
hashed; every redirect must remain on the fixed approved HTTPS origins. The
release and tag identities are checked again after staging. The existing feed
builder checks the APK's package and signing certificate, and now also rejects
tag/version-name mismatches and duplicate version codes before signing indexes.

When `FEEDS_KEEP_RELEASES` requests additional indexed versions, history is
paginated and anchored at the exact selected release. Only full regular releases
after that anchor are retained as older history. A new release appearing before
the anchor cannot become the recommended APK under the selected release's older
listing. A missing anchor or changing history aborts the run. The input count is
bounded at 50 and the selected APK bytes at 950,000,000; the complete site's
existing budget still applies separately.

GitHub history order alone is not a version-order proof. Before signing, the
builder also requires the selected release to be present and have the highest
actual APK version code among indexed inputs. A release-history race therefore
fails instead of silently recommending a newer APK under older listing metadata.

## Listing and native metadata

The default listing revision is the selected release's immutable source commit.
Its `build-inputs/native-artifacts.json` always comes from that source commit.
Native paths, URLs, sizes, digests, duplicate paths and file/directory collisions
are validated before any native download. No input comes from moving `main`.

There is one explicit historical listing exception:

| Release | APK source commit | Listing commit |
| --- | --- | --- |
| `v8.35.1-3-rc2` | `cc55c02bef64b2980c5f384a6a04f52ffb7bef92` | `af90bcda4ed7c83d4eee7582d6f3844b018500b0` |

That release predates the app's Fastlane directory. The listing commit above is
the exact checkout recorded by [successful Pages run 42957848111](https://github.com/0cwa/collie-release-engineering/actions/runs/42957848111),
deploy job `130317140954`, at `2026-11-11T04:14:31Z`. The exception requires both
the release tag and APK source commit to match. It does not change the APK's
source or native manifest. New releases must include their own listing; there
is no missing-directory fallback to a branch.

The input receipt and pinned native manifest are retained as workflow artifacts
for diagnosing publication. CI artifacts are temporary evidence, so keep any
required long-term release archive separately. This input selection does not
approve a deployment, bypass the whole-site baseline, or activate Beta.
