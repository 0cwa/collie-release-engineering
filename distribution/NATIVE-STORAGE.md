# Native input storage

Native inputs use versioned Maven URLs under
`https://repo.collie.im/native-inputs/vN/maven/`. The original six-file policy is
[`native-v1.json`](native-v1.json); additional approved manifests are registered
in [`native-generations.json`](native-generations.json). These are trusted-local
inputs; independent native reproduction is not established.

A native publication bundle carries `native-manifest.json` and the exact files
beneath `native/`. The manifest, bundle and previous site have separately approved
digests. Canonical HTTPS readback must succeed before an app build uses newly
published inputs. See [PAGES.md](PAGES.md) for the two-phase publication procedure.
A native-only bundle does not require an APK or repository-index signer.

## Retention on scheduled feed updates

[OPERATIONS.md](OPERATIONS.md#retention-policy-on-the-pages-site) defines the
bounded retention policy: retain native files named by current release plans,
the indexed APKs and the newest previous APK per channel. Older native generations
can be removed when no current plan uses them. An existing URL may never be reused
with different bytes.

The feed builder requires the complete verified site captured by
`capture-deploy-baseline --retain-site` and its original deploy baseline. It
checks the inventory, file hashes and sizes, and signed repository routes before
using that snapshot; the deployment guard checks the live site against the same
baseline. A captured Beta route must be preserved unless it is being rebuilt.

Native files use explicit `/native-inputs/vN/maven/` generations and versioned
Maven coordinates. Unknown paths, missing files or changed bytes fail the build;
mutable metadata and snapshot versions need a separate policy. Retention does
not admit new native coordinates.

The complete candidate site and the exact uploaded tar artifact must each fit
the 950,000,000-byte ceiling. The tar-size gate runs before deployment. If the
candidate exceeds the limit, publication stops; use the documented retention
policy or a reviewed storage change.

The two-phase native publisher preserves the approved previous site's versioned
native files and the six original native-v1 pins in both phases. Its previous
inventory and site still require external digest approvals. This publication
path does not apply the scheduled feed's pruning policy.

## Admitting a new immutable native generation

`native-generations.json` registers manifests by exact path and raw SHA-256.
Register a manifest only after the new binaries and transformed POMs have reviewed
hashes and byte counts; a source-only native candidate is not sufficient.

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
