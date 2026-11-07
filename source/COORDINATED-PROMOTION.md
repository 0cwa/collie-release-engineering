# GitHub and binary repository promotion

The existing local GitHub publisher and repository generation publisher each
validate bytes, but their original independent journals do not make a single
cross-endpoint transaction. In particular, the legacy GitHub publish command
expects a draft and cannot simply be repeated after the release is published.
Use one durable `coordinated_promotion.py` operator record to bind both endpoints
to the same approved final-manifest digest, APK hash/certificate/package/code,
release ID/tag and signed repository generation. Never rebuild on retry.

1. Prepare/sign once using the approved source/build/release graph and dynamic
   serialized version reservation. Approve the immutable promotion plan digest.
   Existing RC1 redistribution uses its original code/bytes and legacy evidence,
   not a new signing invocation. Keep plans/state beyond CI retention.
2. Stage the GitHub draft and hash readback with existing release_publish.py.
   Manual production approval remains separate. After publication, independently
   read back release ID/tag, final manifest and all five downloaded alias hashes,
   certificate/package/code; produce a passing schema1 `github` readback report.
   Its fields are schema,endpoint,status,release_id,tag,release_manifest_sha256,
   apk_sha256,apk_certificate_sha256,package,version_code,published:true,aliases
   (five filename→exact digest entries). Approve report SHA256 independently.
3. `coordinated_promotion.py --state durable-promotion.json --plan PLAN
   --approved-plan-sha256 DIGEST --endpoint github --report READBACK
   --approved-report-sha256 REPORT_DIGEST` commits the GitHub verified phase with
   file lock/fsync. If interrupted after GitHub publication, obtain readback of
   that same release; do not rerun its draft-only publication path.
4. Ingest the same approved manifest/APK into the standard fdroidserver path.
   Approve signed generation digest and repository receipt, use existing local
   generation journal/provider plan to upload immutable content before mutable
   indexes, then read back APKs/index signatures and generation independently.
5. Record endpoint `repository` with schema1 readback using common fields above
   plus repository_generation_sha256,index_signatures_verified:true. Its digest
   requires independent approval. This phase requires prior GitHub readback.
   Same approved reports are idempotent; another plan/APK/generation fails closed.
   Existing interrupted generation publication resumes with identical bytes.

A journal records approved verifier evidence; it is not a signed attestation or
an HTTP verifier. It performs no signing/upload/publish/credential access and
cannot turn self-authored PASS JSON into independent approval. Endpoint verifier
revisions/reports must be externally trusted. Candidate jobs get no privileged
credentials. Current production orchestration remains manual and disabled in
hosted PR jobs. Fault tests cover journal interruption, replay, missing GitHub,
substituted inputs, divergent aliases and unverified repository signatures.

GitHub and HTTP are never atomic. Clients crossing repository generations must
verify signed referenced digests and retry refresh; preserve old generations and
immutable APK URLs. Rollback indexes never rewrites APK bytes or disguises a
downgrade. Native-input publication has separate approval and no APK key reuse.
