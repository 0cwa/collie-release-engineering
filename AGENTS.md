# Collie release engineering agent notes

- `docs/DEVELOPMENT-CYCLE.md` is the workflow. It wins over older docs here, including `source/PUBLICATION.md` and the contributor sections of `docs/PATCH-PIPELINE.md`.
- App code changes go in ordinary PRs to `0cwa/collieim-android`, not as patch layers here.
- `main` takes PRs only, with required checks green and signed commits. Agents merge their own PR with `gh pr merge` once required checks are green, without asking first, except where that file says otherwise. Its "Merging PRs" section sets the evidence each kind of PR needs.
- This repo is public. Name only projects with a public repo, or Collie Pack, and describe anything else generically ("the push provider", "the distributor").
