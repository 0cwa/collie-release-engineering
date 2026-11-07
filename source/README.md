# Construction adapter

Application development is patch-first. Read [../AGENTS.md](../AGENTS.md) and
[PUBLICATION.md](PUBLICATION.md) before changing application behavior. The
published application checkout is not an editable authoring authority.

## Scope of the retained layer guide

`docs/PATCH-PIPELINE.md` retains the original in-repository stack, Signal-source
checkout assumptions and historical branch/profile commands. Its layer-classification,
provenance, replay and audit rules remain relevant; those historical commands
are not the current published-tree layout or a current multi-edition build matrix.
Use the approved current recipe/plan and this construction adapter for extracted
engineering work, followed by `source/PUBLICATION.md`. An unmerged migration
candidate does not transfer authority away from the approved legacy patch chain.

The recipe retains upstream snapshots, deterministic mechanical integration,
compatibility changes, behavior fixes/additions and attributed fork layers,
followed by reviewed publication/build overlays. Consult the actual plan and
original layer provenance for their exact ordering; do not flatten these review
boundaries or insert a feature layer solely to avoid downstream replay.

## Reconstruct the approved recipe

`python3 source/reconstruct.py --workspace "$HOME/agent-scratch/collieim-android/replay"`
uses a new Git object store, fetches the exact Signal and Molly prerequisites from
their official origins, imports hash-checked historical checkpoint bundles, then
runs the original complete-tree assembler. The default product is `collie`.
`--historical-profile` exists solely for old-release verification.

The checkout must be clean. Record its engineering commit before running. No
signing or production credentials are needed. This is construction, not the
future direct-source build command. Remove the workspace and its sibling
`-source` worktree after retaining the small JSON report. No private object
stores, Git alternates or branches posing as immutable historical inputs are
required by this adapter. Upstream Git signatures are not claimed.

The exported original tools remain at their original paths with their module
boundaries intact. Some historical tests deliberately require the original
application history/workflows; run those in the immutable original application
checkout named by `EXTRACTION.json`. Extraction does not authorize the old
hosted release workflows in this new repository.
