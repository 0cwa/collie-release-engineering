# Security tool ownership and application handoff

This small export preserves the existing Collie SARIF report/review gate and alert
snapshot tool from application PR42 (d6001052447be56625bec2ba2b60533972d4b296),
plus the bounded screenshot-data parser adapted from PR44
(541b82f135bef6ffe115cf1171a3a424faae5270) and consolidated on application commit
a08a9140a0522881d84d051614ffa7b72ea379ca. Original Git attribution remains in those public commits.
Mechanical copied-file hashes are listed below; adapted screenshot workflow tests
remain in the application bridge, while engineering retains the parser tests.
No JNI builder or Spinner source is included. This is engineering tooling coverage,
not Android application coverage or independent native reproduction.

The engineering workflow analyzes actual Python and Actions with security-extended,
retains raw SARIF and applies this one finding/extraction gate. Upload remains
controlled by CODEQL_ADVANCED_UPLOAD, default off. Do not enable advanced upload
until default-setup migration is separately approved. Hosted CodeQL execution is
NOT RUN for this local draft; local parser/report/template tests are recorded.

For clean published source, render tools/codeql/publication with
`python tools/codeql/render_publication.py --engineering-revision REVIEWED_DURABLE_COMMIT --output NEW_LAYER`.
Review and apply all five files as an explicit publication layer BEFORE G, then
rebuild/test that exact source. No workflow/build injects these files at compile
time. The immutable policy checkout supplies screenshot tools and the same SARIF
gate. Future app coverage is Actions-only until a reviewed traced Android build
with verified public native inputs proves Java/Kotlin extraction. Do not copy the
obsolete patch-repository JNI TU/Spinner into clean source or engineering.
The engineering tools commit does not name final G: the rendered layer pins the
already durable policy revision, and a later receipt names G, avoiding a cycle.

Current app PR42 bridge retains local tools until cutover. After this dependency
is merged and the published source layer is approved, engineering is authoritative.
PR44 duplicate SARIF tools/buildless C++/weaker editor changes are intentionally
not copied; unique screenshot trust checks were ported. No signing/deploy secrets,
production key, settings activation or source behavior changes are introduced.

Rollback: revert the prep feature before cutover; leave current bridge/default
CodeQL configuration active. After cutover, revert a reviewed workflow layer and
regenerate/retest a new G, never move release tags or rewrite retained history.

Copied/adapted source hashes:

- tools/codeql/publication/codeql-config.yml 61d120d00ff16b9c25303e245d6a08b286b9836f3c29a822b81d03f9f9601e0e
- tools/codeql/publication/codeql.yml.in 418f93f60267c2c82c2531423202c77d8170f26f1db28e4a3266961e5d335de4
- tools/codeql/publication/publish-screenshots.yml.in 5c265a22ed73167507b5fc2210033e73b1e177cb792deded9e9b70128c3fe897
- tools/codeql/publication/reviewed-findings.json d1240327219f44e01b73eeae84e9b53aea21d51d1cca3da0dd3d62fc2ceaa465
- tools/codeql/publication/update-screenshots.yml.in 11858bbbe6125cd904e28b4312958eb08c0b45a76d4a60ac047fda79f8afaef9
- tools/codeql/render_publication.py 285bf4c28e8468de1d16b71b57b7b43cb26ddb1586b46ee0d3efc43d61cbf63a
- tools/codeql/report.py 6f525ef1b50becfc9f22fc7c9ccd41db37abf2aa43fefeb9ebb77fcb0876dc31
- tools/codeql/snapshot.py 33c839bf5b88408e11d67326c427a7d8a12049bc5a2a545c090901321a875bf2
- tools/codeql/test_render_publication.py b51526557f6fd4ac985dc256903a3e6035797804eb345436ef79a1efa19fcffc
- tools/codeql/test_report.py 50df2bd90eab9f033bee69e4d55cdeb0fdcf9655aef8fbaa6da46b4c491b3d4f
- tools/codeql/test_reviews.py 7cc67e635f67e36d88ea33ffd98e08a616bf17830213b2f038f6b9b373d9a576
- tools/codeql/test_snapshot.py f990fbfff70330686ae23b9cb202fd9110eff3e21192cca2c58ddd47792b762f
- tools/screenshots/screenshot_artifacts.py 886a49ae3dd1d0fc458d49b86b2016cd64f10e72905a6f87e66505cacb28e9bb
- tools/screenshots/test_screenshot_artifacts.py 280422c033f7c88638a9ebe1970bdb745c75f90339d402f130586ec54ffcaf04

Pull request runs must load both this report tool and the reviewed-findings
manifest from the base commit; pushes and manual runs use the analyzed commit.
This stops a pull request from using its own manifest or report implementation
to approve findings. The workflow definition can still be changed in a pull
request, so repositories must require external review and protect workflow edits
with branch protection. This arrangement does not make workflow YAML tamperproof.
