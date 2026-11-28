"""Deploy an uploaded Pages artifact and poll its official deployment API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime
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


class _EmptyPagesStatus(PagesApiError):
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
        if value == "":
            raise _EmptyPagesStatus("Pages deployment status response was empty")
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

    def pages_environment_deployments(self) -> list[dict]:
        value = self._json_request(
            "GET", f"{self.api_url}/repos/{self.repository}/deployments?environment=github-pages&per_page=100",
            bearer=self.github_token,
        )
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise PagesApiError("GitHub returned invalid Pages environment deployments")
        return value

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
        self.completed_id = None

    @staticmethod
    def _timestamp(value: object) -> str:
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                return value
            except ValueError:
                pass
        raise PagesApiError("GitHub deployment timestamp is invalid")

    @classmethod
    def _latest_status(cls, statuses: list[dict], *, durable_guard=False) -> dict | None:
        if not statuses:
            return None
        latest_time = max(cls._timestamp(item.get("created_at")) for item in statuses)
        latest = [item for item in statuses if item["created_at"] == latest_time]
        keys = ("state", "description", "log_url", "environment_url")
        if any(any(item.get(key) != latest[0].get(key) for key in keys) for item in latest[1:]):
            if durable_guard and len(latest) == 2:
                active, terminal = latest
                if terminal.get("state") == "in_progress":
                    active, terminal = terminal, active
                description = active.get("description")
                marker = (re.fullmatch(r"pages-deployment-id:([A-Za-z0-9._-]+)", description)
                          if isinstance(description, str) else None)
                # GitHub timestamps have second precision. The two durable markers
                # can share a timestamp, but callers still verify the Pages API.
                if (active.get("state") == "in_progress" and marker and marker[1] not in (".", "..")
                        and terminal.get("state") == "success"
                        and terminal.get("description") == f"pages-terminal:{marker[1]}:succeed"
                        and all(item.get("environment") == GUARD_ENVIRONMENT
                                and item.get("log_url") == item.get("environment_url") == "" for item in latest)):
                    return terminal
            raise PagesApiError("GitHub latest deployment statuses are ambiguous")
        return latest[0]

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

    def _reconcile_or_block(self, record: dict, current_id: int, matched_environments: set[int]) -> None:
        guard_id = self._guard_id(record)
        latest = self._latest_status(self.api.deployment_guard_statuses(guard_id), durable_guard=True)
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
                except _EmptyPagesStatus:
                    if state == "success" and guard_id < current_id:
                        current_environment = self.require_current(current_id)
                        previous_environment = self._historical_environment_success(record, latest, current_environment)
                        if previous_environment is not None and previous_environment not in matched_environments:
                            matched_environments.add(previous_environment)
                            return
                    raise PagesApiError(
                        f"Guard {guard_id} has an empty Pages status without verified supersession; publication held"
                    ) from None
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

    def _historical_environment_success(self, guard: dict, terminal: dict, current_environment: int) -> int | None:
        payload = guard["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        run_id = payload.get("run_id")
        created = self._timestamp(guard.get("created_at"))
        if type(run_id) is not int or run_id <= 0:
            return None

        job_url = re.escape(f"https://github.com/{self.api.repository}/actions/runs/{run_id}/job/")
        matches = set()
        for deploy in self.api.pages_environment_deployments():
            if deploy.get("sha") != guard.get("sha"):
                continue
            deployment_id = self._guard_id(deploy)
            if deployment_id >= current_environment:
                continue
            statuses = self.api.deployment_guard_statuses(deployment_id)
            latest = self._latest_status(statuses)
            if not latest or latest.get("state") != "inactive":
                continue
            log = latest.get("log_url")
            if not isinstance(log, str) or not re.fullmatch(job_url + r"[1-9][0-9]*", log):
                continue
            started = any(status.get("state") == "in_progress" and status.get("log_url") == log
                          and status["created_at"] <= created for status in statuses)
            succeeded = any(status.get("state") == "success" and status.get("log_url") == log
                            and status.get("environment_url") == "https://repo.collie.im/"
                            and created <= terminal["created_at"] <= status["created_at"] < latest["created_at"]
                            for status in statuses)
            if started and succeeded:
                matches.add(deployment_id)
        return next(iter(matches)) if len(matches) == 1 else None

    def reconcile(self) -> None:
        records = [record for record in self.api.deployment_guard_records() if self._is_our_guard(record)]
        if records:
            current_id = max(self._guard_id(record) for record in records)
            matched_environments: set[int] = set()
            for record in records:
                self._reconcile_or_block(record, current_id, matched_environments)

    def require_current(self, expected_id: int, *, same_attempt=False) -> int:
        records = [record for record in self.api.deployment_guard_records() if self._is_our_guard(record)]
        if not records:
            raise PagesApiError("Current Pages deployment guard is missing")
        current = max(records, key=self._guard_id)
        if self._guard_id(current) != expected_id:
            raise PagesApiError("Current Pages deployment guard differs from the approved predecessor")
        payload = current["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if same_attempt and (
            payload.get("run_id") != self.run_id or payload.get("run_attempt") != self.run_attempt
        ):
            raise PagesApiError("Previous phase deployment belongs to a different run attempt")
        latest = self._latest_status(self.api.deployment_guard_statuses(expected_id), durable_guard=True)
        terminal = re.fullmatch(
            r"pages-terminal:([A-Za-z0-9._-]+):succeed",
            latest.get("description", "") if latest else "",
        )
        if not terminal or latest.get("state") != "success" or terminal[1] in (".", ".."):
            raise PagesApiError("Current Pages deployment lacks a successful terminal identity")
        if self.api.status(terminal[1], interruptible=False) != "succeed":
            raise PagesApiError("Current Pages deployment is not successful in the Pages API")
        return self._require_pages_environment(current, payload)

    def _require_pages_environment(self, guard: dict, payload: dict) -> int:
        own_jobs = f"https://github.com/{self.api.repository}/actions/runs/{self.run_id}/job/"
        old_run = payload.get("run_id")
        if type(old_run) is not int or old_run <= 0:
            raise PagesApiError("Current Pages deployment guard lacks its run")
        previous_jobs = f"https://github.com/{self.api.repository}/actions/runs/{old_run}/job/"
        records = self.api.pages_environment_deployments()
        for record in sorted(records, key=self._guard_id, reverse=True):
            deployment_id = self._guard_id(record)
            latest = self._latest_status(self.api.deployment_guard_statuses(deployment_id))
            state = latest.get("state") if latest else None
            log = latest.get("log_url") if latest else None
            if state in ("error", "failure", "inactive"):
                continue
            if (state in GUARD_ACTIVE_STATES and record.get("sha") == self.build_version and
                    isinstance(log, str) and re.fullmatch(re.escape(own_jobs) + r"[1-9][0-9]*", log)):
                continue
            if (state == "success" and record.get("sha") == guard.get("sha") and
                    isinstance(log, str) and re.fullmatch(re.escape(previous_jobs) + r"[1-9][0-9]*", log) and
                    latest.get("environment_url") == "https://repo.collie.im/"):
                return deployment_id
            break
        raise PagesApiError("The latest Pages environment deployment differs from the approved predecessor")

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
            if state == "success":
                self.completed_id = self.deployment_id
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
    baseline.add_argument("--bootstrap-baseline", type=Path)
    parser.add_argument("--bootstrap-site", type=Path)
    parser.add_argument("--pages-journal", type=Path)
    parser.add_argument("--phase", choices=("phase1", "phase2"))
    parser.add_argument("--channel", choices=("stable", "trailblazer-beta"))
    args = parser.parse_args()
    if args.bootstrap_baseline is not None:
        if not args.bootstrap_site or args.pages_journal or args.phase or args.channel:
            parser.error("bootstrap baseline requires --bootstrap-site and no Pages plan options")
        def baseline_check():
            import bootstrap_inventory
            bootstrap_inventory.verify_deploy_baseline(args.bootstrap_baseline, args.bootstrap_site)
    elif args.bootstrap_site is not None:
        parser.error("--bootstrap-site requires --bootstrap-baseline")
    elif args.feed_baseline is not None:
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
            previous = pages.native_deployment_guard(plan)
            second = args.phase == "phase2"
            if previous and second:
                phase1_id = os.environ.get("COLLIE_PHASE1_GUARD_ID", "")
                if not re.fullmatch(r"[1-9][0-9]*", phase1_id):
                    raise ValueError("Successful phase 1 deployment identity is required")
                previous = int(phase1_id)
                if json.loads(journal.read_text()).get("deployment_guards", {}).get("phase1") != previous:
                    raise ValueError("Recorded phase 1 deployment identity differs")
            if previous:
                guard.require_current(previous, same_attempt=second)
            pages.verify_deploy_baseline(
                plan, args.phase, journal, expected_channel=args.channel,
            )
            if previous:
                guard.require_current(previous, same_attempt=second)
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
        if args.pages_plan and guard and guard.completed_id:
            _write_output("guard_id", str(guard.completed_id))
        print(f"Pages deployment succeeded; build {build_version}")
        return 0
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
