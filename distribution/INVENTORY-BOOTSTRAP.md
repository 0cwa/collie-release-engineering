# Bootstrap an existing Pages inventory

The normal Stable publisher requires a verified `site-manifest.json` before it
builds a replacement site. A legacy site without that file needs this one-time
inventory migration. `force=true` does not bypass the normal baseline checks and
cannot initialize the inventory.

This operation adds only `site-manifest.json`. It preserves every reviewed
existing file byte-for-byte, including the Stable APK, signed indexes, listing,
landing page, `feed-state.json` and six pinned native-v1 inputs. It neither builds
nor signs anything and does not publish a new release or activate Beta.

## Review a complete inventory first

Obtain the complete snapshot from the last successful Pages deployment or an
authoritative archive of that deployment. Record the deployment run ID and how
the snapshot's completeness was established. The old GitHub Actions artifact may
have expired; an expired artifact must not be replaced by a guessed list or a web
crawl. If an archive is unavailable, reconstruct and independently review the
complete output inventory against that exact publisher's inputs and generated
file contract. Every existing path must be accounted for, including older or
otherwise unreferenced assets. Public HTTPS checks alone cannot prove that a
file list is complete.

Using the pinned distribution environment and Java `jarsigner`, inventory the
complete local snapshot:

```sh
python distribution/bootstrap_inventory.py inventory \
  --snapshot <complete-legacy-site> \
  --output collie-pages-legacy-inventory.json \
  --policy <reviewed-40-hex-engineering-main-commit> \
  --previous-deployment-run-id <successful-pages-workflow-run-id> \
  --provenance '<archive identity and the evidence establishing completeness>'
```

The command verifies the Stable signed indexes and their referenced files,
package and signer, the published feed state, the hostname, and the unchanged
native-v1 policy. It writes a strict JSON object containing `schema: 1`,
`kind: collie-pages-legacy-inventory`, `origin: https://repo.collie.im/`,
`policy_revision`, `previous_deployment_run_id`, `provenance`, and `files` mapping
each exact relative path to its `sha256` and byte count. It prints the SHA256 of
the complete raw JSON. Creating this document does not approve its completeness.

An explicitly reconstructed inventory uses the same schema and needs the same
independent review. No source URL, key, command or executable code is accepted
inside it. The preparer permits only the legacy Stable/static/native paths;
unexpected historical routes require separate policy review rather than omission.

Upload the unchanged `collie-pages-legacy-inventory.json` to a new release asset
in **0cwa/collie-release-engineering**, and retain its exact numeric asset ID and
independently reviewed raw SHA256. Do not replace a previously reviewed asset.
An engineering-owned draft release is supported by the existing asset downloader.
No arbitrary URL or asset from another repository is accepted. The inventory is
capped at 8 MiB and 40,000 files; the complete site including its new manifest must
remain within 950,000,000 bytes.

## Prepare and publish the unchanged site

For local verification, run:

```sh
python distribution/download_pages_input.py --kind legacy-inventory \
  --asset-id <engineering-asset-id> --sha256 <reviewed-raw-sha256> \
  --output <fresh-inventory.json>
python distribution/bootstrap_inventory.py prepare \
  --inventory <fresh-inventory.json> --sha256 <reviewed-raw-sha256> \
  --output <fresh-output-directory> --policy <reviewed-policy-commit>
python distribution/bootstrap_inventory.py check-candidate \
  --baseline <fresh-output-directory>/baseline.json \
  --site <fresh-output-directory>/site
```

Preparation checks the full file set and aggregate size before any live file
download. It requires the inventory and all Beta entry/v1/v2 indexes to be absent,
streams each reviewed canonical HTTPS object to disk while hashing it, and
verifies the signed Stable indexes. Redirects, HTTP failures, changed bytes,
missing files, symlinks and unapproved paths fail. A failed preparation leaves its
output for inspection; retry with a fresh directory, never by reusing partial data.

Dispatch **Bootstrap the reviewed legacy Pages inventory** on `main` with the
reviewed policy revision, asset ID and raw SHA256. The existing `FEEDS_ENABLED`
setting and `github-pages` environment apply. The workflow shares the
`collie-pages-production` lock and durable remote deployment guard with both
publishers. It uses no APK or repository signing secret.

The frozen baseline contains the exact reviewed inventory JSON and the new
manifest digest. Before the Pages POST, the workflow reconciles older remote
deployments, verifies that its candidate contains exactly the old files plus
the manifest, then re-fetches and checks the same old files and absence sentinels.
Only then does it reserve the deployment guard and create the deployment. A
changed file, newly appearing inventory, or Beta index aborts before publication;
an old baseline never adopts newer served state. This relies on all publishers
using the shared lock; it is not an origin-side conditional-write transaction.

After Pages reports success, a streaming readback verifies the exact inventory
and every preserved file again. Preserve the raw input, baseline and successful
run identity in the release archive outside CI retention. If deployment completed
but readback failed, use the `readback` command below to verify that same baseline;
do not rerun the deployment or rebuild the site:

```sh
python distribution/bootstrap_inventory.py readback --baseline <baseline.json>
```

Once readback passes, ordinary Stable and Beta publication can use the new
inventory. A second bootstrap deliberately fails because the manifest now
exists. This workflow is not a recovery override for a missing manifest on a
site that has already published Beta; that situation requires investigation.
