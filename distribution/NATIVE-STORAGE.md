# Native storage decision package — not activated

The selected binary-feed/landing host is GitHub Pages. Native Maven inputs should
use a separately approved immutable HTTPS origin: proposed
`https://native.collie.im/maven/v1/`, backed by the existing small six-file R2
publication route. This proposal is not permission to create a bucket, domain,
DNS record, account, key or upload. Current `repo.collie.im/native-inputs/v1/maven/`
source inputs remain NOT PUBLISHED; changing the URL layer/locks requires review
before final G generation.

`native-origin-proposal.json` lists every file's exact hash/size, proposed URL and
R2 object key. No native binary is included in the Pages artifact or Git. The
accepted six files total310,836,447bytes; canonical RC1 is97,054,341bytes. Sharing
Pages initially fits407,890,788bytes before indexes/site. One native generation
plus six similarly sized APKs consumes893,162,493bytes; seven consumes990,216,834
before overhead. Two native generations plus four APKs exceeds1GB. Immutable old
native inputs must remain reachable for source builds, so deleting generations
is not an acceptable capacity policy. A separate native origin avoids making
application source retention compete with binary-feed history.

Pages packaging enforces a conservative950,000,000byte total per snapshot/tar,
leaving50MB below the documented1GB limit. With no native files, nine RC1-sized
APKs consume873,489,069bytes before metadata; the exact full artifact still decides
acceptance. At the100GB/month soft transfer limit, about1030 full RC1 downloads
alone consume that budget, before indexes/readback/other traffic. Actual access
volume and CDN behavior remain unmeasured. Do not promise a free quota or infer
existing R2/DNS access.

R2 custom-domain setup requires checking its current account/zone prerequisites;
no suitable Cloudflare zone or account has been established by this task. Domain
registration, DNS ownership, billing and existing host availability remain operator
items. The source repository's native origin tool supplies exact six-file plan
and readback checks; this document supplies the proposed destination, not a second
feed provider framework. The old R2 feed planner is historical/inactive for this
rollout. Pages stays the only selected feed host.

After native-origin approval, the operator freezes account/bucket/domain, object
keys, immutable content/cache policy and six SHA256/size pairs; performs no-overwrite
upload; reads all six canonical HTTPS URLs back; preserves a durable receipt; then
reviews the changed source URL layer before source regeneration. APK signing,
repository-index signing and HTTPS identities remain separate. The binaries retain
trusted-local provenance, with independent native reproduction NOT RUN.

Official constraints: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
and https://developers.cloudflare.com/r2/buckets/public-buckets/ .
