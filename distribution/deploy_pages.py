"""Deploy an uploaded Pages artifact and poll its official deployment API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import json
import os
import re
import secrets
import signal
import sys
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_WAIT_SECONDS = 1800
POLL_SECONDS = 5
MAX_API_ERRORS = 10
CANCEL_TIMEOUT_SECONDS = 5
CANCEL_CONFIRM_SECONDS = 300
GUARD_ENVIRONMENT = "collie-pages-remote-deploy-guard"
GUARD_TASK = "collie-pages-remote-deploy-guard"
GUARD_KIND = "collie-pages-remote-deploy-guard-v1"
GUARD_ACTIVE_STATES = {"pending", "queued", "in_progress"}
GUARD_TERMINAL_STATES = {"success", "failure", "error", "inactive"}
FINAL_FAILURES = {
    "deployment_failed",
    "deployment_perms_error",
    "deployment_content_failed",
    "deployment_cancelled",
    "deployment_lost",
}


class PagesApiError(RuntimeError):
    pass


class _Interrupts:
    """Raise only in blocking waits; defer signals while handing off state/cleaning up."""

    def __init__(self):
        self.requested = False
        self.waiting = False

    def request(self, _signum, _frame):
        if self.requested:
            return
        self.requested = True
        if self.waiting:
            raise InterruptedError("Pages deployment interrupted")

    @contextmanager
    def wait(self):
        previous = self.waiting
        try:
            self.waiting = True
            if self.requested:
                raise InterruptedError("Pages deployment interrupted")
            yield
        finally:
            self.waiting = previous


class _NoRedirect(HTTPRedirectHandler):
    """Never forward either bearer credential or an OIDC payload to a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirects are not permitted", headers, fp)


def _mask_token(token: str) -> None:
    # Match the Actions toolkit's command escaping; the runner consumes this
    # command and masks subsequent occurrences of the newly issued credential.
    escaped = token.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::add-mask::{escaped}", flush=True)


class PagesApi:
    def __init__(self, api_url: str, repository: str, github_token: str,
                 oidc_url: str, oidc_request_token: str, open_url=None):
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            raise ValueError("GITHUB_REPOSITORY must be owner/repository")
        if urlparse(api_url).scheme != "https":
            raise ValueError("GITHUB_API_URL must use HTTPS")
        if urlparse(oidc_url).scheme != "https":
            raise ValueError("OIDC request URL must use HTTPS")
        if not github_token or not oidc_request_token:
            raise ValueError("GitHub API and OIDC request credentials are required")
        self.api_url = api_url.rstrip("/")
        self.repository = repository
        self.github_token = github_token
        self.oidc_url = oidc_url
        self.oidc_request_token = oidc_request_token
        self.open_url = open_url if open_url is not None else build_opener(_NoRedirect()).open
        self.interruptible = nullcontext

    def _read_response(self, request: Request, timeout: float, *, interruptible=True):
        raw = None
        try:
            with self.interruptible() if interruptible else nullcontext():
                with self.open_url(request, timeout=timeout) as response:
                    raw = response.read()
        except InterruptedError:
            if raw is None:
                raise
        except URLError as error:
            if raw is None or not isinstance(error.reason, InterruptedError):
                raise
        # A signal during response close or wait teardown must not discard a
        # fully buffered creation response. Parse it and hand off the ID; the
        # recorded interruption will then cancel it before the first poll.
        return raw

    def _json_request(self, method: str, url: str, *, bearer: str, payload: dict | None = None,
                      timeout: float = 30, interruptible: bool = True):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {bearer}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            raw = self._read_response(request, timeout, interruptible=interruptible)
        except HTTPError as error:
            error.close()
            raise PagesApiError(f"Pages API request failed with HTTP {error.code}") from None
        except InterruptedError:
            raise
        except URLError as error:
            # urllib wraps interruptions raised during connect/send in URLError.
            if isinstance(error.reason, InterruptedError):
                raise error.reason from None
            raise PagesApiError("Pages API request failed at the transport layer") from None
        except (TimeoutError, OSError, HTTPException):
            raise PagesApiError("Pages API request failed at the transport layer") from None
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PagesApiError("Pages API returned invalid JSON") from None
        if not isinstance(value, (dict, list)):
            raise PagesApiError("GitHub API returned an unexpected response")
        return value

    def get_oidc_token(self) -> str:
        request = Request(
            self.oidc_url,
            headers={"Authorization": f"Bearer {self.oidc_request_token}", "Accept": "application/json"},
        )
        try:
            value = json.loads(self._read_response(request, 30))
        except HTTPError as error:
            error.close()
            raise PagesApiError(f"OIDC token request failed with HTTP {error.code}") from None
        except InterruptedError:
            raise
        except URLError as error:
            if isinstance(error.reason, InterruptedError):
                raise error.reason from None
            raise PagesApiError("OIDC token request failed") from None
        except (TimeoutError, OSError, HTTPException, UnicodeDecodeError, json.JSONDecodeError):
            raise PagesApiError("OIDC token request failed") from None
        token = value.get("value") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise PagesApiError("OIDC token response did not contain a token")
        _mask_token(token)
        return token

    def create(self, artifact_id: int, build_version: str) -> tuple[str, str]:
        payload = {
            "artifact_id": artifact_id,
            "pages_build_version": build_version,
            "oidc_token": self.get_oidc_token(),
        }
        try:
            response = self._json_request(
                "POST", f"{self.api_url}/repos/{self.repository}/pages/deployments",
                bearer=self.github_token, payload=payload,
            )
        except (PagesApiError, InterruptedError) as error:
            # A lost POST response does not prove the service rejected creation.
            # Never retry it or cancel by SHA: both phases use the same build SHA.
            raise type(error)(
                f"{error}; no deployment ID received; creation outcome may be unknown"
            ) from None
        if not isinstance(response, dict):
            raise PagesApiError("Pages API returned an unexpected creation response")
        deployment_id = response.get("id")
        page_url = response.get("page_url", "")
        if (isinstance(deployment_id, bool) or not isinstance(deployment_id, (str, int))
                or str(deployment_id) in (".", "..")
                or not re.fullmatch(r"[A-Za-z0-9._-]+", str(deployment_id))):
            raise PagesApiError("Pages deployment response did not contain an ID; creation outcome is unknown")
        return str(deployment_id), page_url

    def status(self, deployment_id: str, *, timeout: float = 30,
               interruptible: bool = True) -> str:
        response = self._json_request(
            "GET", f"{self.api_url}/repos/{self.repository}/pages/deployments/{deployment_id}",
            bearer=self.github_token, timeout=timeout, interruptible=interruptible,
        )
        if not isinstance(response, dict):
            raise PagesApiError("Pages API returned an unexpected status response")
        value = response.get("status")
        if not isinstance(value, str) or not value:
            raise PagesApiError("Pages deployment status response was invalid")
        return value

    def cancel(self, deployment_id: str) -> None:
        self._json_request(
            "POST", f"{self.api_url}/repos/{self.repository}/pages/deployments/{deployment_id}/cancel",
            bearer=self.github_token, timeout=CANCEL_TIMEOUT_SECONDS, interruptible=False,
        )

    def deployment_guard_records(self) -> list[dict]:
        """Read durable guard deployments written before Pages deployment POSTs."""
        records = []
        for page in range(1, 101):
            query = urlencode({"environment": GUARD_ENVIRONMENT, "task": GUARD_TASK,
                               "per_page": 100, "page": page})
            value = self._json_request(
                "GET", f"{self.api_url}/repos/{self.repository}/deployments?{query}",
                bearer=self.github_token,
            )
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise PagesApiError("GitHub returned an invalid deployment guard inventory")
            records.extend(value)
            if len(value) < 100:
                return records
        raise PagesApiError("GitHub deployment guard inventory exceeded the safe page limit")

    def deployment_guard_statuses(self, deployment_id: int) -> list[dict]:
        value = self._json_request(
            "GET", f"{self.api_url}/repos/{self.repository}/deployments/{deployment_id}/statuses?per_page=100",
            bearer=self.github_token,
        )
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise PagesApiError("GitHub returned invalid deployment guard statuses")
        return value

    def create_deployment_guard(self, *, build_version: str, run_id: int,
                                run_attempt: int, artifact_id: int) -> int:
        response = self._json_request(
            "POST", f"{self.api_url}/repos/{self.repository}/deployments",
            bearer=self.github_token,
            payload={
                "ref": build_version,
                "task": GUARD_TASK,
                "auto_merge": False,
                "required_contexts": [],
                "environment": GUARD_ENVIRONMENT,
                "transient_environment": True,
                "production_environment": False,
                "description": "Pages remote deployment guard; unresolved until verified terminal state",
                "payload": {"kind": GUARD_KIND, "run_id": run_id,
                            "run_attempt": run_attempt, "artifact_id": artifact_id},
            },
        )
        value = response.get("id") if isinstance(response, dict) else None
        if type(value) is not int or value <= 0:
            raise PagesApiError("GitHub deployment guard response did not contain an ID")
        return value

    def set_deployment_guard_status(self, deployment_id: int, state: str, description: str) -> None:
        self._json_request(
            "POST", f"{self.api_url}/repos/{self.repository}/deployments/{deployment_id}/statuses",
            bearer=self.github_token,
            payload={"state": state, "description": description, "auto_inactive": False},
        )


class DeploymentGuard:
    """A durable GitHub deployment marker shared by both production workflows."""

    def __init__(self, api: PagesApi, *, build_version: str, run_id: int,
                 run_attempt: int, artifact_id: int, clock=time.monotonic, sleep=time.sleep):
        self.api = api
        self.build_version = build_version
        self.run_id = run_id
        self.run_attempt = run_attempt
        self.artifact_id = artifact_id
        self.clock = clock
        self.sleep = sleep
        self.deployment_id = None

    @staticmethod
    def _latest_status(statuses: list[dict]) -> dict | None:
        if not statuses:
            return None
        # The list endpoint is normally newest-first; using the explicit timestamp
        # makes the interpretation independent of response ordering.
        valid = [item for item in statuses if isinstance(item.get("created_at"), str)]
        if len(valid) != len(statuses):
            raise PagesApiError("GitHub deployment guard status omitted its timestamp")
        return max(valid, key=lambda item: item["created_at"])

    def _is_our_guard(self, record: dict) -> bool:
        payload = record.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return False
        return (record.get("environment") == GUARD_ENVIRONMENT
                and record.get("task") == GUARD_TASK
                and isinstance(payload, dict) and payload.get("kind") == GUARD_KIND)

    @staticmethod
    def _guard_id(record: dict) -> int:
        value = record.get("id")
        if type(value) is not int or value <= 0:
            raise PagesApiError("GitHub deployment guard record has an invalid ID")
        return value

    def _reconcile_or_block(self, record: dict) -> None:
        guard_id = self._guard_id(record)
        latest = self._latest_status(self.api.deployment_guard_statuses(guard_id))
        state = latest.get("state") if latest else "pending"
        if state in GUARD_TERMINAL_STATES:
            description = latest.get("description", "") if latest else ""
            terminal = re.fullmatch(
                r"pages-terminal:([A-Za-z0-9._-]+):(succeed|deployment_failed|deployment_perms_error|deployment_content_failed|deployment_cancelled|deployment_lost)",
                description,
            )
            expected_guard_state = "success" if terminal and terminal.group(2) == "succeed" else "failure"
            if terminal and state == expected_guard_state:
                try:
                    actual = self.api.status(terminal.group(1), interruptible=False)
                except PagesApiError as error:
                    raise PagesApiError(
                        f"Guard {guard_id} has a terminal claim but Pages status cannot be verified; publication held"
                    ) from error
                if actual == terminal.group(2):
                    return
            raise PagesApiError(
                f"Guard {guard_id} terminal state lacks matching authoritative Pages status; publication held"
            )
        if state not in GUARD_ACTIVE_STATES:
            raise PagesApiError(f"Unknown Pages deployment guard state {state!r}; publication held")
        description = latest.get("description", "") if latest else ""
        match = re.fullmatch(r"pages-deployment-id:([A-Za-z0-9._-]+)", description)
        if match:
            try:
                pages_state = self.api.status(match.group(1), interruptible=False)
            except PagesApiError as error:
                raise PagesApiError(
                    f"Unresolved Pages deployment guard {guard_id}; status lookup failed; publication held"
                ) from error
            if pages_state == "succeed":
                self.api.set_deployment_guard_status(
                    guard_id, "success", f"pages-terminal:{match.group(1)}:succeed",
                )
                return
            if pages_state in FINAL_FAILURES:
                self.api.set_deployment_guard_status(
                    guard_id, "failure", f"pages-terminal:{match.group(1)}:{pages_state}",
                )
                return
        raise PagesApiError(
            f"Unresolved Pages deployment guard {guard_id} remains active; publication held"
        )

    def reconcile(self) -> None:
        records = self.api.deployment_guard_records()
        for record in records:
            if self._is_our_guard(record):
                self._reconcile_or_block(record)

    def acquire(self) -> None:
        self.deployment_id = self.api.create_deployment_guard(
            build_version=self.build_version, run_id=self.run_id,
            run_attempt=self.run_attempt, artifact_id=self.artifact_id,
        )
        # Persist an active marker before the Pages deployment creation request.
        self.api.set_deployment_guard_status(
            self.deployment_id, "in_progress", "Pages deployment reserved; Pages deployment ID not yet recorded",
        )

    def record_pages_id(self, pages_deployment_id: str) -> None:
        if self.deployment_id is None:
            raise PagesApiError("Pages deployment guard was not acquired")
        self.api.set_deployment_guard_status(
            self.deployment_id, "in_progress", f"pages-deployment-id:{pages_deployment_id}",
        )

    def finish(self, state: str, description: str) -> None:
        if self.deployment_id is not None:
            self.api.set_deployment_guard_status(self.deployment_id, state, description)
            self.deployment_id = None


def _cancel_and_raise(api: PagesApi, deployment_id: str, error: Exception,
                      guard: DeploymentGuard | None = None, *, clock=time.monotonic,
                      sleep=time.sleep) -> None:
    try:
        api.cancel(deployment_id)
    except (PagesApiError, InterruptedError):
        raise PagesApiError(
            f"{error}; Pages deployment cancellation request failed for {deployment_id}; "
            "final service state is unknown"
        ) from None
    deadline = clock() + CANCEL_CONFIRM_SECONDS
    while clock() < deadline:
        try:
            status = api.status(deployment_id, timeout=min(30, max(1, deadline - clock())),
                                interruptible=False)
        except PagesApiError:
            status = None
        if status == "succeed":
            if guard:
                guard.finish("success", f"pages-terminal:{deployment_id}:succeed")
            break
        if status in FINAL_FAILURES:
            if guard:
                guard.finish("failure", f"pages-terminal:{deployment_id}:{status}")
            break
        sleep(min(POLL_SECONDS, max(0, deadline - clock())))
    else:
        raise PagesApiError(
            f"{error}; cancellation requested for Pages deployment {deployment_id}; "
            "final service state is unconfirmed; durable publication hold retained"
        ) from None
    raise type(error)(f"{error}; Pages deployment {deployment_id} reached a terminal state after cancellation") from None


def deploy_and_wait(api: PagesApi, artifact_id: int, build_version: str, *,
                    wait_seconds: int = DEFAULT_WAIT_SECONDS, poll_seconds: int = POLL_SECONDS,
                    clock=time.monotonic, sleep=time.sleep, interrupted=lambda: False,
                    interruptible=nullcontext, guard: DeploymentGuard | None = None,
                    baseline_check=None) -> str:
    if wait_seconds <= 600 or poll_seconds <= 0:
        raise ValueError("wait must exceed 600 seconds and polling interval must be positive")
    if interrupted():
        raise InterruptedError("Pages deployment interrupted before creation")
    if guard:
        guard.reconcile()
    if baseline_check is None:
        raise ValueError("deploy-time captured baseline check is required")
    baseline_check()
    if guard:
        guard.acquire()
    deployment_id = None
    pending = True
    try:
        deployment_id, page_url = api.create(artifact_id, build_version)
        if guard:
            guard.record_pages_id(deployment_id)
        if not isinstance(page_url, str):
            raise PagesApiError("Pages deployment response contained an invalid URL")
        deadline = clock() + wait_seconds
        api_errors = 0
        while True:
            if interrupted():
                raise InterruptedError("Pages deployment interrupted")
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("Pages deployment exceeded its final wait deadline")
            try:
                with interruptible():
                    status = api.status(deployment_id, timeout=min(30, remaining))
            except PagesApiError:
                # Like the pinned official action, count errors cumulatively.
                api_errors += 1
                if api_errors >= MAX_API_ERRORS:
                    raise
            else:
                if interrupted():
                    raise InterruptedError("Pages deployment interrupted")
                if deadline - clock() <= 0:
                    raise TimeoutError("Pages deployment exceeded its final wait deadline")
                if status == "succeed":
                    pending = False
                    if guard:
                        guard.finish("success", f"pages-terminal:{deployment_id}:succeed")
                    return page_url
                if status in FINAL_FAILURES:
                    pending = False
                    if guard:
                        guard.finish("failure", f"pages-terminal:{deployment_id}:{status}")
                    raise PagesApiError(f"Pages deployment {deployment_id} ended with status {status}")
            remaining = deadline - clock()
            if remaining > 0:
                with interruptible():
                    sleep(min(poll_seconds, remaining))
    except (PagesApiError, TimeoutError, InterruptedError) as error:
        if pending and deployment_id is not None:
            if guard:
                _cancel_and_raise(api, deployment_id, error, guard, clock=clock, sleep=sleep)
            try:
                api.cancel(deployment_id)
            except (PagesApiError, InterruptedError):
                raise PagesApiError(
                    f"{error}; Pages deployment cancellation request failed for {deployment_id}; final state unknown"
                ) from None
            raise type(error)(
                f"{error}; cancellation requested for Pages deployment {deployment_id}; final service state is unconfirmed"
            ) from None
        raise


def _write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        delimiter = f"pages-output-{secrets.token_hex(16)}"
        while delimiter in value:
            delimiter = f"pages-output-{secrets.token_hex(16)}"
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--timeout-seconds", default=DEFAULT_WAIT_SECONDS, type=int)
    baseline = parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument("--feed-baseline", type=Path)
    baseline.add_argument("--pages-plan", type=Path)
    parser.add_argument("--pages-journal", type=Path)
    parser.add_argument("--phase", choices=("phase1", "phase2"))
    parser.add_argument("--channel", choices=("stable", "trailblazer-beta"))
    args = parser.parse_args()
    if args.feed_baseline is not None:
        if args.pages_journal or args.phase or args.channel:
            parser.error("feed baseline cannot be combined with Pages plan options")
        def baseline_check():
            import feed
            feed.verify_deploy_baseline(args.feed_baseline)
    else:
        if not args.pages_journal or not args.phase or not args.channel:
            parser.error("Pages deployment requires --pages-journal, --phase, and --channel")
        def baseline_check():
            import pages
            plan = json.loads(args.pages_plan.read_text())
            journal = args.pages_journal
            pages.verify_deploy_baseline(
                plan, args.phase, journal, expected_channel=args.channel,
            )
    if args.artifact_id <= 0:
        parser.error("artifact ID must be positive")
    build_version = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", build_version):
        parser.error("GITHUB_SHA must be a full commit SHA")
    try:
        run_id = int(os.environ.get("GITHUB_RUN_ID", ""))
        run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    except ValueError:
        parser.error("GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be positive integers")
    if run_id <= 0 or run_attempt <= 0:
        parser.error("GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be positive integers")
    api = PagesApi(
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""),
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
    )
    interrupts = _Interrupts()
    api.interruptible = interrupts.wait
    guard = DeploymentGuard(
        api, build_version=build_version, run_id=run_id, run_attempt=run_attempt,
        artifact_id=args.artifact_id,
    )

    old_handlers = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, interrupts.request)
        try:
            page_url = deploy_and_wait(
                api, args.artifact_id, build_version,
                wait_seconds=args.timeout_seconds, interrupted=lambda: interrupts.requested,
                interruptible=interrupts.wait, guard=guard, baseline_check=baseline_check,
            )
        except (PagesApiError, TimeoutError, InterruptedError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        if interrupts.requested:
            print("Pages deployment interrupted", file=sys.stderr)
            return 1
        _write_output("page_url", page_url)
        print(f"Pages deployment succeeded; build {build_version}")
        return 0
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
