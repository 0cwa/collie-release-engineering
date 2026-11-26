# Engineering CodeQL report and review tools

The SARIF report and review gate originated in [collieim-android PR #42](https://github.com/0cwa/collieim-android/pull/42)
(d6001052447be56625bec2ba2b60533972d4b296). Engineering runs the gate for
Python and Actions, retains raw SARIF, and controls upload with
`CODEQL_ADVANCED_UPLOAD`. The Android compiler-scope gate and screenshot tools
are maintained in [collieim-android](https://github.com/0cwa/collieim-android)
under `tooling/codeql/` and `tooling/screenshots/`. Older Android revisions that
pin an engineering commit still use the files at that commit.

Original copied-file hashes:

- tools/codeql/report.py 6f525ef1b50becfc9f22fc7c9ccd41db37abf2aa43fefeb9ebb77fcb0876dc31
- tools/codeql/test_report.py 50df2bd90eab9f033bee69e4d55cdeb0fdcf9655aef8fbaa6da46b4c491b3d4f
- tools/codeql/test_reviews.py 7cc67e635f67e36d88ea33ffd98e08a616bf17830213b2f038f6b9b373d9a576

## Source-bound reviews

Review entries may use `false-positive` or `intentional-use`. The latter is for
independently reviewed, deliberate behavior; it is not a finding dismissal or a
blanket approval. Both require the same exact finding identity, source SHA-256,
explanation, and trusted-base policy process. Confirmed vulnerabilities and
unreviewed findings remain blocking; SARIF retains every finding. Each actual
entry needs independent review before a trusted-main policy PR.

Flow and related-location reviews require `context_sha256` in addition to the
existing source fingerprint. The digest is calculated with
`report.context_sha256(finding, source_root)` from the audited finding and the
analyzed checkout. It binds the complete SARIF `codeFlows` and
`relatedLocations`, their resolved locations, and the contents of every
referenced source file. Missing, unavailable generated, external, unsafe, or otherwise
unbound locations cannot be approved through this mechanism. A path-problem or
finding with flow or related-location evidence without this field remains
unreviewed. Direct findings without such evidence retain the existing contract.

The field accepts one lowercase SHA-256 string, or a nonempty list of unique
lowercase SHA-256 strings for independently reviewed exact contexts. The
actual context is still hashed once by the same version-2 algorithm and must
match one listed value. Unknown contexts fail closed; this does not normalize
locations, IDs, flows, or messages. Lists must be deployed only after every
checker consuming the manifest supports them; prior checkers do not match lists.

Older checkers without `context_sha256` support ignore the field. Use
compatible checkers wherever a manifest relies on this binding.

A review may additionally declare `reviewed_sources`, a map of checkout-relative
source paths to lowercase SHA-256 hashes. Reviewers must include the source
files on which a nonlocal judgment depends, even when those files do not appear
in SARIF. Each declared file must exist, remain inside the checkout without
symlinks, and match its recorded hash; otherwise the review fails closed. This
list does not discover future callers or prove a complete call graph. Automatic
flow context and primary-source binding remain required as applicable.

Earlier checkers without `reviewed_sources` support ignore the field. Use
compatible checkers wherever a manifest relies on these dependencies.

## Optional trusted review snapshots

A consumer may opt in with `--review-catalog`, `--trusted-policy-root`,
`--analysis-config`, and `--analysis-config-sha256`, retaining `--reviews` as the
default manifest. Catalog, manifest and config paths are relative to the trusted
policy checkout. For a PR, use its trusted base checkout, never the analyzed PR's
catalog. Supply the digest of the exact trusted config used by CodeQL init.
Without these options, the existing `--reviews` behavior is unchanged.

The catalog has `schema: 1`, `profile: "collie-android-v1"`, and a `snapshots`
array. Each entry contains `language`, `source_sha256`, `analysis_config_sha256`,
`manifest`, and `manifest_sha256`. The helper selects one complete manifest for
an exact identity match; it never combines snapshots. A miss uses the default.
Malformed, ambiguous, unsafe or hash-mismatched catalogs fail the audit.

`review_snapshots.source_fingerprint(source_root, language)` computes the source
key using the pinned profile's application and recipe paths. It hashes indexed
paths, modes and actual bytes, and independently rejects additional disk inputs,
including ignored source. Only fixed Gradle build/cache directories beneath
indexed module build scripts are excluded from disk inventory. Symlinks and
submodules in the profile are rejected. Policy payloads and ordinary root docs
do not affect the key, so adding a reviewed snapshot does not invalidate itself.
Generated output is outside this key; existing finding, context, generated-source,
dependency, extraction and compiler-coverage guards still apply.

Every snapshot record needs independent source review and exact analysis
binding before it enters trusted main. This selector supplies no approvals and
does not waive failures.
