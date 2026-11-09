# Construction adapter

```sh
python3 source/reconstruct.py --workspace REPLAY_WORKSPACE
```

Replace `REPLAY_WORKSPACE` with an empty workspace path. The adapter creates a
Git object store, fetches the exact Signal and Molly prerequisites from their
official origins, imports hash-checked historical checkpoint bundles, then
runs the original complete-tree assembler. The default product is `collie`.
`--historical-profile` exists solely for old-release verification.

No signing or production credentials are needed. This is source construction,
not the future direct-source build command. No private object stores, Git
alternates or branches posing as immutable historical inputs are required by
the adapter. Upstream Git signatures are not claimed.

The exported original tools remain at their original paths with their module
boundaries intact. Some historical tests deliberately require the original
application history/workflows; run those in the immutable original application
checkout named by `EXTRACTION.json`. Extraction does not authorize the old
hosted release workflows in this new repository.
