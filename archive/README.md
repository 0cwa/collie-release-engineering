# Archived patch-built releases

Collie releases up to 8.35.1 were composed from patch layers: Signal source plus
the Molly snapshot (`molly`), Signal port layers (`ports`) and Signal-FOSS
profile layers (`forks`), assembled by `tools/patches` from `molly-build.json`.
App `main` is now the source and upstream code arrives as Git merges
([development cycle](../docs/DEVELOPMENT-CYCLE.md)). The composer was retired on
2026-11-22, after `v8.36.3-1-rc2`, the first upstream-merge upgrade, shipped as a
full release.

## Patch forest

The [`archive/patch-forest` tag](https://github.com/0cwa/collie-release-engineering/tree/archive/patch-forest)
preserves the forest moved by commit `0bd304c2e67b0b74c1e665d8cd71a33a6a81e0ed`.
The Git tree IDs match the original `patches/molly`, `patches/ports` and
`patches/forks` trees:

| Path | Tree |
| --- | --- |
| `patch-forest/molly` | `7a280ac23b61cef2c09009a6dbed02773fff3dfa` |
| `patch-forest/ports` | `6ddfecf3390e6561db0884c9ec4ec483514d000f` |
| `patch-forest/forks` | `ba1a98bb6f4e207c7199d583888949a258f6d074` |

Check with `git rev-parse refs/tags/archive/patch-forest:archive/patch-forest/molly`
(and the others).
Authorship and layer provenance stay in the patch headers, `series` files,
`provenance.json` files and source bundles. The forest was extracted from app
history as recorded in [`EXTRACTION.json`](https://github.com/0cwa/collie-release-engineering/blob/archive/patch-forest/EXTRACTION.json).
Files in the forest
refer to paths as they were before the move (`patches/...`, `tools/patches/...`,
`molly-build.json`).

## Reproducing a patch-built release

The composer, its tests and its documentation (`tools/patches`,
`molly-build.json`, `docs/PATCH-PIPELINE.md`, `docs/PORT-REPORT.md`) were
removed rather than moved. The last `main` commit that contains them with the
forest at its original paths is `fb5090f35920fb57476268e995fde1505e49ebf4`.
Check it out in a separate worktree and follow its `docs/PATCH-PIPELINE.md`,
which also lists the pinned Signal and Molly commits the composer needs:

```sh
git worktree add ../collie-patch-composer fb5090f35920fb57476268e995fde1505e49ebf4
cd ../collie-patch-composer
python3 -m unittest discover -s tools/patches -p 'test_*.py' -v
python3 tools/patches/release.py --repo . --config molly-build.json \
  --worktree /path/to/new/release-tree --report /path/to/source-proof.json
```

Do not use the composer for new app work, and do not regenerate or edit the
archived patches.
