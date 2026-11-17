# Shared labels

`catalog.json` records the shared taxonomy and selected repository areas. Existing
labels, including `no log entry`, remain in place with their existing colors and
descriptions. The sync only creates missing entries; it never edits, renames or
deletes labels. It does not assign labels or infer security, size, target or gates.

Preview with `python3 tools/labels/sync.py --repo OWNER/REPO`. Add `--apply`
to create the listed labels using the authenticated GitHub CLI. Release scope labels
are opt-in: add `--release-targets` only for repositories whose release process
defines beta or RC scope (currently Android). Run it explicitly
for each repository; there is no automatic synchronization.
