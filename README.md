# Collie release engineering

Public construction, source verification and distribution tooling for [Collie](https://github.com/0cwa/collieim-android). Migration implementation is reviewed in pull requests. This repository does not contain signing keys or grant production publication authority.

Application development is patch-first: author the appropriate audited layer
here and regenerate the published application source. Do not manually edit
application code in the application repository. Read [AGENTS.md](AGENTS.md),
[the layer workflow](docs/PATCH-PIPELINE.md) and
[publication rules](source/PUBLICATION.md) before contributing.
