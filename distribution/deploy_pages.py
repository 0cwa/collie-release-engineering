"""Deploy an uploaded Pages artifact and poll its official deployment API."""
from __future__ import annotations

import argparse
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
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_WAIT_SECONDS = 1800
POLL_SECONDS = 5
MAX_API_ERRORS = 10
CANCEL_TIMEOUT_SECONDS = 5
FINAL_FAILURES = {
    "deployment_failed",
    "deployment_perms_error",
    "deployment_content_failed",
    "deployment_cancelled",
    "deployment_lost",
}


class PagesApiError(RuntimeError):
    pass


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

    def _json_request(self, method: str, url: str, *, bearer: str,
                      payload: dict | None = None, timeout: float = 30) -> dict:
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
            with self.open_url(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as error:
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
        if not isinstance(value, dict):
            raise PagesApiError("Pages API returned an unexpected response")
        return value

    def get_oidc_token(self) -> str:
        request = Request(
            self.oidc_url,
            headers={"Authorization": f"Bearer {self.oidc_request_token}", "Accept": "application/json"},
        )
        try:
            with self.open_url(request, timeout=30) as response:
                value = json.loads(response.read())
        except HTTPError as error:
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
        deployment_id = response.get("id")
        page_url = response.get("page_url", "")
        if (isinstance(deployment_id, bool) or not isinstance(deployment_id, (str, int))
                or str(deployment_id) in (".", "..")
                or not re.fullmatch(r"[A-Za-z0-9._-]+", str(deployment_id))):
            raise PagesApiError("Pages deployment response did not contain an ID; creation outcome is unknown")
        if not isinstance(page_url, str):
            _cancel_and_raise(api=self, deployment_id=str(deployment_id), error=PagesApiError(
                "Pages deployment response contained an invalid URL"
            ))
        return str(deployment_id), page_url

    def status(self, deployment_id: str, *, timeout: float = 30) -> str:
        response = self._json_request(
            "GET", f"{self.api_url}/repos/{self.repository}/pages/deployments/{deployment_id}",
            bearer=self.github_token, timeout=timeout,
        )
        value = response.get("status")
        if not isinstance(value, str) or not value:
            raise PagesApiError("Pages deployment status response was invalid")
        return value

    def cancel(self, deployment_id: str) -> None:
        self._json_request(
            "POST", f"{self.api_url}/repos/{self.repository}/pages/deployments/{deployment_id}/cancel",
            bearer=self.github_token, timeout=CANCEL_TIMEOUT_SECONDS,
        )


def _cancel_and_raise(api: PagesApi, deployment_id: str, error: Exception) -> None:
    try:
        api.cancel(deployment_id)
    except (PagesApiError, InterruptedError):
        raise PagesApiError(
            f"{error}; Pages deployment cancellation request failed for {deployment_id}; "
            "final service state is unknown"
        ) from None
    raise type(error)(
        f"{error}; cancellation requested for Pages deployment {deployment_id}; "
        "final service state is unconfirmed"
    ) from None


def deploy_and_wait(api: PagesApi, artifact_id: int, build_version: str, *,
                    wait_seconds: int = DEFAULT_WAIT_SECONDS, poll_seconds: int = POLL_SECONDS,
                    clock=time.monotonic, sleep=time.sleep, interrupted=lambda: False) -> str:
    if wait_seconds <= 600 or poll_seconds <= 0:
        raise ValueError("wait must exceed 600 seconds and polling interval must be positive")
    if interrupted():
        raise InterruptedError("Pages deployment interrupted before creation")
    deployment_id = None
    pending = True
    try:
        deployment_id, page_url = api.create(artifact_id, build_version)
        deadline = clock() + wait_seconds
        api_errors = 0
        while True:
            if interrupted():
                raise InterruptedError("Pages deployment interrupted")
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("Pages deployment exceeded its final wait deadline")
            try:
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
                    return page_url
                if status in FINAL_FAILURES:
                    pending = False
                    raise PagesApiError(f"Pages deployment {deployment_id} ended with status {status}")
            remaining = deadline - clock()
            if remaining > 0:
                sleep(min(poll_seconds, remaining))
    except (PagesApiError, TimeoutError, InterruptedError) as error:
        if pending and deployment_id is not None:
            _cancel_and_raise(api, deployment_id, error)
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
    args = parser.parse_args()
    if args.artifact_id <= 0:
        parser.error("artifact ID must be positive")
    build_version = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", build_version):
        parser.error("GITHUB_SHA must be a full commit SHA")
    api = PagesApi(
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""),
        os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
    )
    interrupted = False

    def request_interrupt(_signum, _frame):
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        # A flag alone leaves blocking I/O asleep past the runner's kill grace.
        # Further signals must not interrupt the best-effort cancellation request.
        raise InterruptedError("Pages deployment interrupted")

    old_handlers = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, request_interrupt)
        try:
            page_url = deploy_and_wait(
                api, args.artifact_id, build_version,
                wait_seconds=args.timeout_seconds, interrupted=lambda: interrupted,
            )
        except (PagesApiError, TimeoutError, InterruptedError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        _write_output("page_url", page_url)
        print(f"Pages deployment succeeded; build {build_version}")
        return 0
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
