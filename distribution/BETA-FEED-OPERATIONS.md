# TrailBlazer Beta feed candidate and Pages promotion

The candidate workflow signs only a Beta F-Droid index and uploads an Actions
artifact. It never deploys Pages or publishes an Android release. Beta deployment
continues through the existing manually dispatched, two-phase Pages workflow.
The scheduled Stable workflow continues to rebuild Stable automatically and now
retains the verified Beta route from the currently served site inventory.
Before building, it records the digest of a complete verified site inventory;
the deploy command checks that same inventory again after reconciling any older
Pages deployment and immediately before its durable reservation. A Beta update
that lands during a Stable build makes the Stable candidate stale and blocks it.

## Build the reviewed Beta candidate

Before repository generation, an operator stages a **pre-index** archive on a new
engineering-owned draft release. It contains only the selected signed APK, its
schema 2 source/build/release receipts and release approval, and the unsigned/final
manifests. It does not contain repository indexes, repository receipt, readiness
data, Pages approval or future hashes. This ordering avoids requiring the index
that this workflow is about to create. The engineering workflow token cannot read
an Android draft release, so the operator independently approves the engineering
asset ID and raw SHA256. The workflow verifies that fixed asset through the
engineering repository API, then verifies the receipt graph, actual APK identity,
source CI, signed source tag, title/tag/prerelease/version and exact asset name.
This boundary does not claim an independent API observation of the Android draft.

Before archiving, explicitly stage the exact preparation record generated during
trusted collection. The command checks the approved plan, source receipt, approved
build receipt and the preparation marker, then creates
`bundles/<release-id>/preparation.json` exclusively under an existing release-output
directory with owner-only permissions (`0600`). It refuses to overwrite a file
already at that path. Archive creation below sets the required member permissions
without changing the local files:

```sh
python3 tools/patches/stage_preparation.py \
  --channel trailblazer-beta \
  --release-plan <approved-release-plan.json> \
  --approved-release-plan-sha256 <approved-raw-plan-sha256> \
  --source-receipt <source.json> \
  --build-receipt <build.json> \
  --approved-build-receipt-sha256 <approved-canonical-build-digest> \
  --preparation <collector-output>/preparation.json \
  --release-id <release-id> --bundle-root <release-output>
```

Build the archive from the selected release output directory. `<release-id>` must
be the authoritative Android release ID in the schema 2 approval. Use a fresh
engineering staging draft/tag for each asset and never replace an uploaded asset.

```sh
tar --format=ustar --sort=name --owner=0 --group=0 --numeric-owner --mode=0644 --mtime='UTC 1970-01-01' \
  -C <release-output> -cf trailblazer-beta-release-input.tar \
  "releases/<release-id>.json" \
  "bundles/<release-id>/source.json" "bundles/<release-id>/build.json" \
  "bundles/<release-id>/release.json" "bundles/<release-id>/preparation.json" \
  manifests/unsigned-manifest.json manifests/final-manifest.json \
  apk/trailblazer-beta.apk
sha256sum trailblazer-beta-release-input.tar
```

Create a new engineering-owned staging draft for every archive; never reuse a tag
or replace an uploaded asset:

```sh
STAGING_TAG=trailblazer-feed-input-<unique-tag>
gh release create "$STAGING_TAG" -R 0cwa/collie-release-engineering --draft \
  --title "TrailBlazer Beta feed input" --notes "Reviewed pre-index APK and receipt graph."
gh release upload "$STAGING_TAG" -R 0cwa/collie-release-engineering trailblazer-beta-release-input.tar
gh release view "$STAGING_TAG" -R 0cwa/collie-release-engineering --json assets \
  --jq '.assets[] | select(.name == "trailblazer-beta-release-input.tar") | [.id,.digest,.size] | @tsv'
```

An independent reviewer checks the raw SHA256, asset ID and release graph before
the values are entered in the protected workflow dispatch. The downloader uses the
fixed engineering API asset endpoint with this repository's token, so an
engineering draft asset is supported without a new cross-repository token.

Dispatch **Build approved TrailBlazer Beta feed candidate** on `main`, with the
numeric engineering pre-index asset ID and its complete SHA256. The job is protected
by the existing `github-pages` environment and is main-only. It verifies the
pre-index asset and full signed release graph, checks out the source
commit from the verified receipts, and reads only
`fastlane/metadata/trailblazer-beta` from that immutable checkout. It checks the
TrailBlazer title and icon SHA256 `f6a0751bf26db92367f6012f063d643bf49144728bb4c90dcdbd2f48f27ba4b2`.
The submitted source revision must be the independently approved final Beta source
commit containing Android branding commit `ae514b281b9c` and the approved source
workflow commit `62e74987dcdb71bad6b4f52244e01c7d66cd51ba`; neither commit alone is
a release pin. The source receipts and successful exact-commit CI run remain the
authority for the actual final source SHA.

The workflow emits the `trailblazer-beta-feed-candidate` artifact, including the
Beta-only `/fdroid/beta/repo/` tree, its signed indexes and indexed APK, public site
inventory, feed state, and candidate receipt summary. It is not a readiness or
Pages input. The candidate verifier uses the current served signed Beta index as
the versionCode floor. Before the first Beta index exists, it checks all pages of
the public Android release list, downloads every published Beta APK asset from the
fixed GitHub release host, verifies the GitHub asset digest, then parses its package,
signer and version. An unavailable or malformed history fails closed. The new
versionCode must exceed that verified history.

After the candidate passes the isolated F-Droid client check, create the real
prepublication repository receipt with `distribution/receipt.py` using the actual
candidate route's repository files as the receipt input. Copy only
`beta-site/fdroid/beta/repo/*` into the later evidence input's `candidate/` root;
do not include its site-level `CNAME`, inventory or `feed-state.json` in the
F-Droid generation digest. Use the exact signed APK and release graph from the
verified archive. Keep
production HTTPS readback as `NOT RUN`; it is journaled only after deployment. Pack
the completed graph with `distribution/beta_evidence.py pack`, independently review
the complete tar SHA256, and stage that exact tar under the fixed
`trailblazer-beta-release-evidence.tar` name. The readiness workflow then verifies
the completed evidence and emits its one-file readiness artifact. The separate
Pages approval is prepared only after that run succeeds. For the first Beta,
keep the Android release in draft through readiness at the captured candidate
SHA. Then publish that same release ID as a prerelease, preserving its tag and
APK assets. Use the completed readiness artifact and the same verified bytes
for the separately approved feed deployment and canonical readback.

## Prepare the final Pages promotion archive

Use the same evidence tar whose complete SHA256 was approved for the readiness run.
Copy its raw bytes to `<pages-input>/release-evidence.tar` and verify that SHA again
after unpacking the final archive. The final Pages tar contains this frozen tar,
its unchanged receipts, candidate APK/index files and receipt graph, plus the readiness run/artifact identity, a fresh complete served
site snapshot, and the reviewed landing files. It does not modify or repack signed
APK/index/receipt bytes. The Pages verifier downloads the exact readiness artifact
again from the authoritative run and checks its digest and receipt links.

Capture the previous whole site immediately before assembling the promotion input:

```sh
python distribution/feed.py --capture-live-site <pages-input>/previous
```

The first Beta promotion requires a served `site-manifest.json`. If the current
Stable site predates this inventory, first complete the explicit
[inventory-only bootstrap](INVENTORY-BOOTSTRAP.md), preserving all reviewed
existing bytes. `force=true` does not bypass the Stable publisher's required
baseline and cannot initialize it. The Beta capture path treats an absent
inventory as no Beta route only after confirming the Beta index is also absent.
Any Beta bytes without the canonical inventory stop the run.

This fetches only `https://repo.collie.im/`, checks every served byte against its
`site-manifest.json`, and verifies both signed repository indexes when present. A
missing inventory is not treated as empty if the Beta index exists. Retain this
directory unchanged in the final archive. Copy the approved Beta candidate into
`candidate/`, the identical release approvals and schema 2 source/build/release
receipts into `releases/` and `bundles/`, and the corresponding repository receipt
to the bundle root. Copy `previous/index.html` and its approved static assets into
`landing/`; Beta promotion preserves the existing Stable landing and route.

Write `approval.json` as a separate human-reviewed file. Its exact schema 2 fields
are documented in `PAGES.md`: `kind=collie-pages-input`, origin, policy revision,
previous inventory and manifest digests, candidate generation digest, repository
certificate and receipt digests, release approval names, landing digest and site
byte limit, plus Beta channel, source branch/commit, readiness workflow/run/attempt,
artifact ID/digest and readiness commit. Set readiness commit equal to the approved
source commit. Derive hashes from the unchanged files; do not copy identity or
version strings from dispatch inputs. Human review of this complete input is the
approval; packing never approves it.

Create the final data-only archive and verify the actual unpack/prepare path before
staging it for the existing Pages workflow:

```sh
tar --format=ustar --sort=name --owner=0 --group=0 --numeric-owner --mtime='UTC 1970-01-01' \
  -C <pages-input> -cf <new-pages-input.tar> approval.json repository-receipt.json \
  releases bundles candidate previous landing release-evidence.tar
python distribution/pages.py unpack <new-pages-input.tar> <fresh-unpacked-dir>
sha256sum <fresh-unpacked-dir>/release-evidence.tar
python distribution/pages.py prepare <fresh-unpacked-dir> <fresh-prepared-dir> \
  --policy <reviewed-40-hex-engineering-commit> --channel trailblazer-beta
```

Set `COLLIE_APP_READINESS_TOKEN` in the approved credential environment for the
local prepare command above. For the hosted credential setup and verification-only
dispatch, follow [Workflow permission and activation gate](PAGES.md#workflow-permission-and-activation-gate).

Run `pages.py preflight` and the existing two separately approved Pages phases using
the prepared plan. Preserve the exact evidence tar SHA and all receipt bytes in the
review packet. If the live site changes after capture, the existing Pages preflight
rejects the stale previous snapshot; recapture and create a new approval. Stable and
Beta Pages runs share `collie-pages-production` with `cancel-in-progress: false`,
so one channel cannot publish a stale whole-site snapshot over the other.

The engineering checkout does not have the Android readiness workflow or the final
Pages archive packer as an automated producer. This recipe uses the existing tar,
`pages.py unpack`, validator and manual approval path; no new upload or publisher
command is implied. Candidate tests use mocked external APK/GitHub/index transports
where called out. They do not prove a live Beta release, real production key use,
GitHub environment protection settings, hosted F-Droid client acceptance, or Pages
deployment.
