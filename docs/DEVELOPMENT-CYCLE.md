# Collie development cycle

Decided 2026-11-10. Where this conflicts with older docs here (`source/PUBLICATION.md`, `docs/PATCH-PIPELINE.md` contributor sections) or with app `docs/VERIFY.md`, this file wins until those are retired.

## Day-to-day work

- App `main` (`0cwa/collieim-android`) is the source. Change app code directly in ordinary PRs; do not re-import app changes as engineering layers.
- Every non-merge app commit carries one `Layer: <feature>` trailer (lowercase, hyphens), e.g. `Layer: collie-branding`. The app `pr.yml` check enforces it.
- Required app PR checks: source checks, fast lint, Collie unit tests. A debug APK build runs on every PR but is not required.
- `main` in both repos: PRs only, required checks green, signed commits, no force pushes, no required human review. Agents merge with `gh pr merge`. (Enabled after the 8.35.1 release that ships on the old flow.)

## Upstream upgrades (Signal / Molly)

- This repo generates feature layers from app `main` (Collie's changes against the Molly base, grouped by `Layer:` trailer), replays them onto the new base, and opens one app PR. Each conflict fix is its own reviewed patch.
- The Molly/Signal reconstruction proof (`tools/patches`, `patches/molly`, `patches/ports`) stays for upgrades.

## Releases

Process: app `docs/RELEASING.md`.

- The release APK is built by this machine or GitHub, whichever is faster, with app `tooling/reproducible-builds/build-release.sh`.
- The maintainer signs it locally (APK key) and tags it locally (publication key, `WtFs…`, listed in app `.github/release-signers`).
- One `collie.apk` is uploaded. App CI checks it, uploads the profile copies (`collie-foss`, `molly-continued`, `molly-foss-continued`, `molly-minimal`) and publishes a prerelease within minutes.
- A full release is the same APK, promoted only after a rebuild on the other side (GitHub or this machine) matches it.
- Release notes: `distribution/release-notes/<version>.md` here, in the maintainer's style (see existing files). App CI refuses to publish without one. In-app notices (e.g. the later move to one `collie.apk`) ship as ordinary app PRs.
- F-Droid and Pages carry verified full releases only.

## To retire once the new flow has shipped a release

Generated publication commits on app `main` (G/I), the plan/receipt approval chain and the composer (`source/compose_security_publication.py`, `source/publish.py`, candidate plans/receipts). Keep native-input delivery (`repo.collie.im/native-inputs`) and Pages/F-Droid publishing.
