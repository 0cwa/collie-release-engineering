# Pages deployment audit — 9 November 2026

## Result and scope

PR #11's custom deployment client has reproducible credential-redirect and
cancellation defects. Focused corrections and offline regressions are prepared
against main commit `9cbf2c865a5973398847c12d0d024114b0031aad`. The publication
workflow, token permissions, artifact packaging, signed content, and readback
implementation are not changed by these corrections.

The review compared:

- The supplied PR head, `fc2f3cd68b380853fbefa9e947b888330872834e`.
- The merged PR head, `f0f3f3c2dbd8df16232e088cb5429896e31c3a09`.
- Main at the audit base, `9cbf2c865a5973398847c12d0d024114b0031aad`.
- The exact former official deployment action,
  `actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e`.
- The pinned uploader,
  `actions/upload-pages-artifact@5f327015a708106f6f1b7c9a633901af30fe80cb`.

The custom deployer, deployment workflow, and original deployment/workflow tests
are identical across the supplied PR head, merged head, and audit base. Later
readback/recovery changes in `pages.py` were identified and preserved; this audit
does not independently endorse that separate recovery policy.

No merge, workflow dispatch, protected deployment, environment approval, or
publication-setting change was performed for this audit.

## Updated live evidence

The handoff was accurate about the inspected run, but later repository activity
changes the available evidence. Times in this technical record are UTC.

| Event | Observed result |
| --- | --- |
| PR #11, 02:48:34 on 9 November | Already merged at `f0f3f3c2…` before this audit. |
| Run #42559808017, started 04:05:04 | Custom phase1 deployment succeeded; phase1 canonical readback failed; phase2 and final readback were skipped. |
| Run #42569065555, started 05:44:59 | Preparation, both custom deployment jobs, and both canonical readback jobs succeeded. Final job completed at 05:49:22. |

For the successful run, the phase1 deployment step took approximately 12 seconds
and phase2 approximately 2 seconds. Its deployer has the same SHA-256
(`9a3f483ad2b116a1d0e247290b50ceda2aff3add0a6abc777dcb29dcbd40cf95`)
as the deployer at the supplied PR head. This is live happy-path evidence for the
implementation under review. It is not evidence of a deployment requiring more
than ten minutes, successful cancellation, or resilience to broken HTTP responses.

The successful run's approval-history API returned an empty array. Neither that
response nor a workflow's `environment: github-pages` declaration establishes
the presence of independent reviewers, prevention of self-review, or protected
branch rules. Live environment administration settings were not established here.

Sources: [PR #11](https://github.com/0cwa/collie-release-engineering/pull/11),
[earlier post-merge run](https://github.com/0cwa/collie-release-engineering/actions/runs/42559808017),
[successful run](https://github.com/0cwa/collie-release-engineering/actions/runs/42569065555).
The run conclusions and individual job/step conclusions were read through the
GitHub API; no run was initiated or retried.

## Findings and corrections

### 1. Credentialed redirects leave the intended endpoint

**Priority: high; trigger requires an unexpected redirect from the configured
HTTPS endpoint. No production exploit or induced GitHub redirect was observed.**

The original `PagesApi` uses the default `urllib.request.urlopen`. Its redirect
handling preserves the explicitly supplied Authorization header, including across
hosts and HTTPS-to-HTTP redirects. An offline reproduction using the actual urllib
opener and an in-memory transport observed:

1. An authenticated HTTPS Pages status request.
2. A synthetic 302 to an HTTP URL on another host.
3. A second request carrying the same fake bearer credential.
4. The client accepting `succeed` from that redirected response.

The same default transport is used for OIDC retrieval, deployment creation,
status, and cancellation. Validating only the initial URL's scheme does not
protect subsequent requests.

**Correction:** the default opener now rejects redirects. API and OIDC
credentials and the creation payload stay on the configured initial request;
redirect responses become sanitized failures. Tests exercise real urllib redirect
processing for all four request types, five redirect codes, and four target
classes: 80 subcases, with no external network or real credentials.

The official pin is not claimed to be flawless in all redirect paths. Its Pages
REST client uses Octokit/native fetch; its bundled OIDC client blocks HTTPS
downgrades but has separate bearer-handler behavior. Denying redirects avoids
depending on those subtleties.

### 2. Cancellation signals can miss the runner's termination window

**Priority: high for operational cleanup. Remote cancellation remains best effort.**

The original SIGINT/SIGTERM handler only sets a flag. A status request can remain
blocked for its 30-second socket timeout, and a poll sleep can delay recognition.
A local subprocess/socket-pair reproduction remained blocked with no cancellation
after SIGINT plus 7.5 seconds and SIGTERM plus another 2.5 seconds.

GitHub documents those grace periods before killing the process tree. The
original implementation therefore cannot be relied on to issue its cancellation
request during runner cancellation.

**Correction:** the first signal raises an interruption immediately; repeated
signals allow the one cleanup attempt to proceed. Both HTTP adapters preserve
direct interruptions and `URLError(InterruptedError(...))`, which urllib can
produce during connect/send. Known-ID bookkeeping and deadline initialization
are inside the cleanup guard. Interruption already known before creation causes
no creation side effect. Original signal handlers are restored when `main()`
returns.

Cancellation uses a five-second **socket inactivity timeout**, not a guaranteed
wall-clock bound. Tests use real POSIX signals and blocked socket reads in a
subprocess, plus a second signal during cleanup. They do not emulate every
runner or network condition.

Source: [GitHub workflow cancellation reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation).

### 3. Truncated or malformed HTTP transport responses bypass cleanup

**Priority: medium.**

`http.client.IncompleteRead` and `BadStatusLine` inherit from `HTTPException`,
not `OSError`. The original adapters do not catch them. A truncated status body
therefore escapes the deployment monitor without consuming the error budget or
attempting cancellation.

**Correction:** normalize HTTP protocol exceptions in both adapters to sanitized
`PagesApiError` values, while preserving deliberate interruption separately.
The existing cumulative ten-error limit now applies. Tests cover recovery
followed by success, exhaustion followed by one cancellation request, cancellation
transport failure, malformed JSON, unexpected response shapes, and credential-safe
error messages.

The implementation intentionally retains cumulative error accounting. The exact
official pin also does not reset its error count after a valid pending poll.
The official action adds error-reporting backoff; the custom client retains its
existing five-second polling interval. No new retry of a creation POST was added.

### 4. Malformed creation metadata can abandon a known deployment

**Priority: medium.**

A response such as `{"id":"deployment-1","page_url":123}` creates a known-ID
deployment but fails URL-type validation before the caller receives that ID.
The original code exits without cleanup.

**Correction:** attempt cancellation of the known ID before reporting malformed
output metadata. Invalid IDs are rejected rather than used in an API path.

There is a different, unavoidable uncertainty when the creation response is lost
before any usable ID is received. The client now reports that ambiguity
explicitly. It never blindly retries creation or cancels by build SHA; both
publication phases share that SHA, so a SHA fallback could target a different
deployment.

### 5. Success could be accepted after the advertised final deadline

**Priority: medium contract gap; this behavior also exists in the pinned action.**

The original successful-status branch precedes the post-request deadline check.
A request that finishes after 1,800 seconds can therefore return success and
advance the workflow.

**Correction:** check the deadline before accepting the returned success.
The regression advances a fake clock beyond the deadline inside a status request
and verifies failure and a cancellation attempt. This enforces the custom
client's stated final-deadline policy; it is not described as a regression unique
to this PR relative to the official action.

The deadline governs local polling from creation completion. OIDC retrieval,
creation itself, network operations with inactivity timeouts, and cleanup prevent
this from being a strict total-process wall-clock bound.

### 6. Existing workflow tests did not lock executable readback

**Priority: medium regression gap; no actual readback removal was found.**

The original workflow test searches broadly for substrings and counts. Replacing
the phase1-readback job's real command with `run: 'true'` still passes that test,
because the job name and dependency survive.

**Correction:** add dependency-free, job-scoped assertions for executable readback
steps, conditions, control-journal transfers, phase artifact ID routes, protected
permissions/environments, exact revision checkouts, and activation-mode
preparation/readback. In-memory mutations that remove phase1 readback, remove
final readback, or miswire the phase1 artifact are now rejected.

These are static structural checks, not an interpreter for GitHub expressions
or proof of hosted environment protection.

### Additional protective measures

The official pin obtains OIDC through `core.getIDToken()`, whose bundled
implementation registers the issued JWT for masking. The custom implementation
omitted that registration, although its ordinary/error paths did not demonstrably
print credentials.

The correction registers the issued token with an escaped `::add-mask::`
workflow command before the Pages POST. Tests verify ordering and percent/CR/LF
escaping. Response bodies, bearer values, and raw exception details stay out of
ordinary error messages. This is a missing protective measure, not evidence of
a prior token leak.

The API-version header is now consistent for create, status, and cancellation.
The existing random-delimiter output writer is retained.

## Artifact identity, OIDC, and publication guarantees

| Area | Audit conclusion |
| --- | --- |
| Artifact selection | The official pin lists artifacts in the current run and requires one matching name. The custom workflow forwards the exact pinned uploader's artifact ID. That ID is not a dispatch input or bundle-supplied value. |
| Uploader contract | `upload-pages-artifact` exposes `artifact_id`, forwarding the nested upload action's `artifact-id`. The workflow's underscore spelling is correct. |
| Phase separation | Phase1 consumes the phase1 uploader output. Phase2 consumes the phase2 output relayed through successful phase1-readback. Different site paths and names remain correctly paired. |
| Provenance limits | No independent digest/attestation check is added to the deployer. GitHub's acceptance of arbitrary stale, cross-run, or foreign IDs was not probed. Extra REST metadata lookup would add permission and availability requirements without fixing a demonstrated substitution path in this workflow. |
| OIDC | Both clients use the Actions runtime's request URL and request bearer, default audience behavior, and the Pages payload fields `artifact_id`, `pages_build_version`, `oidc_token`. GitHub validates JWT claims. No invented audience or client JWT verifier is needed. |
| Credentials | Only the two deployment jobs receive `pages: write` and `id-token: write` and target `github-pages`; their `contents: read` supports the checked-out trusted implementation. No new permission is added. |
| Code authority | Deployment jobs check out `github.sha` with credential persistence disabled. Preparation checks the independently supplied policy revision equals that SHA. Candidate bundles remain data outside that checkout. |
| States | Only `succeed` is success. All five terminal failures match the exact official pin. Unknown and temporary states stay pending. Terminal failures before the deadline do not trigger redundant cancellation. |
| Feed path | Successful preparation → phase1 deployment → exact canonical phase1 readback → phase2 deployment → exact final readback. The live failed-readback run also demonstrates phase2 being skipped on that observed failure. |
| Activate-links path | Phase1 deployment is intentionally skipped; preparation performs the activation preparation, preflight, and phase1 readback before forwarding control evidence. This preexisting exception is preserved. |
| Readback limits | Exact size/hash checks and journal advancement remain the content gate. They are not a provider-side atomic transaction, a publication lock, or proof of all CDN clients' observations. |

The exact official deployment pin hard-caps its timeout at 600,000 milliseconds,
including an explicit `Math.min` clamp. Setting its timeout input to 30 minutes
would not accomplish this PR's stated purpose. The audit therefore does not
propose that ineffective replacement.

Primary comparison sources:
[pinned deployment state machine](https://github.com/actions/deploy-pages/blob/d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e/src/internal/deployment.js),
[pinned artifact/API client](https://github.com/actions/deploy-pages/blob/d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e/src/internal/api-client.js),
[pinned OIDC entry point](https://github.com/actions/deploy-pages/blob/d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e/src/index.js),
[pinned bundled dependencies](https://github.com/actions/deploy-pages/blob/d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e/dist/index.js),
[pinned uploader](https://github.com/actions/upload-pages-artifact/blob/5f327015a708106f6f1b7c9a633901af30fe80cb/action.yml),
[Pages API documentation](https://docs.github.com/en/rest/pages/pages).

## Validation record

Runtime: Python 3.12.14. Broader tests used an isolated virtual environment with
the repository's exact, hash-verified `distribution/site-requirements.txt`
(Pillow 13.0.0 and qrcode 8.2).

- Focused deployer/HTTP/lifecycle/workflow suite: **32 tests passed**, including
  80 redirect subcases and both actual SIGINT/SIGTERM subprocess cases.
- Complete distribution suite: **125 tests passed**.
- Workflow mutation checks: removed phase1 readback, removed final readback,
  and wrong phase1 artifact were rejected.
- `git diff --check`: passed.
- All distribution Python files compile successfully.

The first broad local test attempt lacked qrcode and failed eight existing
rendering/activation tests. Installing the repository's pinned test requirements
resolved that setup issue; no test or production dependency specification was
changed.

No protected service test of the corrected client has been run. Local tests
validate request construction, state handling, and failure behavior under
controlled conditions. They do not prove cancellation completion, absence of
late server-side writes, long-sync behavior, service-side artifact authorization,
or independent live environment approval.

## Review and approval boundary

The correction is suitable for independent code review and ordinary PR checks.
A proposed merge must identify the PR, exact head commit, base, and merge method.
A proposed protected deployment must identify the exact reviewed commit,
workflow/ref, preparation mode, approved bundle asset ID and SHA-256, and the
environment approval being requested. The exact action must be shown for explicit
approval before either operation; this audit authorizes neither.
