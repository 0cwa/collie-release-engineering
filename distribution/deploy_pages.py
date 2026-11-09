"""Deploy an uploaded Pages artifact and poll its official deployment API."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_WAIT_SECONDS = 1800
POLL_SECONDS = 5
MAX_API_ERRORS = 10
FINAL_FAILURES = {
    "deployment_failed",
    "deployment_perms_error",
    "deployment_content_failed",
    "deployment_cancelled",
    "deployment_lost",
}


class PagesApiError(RuntimeError):
    pass


class PagesApi:
    def __init__(self, api_url: str, repository: str, github_token: str,
                 oidc_url: str, oidc_request_token: str, open_url=urlopen):
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
        self.open_url = open_url

    def _json_request(self, method: str, url: str, *, bearer: str,
                      payload: dict | None = None, timeout: float = 30) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {bearer}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.open_url(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as error:
            raise PagesApiError(f"Pages API request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
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
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise PagesApiError("OIDC token request failed") from None
        token = value.get("value") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise PagesApiError("OIDC token response did not contain a token")
        return token

    def create(self, artifact_id: int, build_version: str) -> tuple[str, str]:
        payload = {
            "artifact_id": artifact_id,
            "pages_build_version": build_version,
            "oidc_token": self.get_oidc_token(),
        }
        response = self._json_request(
            "POST", f"{self.api_url}/repos/{self.repository}/pages/deployments",
            bearer=self.github_token, payload=payload,
        )
        deployment_id = response.get("id")
        page_url = response.get("page_url", "")
        if not isinstance(deployment_id, (str, int)) or not re.fullmatch(r"[A-Za-z0-9._-]+", str(deployment_id)):
            raise PagesApiError("Pages deployment response did not contain an ID")
        if not isinstance(page_url, str):
            raise PagesApiError("Pages deployment response contained an invalid URL")
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
            bearer=self.github_token,
        )


def _cancel_and_raise(api: PagesApi, deployment_id: str, error: Exception) -> None:
    try:
        api.cancel(deployment_id)
    except PagesApiError:
        raise PagesApiError(f"{error}; Pages deployment cancellation request failed") from None
    raise error


def deploy_and_wait(api: PagesApi, artifact_id: int, build_version: str, *,
                    wait_seconds: int = DEFAULT_WAIT_SECONDS, poll_seconds: int = POLL_SECONDS,
                    clock=time.monotonic, sleep=time.sleep, interrupted=lambda: False) -> str:
    if wait_seconds <= 600 or poll_seconds <= 0:
        raise ValueError("wait must exceed 600 seconds and polling interval must be positive")
    deployment_id, page_url = api.create(artifact_id, build_version)
    deadline = clock() + wait_seconds
    api_errors = 0
    while True:
        if interrupted():
            _cancel_and_raise(api, deployment_id, InterruptedError("Pages deployment interrupted"))
        remaining = deadline - clock()
        if remaining <= 0:
            _cancel_and_raise(api, deployment_id, TimeoutError("Pages deployment exceeded its final wait deadline"))
        try:
            status = api.status(deployment_id, timeout=min(30, remaining))
        except PagesApiError as error:
            api_errors += 1
            if api_errors >= MAX_API_ERRORS:
                _cancel_and_raise(api, deployment_id, error)
        else:
            if interrupted():
                _cancel_and_raise(api, deployment_id, InterruptedError("Pages deployment interrupted"))
            if status == "succeed":
                return page_url
            if status in FINAL_FAILURES:
                raise PagesApiError(f"Pages deployment ended with status {status}")
            if deadline - clock() <= 0:
                _cancel_and_raise(api, deployment_id, TimeoutError("Pages deployment exceeded its final wait deadline"))
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(poll_seconds, remaining))


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
        interrupted = True

    signal.signal(signal.SIGINT, request_interrupt)
    signal.signal(signal.SIGTERM, request_interrupt)
    try:
        page_url = deploy_and_wait(
            api, args.artifact_id, build_version,
            wait_seconds=args.timeout_seconds, interrupted=lambda: interrupted,
        )
    except (PagesApiError, TimeoutError, InterruptedError, ValueError) as error:
        import sys
        print(str(error), file=sys.stderr)
        return 1
    _write_output("page_url", page_url)
    print(f"Pages deployment succeeded; build {build_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
