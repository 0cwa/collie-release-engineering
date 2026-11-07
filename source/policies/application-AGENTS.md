# Collie: mandatory patch-first development

Collie uses a layered, patch-based release system. A checkout may contain pristine upstream source on a legacy patch branch or reconstructed application source on a publication branch. Neither layout permits manually authoring application changes in those source files; the approved patch chain is the authority.

## Hard rule

**Never manually edit application code in this repository.** This includes source, resources, native code, dependency inputs and build logic, on every branch, fork, worktree and source-publication candidate. A checkout containing ordinary application files, or lacking patch tools, does not waive this rule.

Do not open or merge an application-code change here first and promise to export a patch later. Do not treat a direct-source contribution guide, an unmerged migration PR, passing tests or green layout CI as permission to do so.

Reading, diagnosis and testing are allowed. Temporary implementation work belongs in a disposable reconstruction outside this published checkout, as part of producing the canonical patch. Its source edits are not the deliverable.

## Required route for application changes

1. Resolve the approved patch recipe and input revisions before editing. Legacy patch branches keep the canonical forest, `molly-build.json`, tools and `docs/PATCH-PIPELINE.md` here. After an approved extraction, use [collie-release-engineering](https://github.com/0cwa/collie-release-engineering) and read its root instructions, layer workflow, `source/README.md` and `source/PUBLICATION.md`. An unmerged source-publication branch or missing patch directory does not transfer authoring authority; verify the current approved location and publication plan rather than choosing a nearby staging checkout.
2. Classify the change in the documented layer structure: upstream snapshot, compatibility adaptation, behavior-preserving fix, behavior addition, or attributed fork change. Keep pipeline/tool changes separate. Do not choose a layer to avoid replaying later layers; clarify an ambiguous classification before proceeding.
3. Author and retain the canonical patch and its tests in that layer. Preserve the immutable upstream forest, layer order, dependencies, source authorship and attribution. Record source commits, exact before/after trees and computed patch/bundle hashes and prerequisites. Never hand-edit hashes to make a check pass or refresh unrelated patches for line offsets.
4. Replay the affected chain from its pinned inputs. Run the applicable scope, provenance, complete-tree, port-report/line-origin, native-input, build and test checks. Report unmeasured, failed and not-run checks accurately. A source-tree proof, successful compilation, native reproduction, signing and live-device acceptance are separate claims.
5. Regenerate published source from the reviewed engineering layers. Verify the complete generated tree and the approved publication plan/receipt, including generated endpoint and integration parents. Only that generated source update may carry application changes into this repository. A source-only layout check is not patch-parity or publication approval.

If the patch route or its required inputs cannot be verified, retain the candidate in scratch and report the missing dependency. Do not fall back to manual application edits here.

## Documentation and coordination

- User-requested documentation and policy corrections may be prepared here. Update their canonical producer as well when the publication layer generates them; regeneration must not restore contradictory instructions.
- The published `AGENTS.md` is emitted from engineering's `source/policies/application-AGENTS.md`. Keep that template and the generated copy identical.
- Collie currently ships one consolidated application. Legacy filenames and historical profile APIs are not separate current editions or an instruction to run their old build matrix.
- Preserve the user's README introduction and tone. Put brief source/build links near the bottom; keep detailed agent and developer instructions in their own documents.
- Respect the owners and order of existing PRs. Read their scope before changing shared pipeline rules, and do not rewrite another agent's branch or candidate approvals. A changed recipe, layer or publication base requires fresh bound evidence.
