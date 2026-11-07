# Collie release-engineering instructions

## Application work is patch-first

Collie is maintained through a layered patch-based release system. The application repository is a published reconstruction, not an editable development authority. Never manually change application code there, on any branch or worktree, and never merge a source PR first with a promise to export its patch afterward.

Application changes must start as canonical engineering patches, with tests in the same logical layer. Temporary source work is permitted only in a disposable reconstruction used to derive and test those patches. Regenerate the published application after the engineering layer is reviewed and its complete-tree parity is verified.

Read `docs/PATCH-PIPELINE.md`, `source/README.md` and `source/PUBLICATION.md` before application work. Resolve the approved recipe and current plan; do not substitute an unmerged staging branch or let a nearby checkout choose the inputs.

## Preserve the layer and audit contracts

- Keep pipeline/tool maintenance, the immutable official Molly snapshot, mechanical integration, compatibility adaptations, behavior fixes/additions and attributed fork changes in their documented review boundaries.
- Classify by behavior and provenance, not by which position avoids replaying downstream checkpoints. Keep existing ordering/dependencies; clarify an ambiguous class or unapproved input change before proceeding.
- Preserve original mailbox bytes and credits unless the relevant change actually requires replacement. Keep tests with the application change. Do not regenerate unrelated patches merely because lines moved.
- Compute before/after trees, source commits, patch/bundle hashes, advertised refs and prerequisite chains from actual objects. Never change a digest or expected tree by hand to turn a failure into a pass.
- Replay affected downstream layers and run their applicable scope/provenance, whole-tree, report/line-origin, native, build and test checks. Distinguish unknown/not-run/failed from passed, and keep source equality, compilation, reproducibility, signing and live acceptance separate.
- Publication plans, G/I commits, receipts, commit-message approvals and live-main reads must remain bound to the exact reviewed candidate. Changing a layer, base or dependency invalidates prior approval. Do not update old tags or protected history.
- Native binary/POM hash acceptance is not independent native reproduction or public-origin verification. Keep production native and host-test inputs separate; never relax verification or ship test JNI to make a build pass.
- Use the existing single-Collie release path. Historical profile support is for explicitly selected old-release verification, not a current product matrix.

## Working in this repository

Engineering tools and policy may be edited in their proper pipeline/publication layer; that is not permission to edit the published application code. Keep changes narrow and retain the same audit contracts. Other agents' priority branches and draft publication approvals remain theirs; use an isolated branch and coordinate integration rather than replacing their work.

`source/policies/application-AGENTS.md` is the authoritative application-facing policy. `source/layers/publication.py` must emit it as the application's tracked root `AGENTS.md`. Any generated BUILD/VERIFY/README guidance must agree with it. Preserve the user's README introduction; place brief source/build information near the bottom.

The existing contribution import/round-trip helpers are mechanical validators and historical recovery tools. Their existence does not authorize code-first application contributions. No publication, signing, production account, repository policy or native-origin approval is implied by a local test or receipt.
