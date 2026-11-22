# Collie release engineering agent notes

- `docs/DEVELOPMENT-CYCLE.md` is the workflow. It wins over older docs here. The historical patch composer is archived ([archive/README.md](archive/README.md)); do not use it for app work.
- App code changes go in ordinary PRs to `0cwa/collieim-android`, not as patch layers here.
- `main` takes PRs only, with required checks green and signed commits. Agents merge their own PR with `gh pr merge`, without asking first, once it has the evidence in that file's "Merging PRs" section, except where that file says otherwise.
- This repo is public. Name only projects with a public repo, or Collie Pack, and describe anything else generically ("the push provider", "the distributor").
