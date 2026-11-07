# Initial native storage budget — prepared, not activated

GitHub Pages at `https://repo.collie.im/` is the selected initial host for both the
binary feed and exactly one existing native-v1 Maven generation at
`/native-inputs/v1/maven/`. This explicit capacity decision preserves the original
source URLs. No Pages/DNS/TLS setting, key or upload has been activated. The former
separate R2/native.collie.im proposal is an inactive future alternative, not an
initial requirement or a second feed provider.

`native-v1.json` is the trusted six-file policy: three existing binary inputs and
three transformed POMs, exact SHA256 and sizes, totaling310,836,447bytes. The data
bundle must carry an identical `native-manifest.json` with a separately approved
raw SHA256 and the six files beneath `native/`. No arbitrary native file, new
coordinate/generation, symlink, omission or changed byte is admitted. These are
accepted trusted-local inputs; independent native reproduction remains NOT RUN.

Native-v1 plus the canonical RC1 APK is407,890,788bytes before index/site overhead.
One native generation plus six RC1-sized APKs is893,162,493bytes; seven is990,216,834
before overhead, exceeding the950,000,000byte operational ceiling. Both complete
site snapshots AND their tar artifacts must fit this ceiling. Every published
native-v1 file stays reachable in every later snapshot. No automatic pruning or
old-binary overwrite is permitted; growth requires a separate archive/storage
approval. Pages has a documented1GB site limit and100GB soft monthly transfer
limit; six binaries plus indexes/readbacks/download traffic are budgeted together.

A native-only bundle needs no APK, repository-index signer, signed index or add
link. Both snapshots preserve any existing feed/landing; an initial empty site
shows preparation text and no active feed. Initial preflight checks absent
manifest/entry plus every native URL (absent or already identical). Full canonical
HTTPS readback must succeed before a new-source build may use these public inputs.
Later feed publication needs its own approved production index certificate and
signed repository receipt. Test staging keys are never production authorization.

Operator package: exact policy revision, native-manifest raw SHA256, complete
input bundle SHA256, previous published inventory/manifest, both tar SHA256/sizes,
per-file URL/hash/size plan and durable journal. Provision/activate Pages/custom
domain/TLS/protected reviewers only with separate production approval. Read every
canonical URL back before source-build acceptance; preserve the receipt outside
CI retention. R2 account/DNS migration is not requested for this initial route.

Official limits: https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits
