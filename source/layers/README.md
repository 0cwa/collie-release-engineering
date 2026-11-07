# Application publication layers

Changes in this directory are applied after complete historical reconstruction
and before publication/compilation. Their whole before/after trees and patch
hashes are reviewed in the publication plan. Publication of source alone does
not authorize building/signing/releasing an artifact.

The publication layer's producer passes the exact application commit from the
approved plan as `application_base=plan['old_main']`, together with that plan.
The commit must be available in the pinned application repository. The layer
copies `README.md`, `LICENSE`, `NOTICE`, and `assets` from that commit without
rewriting them; the publisher and candidate verifier compare their complete
Git entries, including modes and symlinks, with the same `old_main`. Never use
the original cutover baseline after the approved application main has advanced.

The required MollySocket trust-context correction is recorded separately in
`mollysocket.patch` and `mollysocket.json`. It starts from the complete current
source checkpoint and preserves the original application commit and author in
the generated feature-layer provenance; see [MOLLYSOCKET.md](../MOLLYSOCKET.md).

Direct build blockers: the accepted native lock binds libsignal-android/client
0.106.1-collie.1 and ringrtc-android 2.74.0-collie.1 but has no public immutable
artifact origin. Do not substitute upstream binaries. Obtain approval to publish
those exact accepted bytes plus POMs at immutable public coordinates, verify all
lock hashes by independent download, add narrow exclusive repository and strict
verification metadata, then test direct compilation from a clean checkout.
Independent native reproduction is a separate follow-up: complete source/toolchain
provenance, reproduce four ABI outputs, compare packaged libraries and execute
proxy/call/device checks. Existing trusted-local evidence is not reproduction.
