# Initial native storage budget — prepared, not activated

GitHub Pages at `https://repo.collie.im/` is the selected initial host for both the
binary feed and exactly one existing native-v1 Maven generation at
`/native-inputs/v1/maven/`. This explicit capacity decision preserves the original
source URLs. No Pages/DNS/TLS setting, key or upload has been activated. The former
separate R2/native.collie.im proposal is an inactive future alternative, not an
initial requirement or a second feed provider.

`native-v1.json` is the trusted six-file policy: three existing binary inputs and
three transformed POMs, exact SHA256 and sizes, totaling310,836,447bytes. The data
bundle must carry an identical `native-manifest.json` with a separately approved
raw SHA256 and the six files beneath `native/`. No arbitrary native file, new
coordinate/generation, symlink, omission or changed byte is admitted. These are
accepted trusted-local inputs; independent native reproduction remains NOT RUN.

Native-v1 plus the canonical RC1 APK is407,890,788bytes before index/site overhead.
One native generation plus six RC1-sized APKs is893,162,493bytes; seven is990,216,834
before overhead, exceeding the950,000,000byte operational ceiling. Both complete
site snapshots AND their tar artifacts must fit this ceiling. Every published
native-v1 file stays reachable in every later snapshot. No automatic pruning or
old-binary overwrite is permitted; growth requires a separate archive/storage
approval. Pages has a documented1GB site limit and100GB soft monthly transfer
limit; six binaries plus indexes/readbacks/download traffic are budgeted together.

A native-only bundle needs no APK, repository-index signer, signed index or add
link. Both snapshots preserve any existing feed/landing; an initial empty site
shows preparation text and no active feed. Initial preflight checks absent
manifest/entry plus every native URL (absent or already identical). Full canonical
HTTPS readback must succeed before a new-source build may use these public inputs.
Later feed publication needs its own approved production index certificate and
signed repository receipt. Test staging keys are never production authorization.

Operator package: exact policy revision, native-manifest raw SHA256, complete
input bundle SHA256, previous published inventory/manifest, both tar SHA256/sizes,
per-file URL/hash/size plan and durable journal. Provision/activate Pages/custom
domain/TLS/protected reviewers only with separate production approval. Read every
canonical URL back before source-build acceptance; preserve the receipt outside
CI retention. R2 account/DNS migration is not requested for this initial route.

Official limits: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits

## Retention on scheduled Stable feed updates

The Stable feed workflow keeps the complete verified site captured by
`capture-deploy-baseline --retain-site`. The builder requires that snapshot and
its original deploy baseline together. It checks the exact inventory, every
file's hash and size, and both signed repository routes before using the snapshot.
It does not capture a different Beta generation during the build. The existing
deployment guard still checks the live site against the original baseline before
creating a Pages deployment.

Each whole-site update preserves every previously published version-addressed
native Maven artifact and every Stable `im.collie.app_<versionCode>.apk` URL.
It also retains signed-index and repository-receipt archives at the known
`_generations/<digest>/` paths, F-Droid diff files, versioned icons, and package
listing artwork. Previously retained URLs remain retained on subsequent updates.
Unknown generation paths fail the build instead of being silently discarded.
Native files must
use an explicit `/native-inputs/vN/maven/` generation and a versioned Maven
coordinate; mutable Maven metadata and snapshot versions need a separate policy.
Unknown native paths, malformed APK paths, missing files, or changed bytes fail
the build. An existing immutable URL can be reused only with identical bytes.

`FEEDS_KEEP_RELEASES` controls which releases enter the newly signed F-Droid
index. It is not a deletion policy. Older APKs are copied after index generation,
so clients with a cached signed index can still download their referenced APK.
The current `feed-state.json` lists indexed APKs under `apks` and older served
APKs under `retained_apks`. The public inventory covers all served files. The
complete existing Beta route is preserved when `--preserve-beta` is enabled;
a captured Beta route cannot be silently discarded by omitting that option.

Before copying retained files, the builder admits the complete candidate union,
including the new inventory, against the 950,000,000-byte ceiling. It checks the
final assembled site again before upload. After the pinned Pages upload action,
it measures the exact regular, unlinked `$RUNNER_TEMP/artifact.tar` left by that
action; headers and padding must also fit the same ceiling. This gate runs before
the deployment step, so an oversized uploaded artifact never triggers a Pages
deployment. Crossing that ceiling stops publication
and requires a separately reviewed retirement or storage migration; reducing the
number of indexed releases does not silently delete published APK or native URLs.

The two-phase Pages publisher uses the same versioned-path policy for native
files retained from an approved previous site. Its six fixed native-v1 pins must
remain present and unchanged, while later already published immutable versions
are preserved in both phases. The previous inventory and complete site still
need their external digest approvals. Supplied native inputs remain restricted
to the separately approved six-file policy; retention does not authorize a new
native coordinate. The publisher also preserves the feed state, canonical
`repo.collie.im` CNAME, and F-Droid artwork filenames containing `=`.

## Admitting a new immutable native generation

`native-generations.json` registers additional manifest files by exact path and
raw SHA-256. Its empty initial list admits no new inputs. Register a manifest
only after the new binaries and transformed POMs have real, reviewed hashes and
byte counts; a source-only native candidate is not sufficient.

A registered `native-generations/vN.json` (N at least 2) uses schema 1, kind
`collie-pages-native-generation`, origin `https://repo.collie.im/`, integer
`generation` N, and a `files` map. That map includes all six original native-v1
entries unchanged, plus the exact newly reviewed artifacts under
`native-inputs/vN/maven/im/collie/native/`. Each added coordinate has a versioned
binary and POM; libsignal Android and client versions must match. New libsignal
artifacts can reuse the original RingRTC coordinate without rebuilding or
copying it into another generation.

The bundle's `native-manifest.json` must equal that checked-in registered
manifest, and its separately approved raw digest must match. The supplied
`native/` inventory must equal the complete registered file map. A caller's
manifest cannot authorize itself. Registry entries reject altered original
bytes, unversioned metadata, unknown modules, missing POMs, mismatched libsignal
versions and policy path substitutions. The original six-file manifest remains
accepted for ordinary feed updates; a captured later generation remains in
the complete retained site. The special initial empty-site recovery remains
limited to native-v1.

Registration adds no deployment authority. Existing policy-revision approval,
previous-site capture, complete-site capacity checks, immutable-URL checks,
two-phase deployment and canonical HTTPS readback still apply. An accepted
manifest may not overwrite an already published URL or remove earlier files.
