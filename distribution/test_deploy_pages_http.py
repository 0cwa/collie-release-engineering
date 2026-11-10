"""Offline credential-boundary checks for the Pages HTTP client."""
import contextlib
import io
import json
import unittest
from email.message import Message
from http.client import HTTPException
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import BaseHandler, build_opener
from urllib.response import addinfourl

from distribution.deploy_pages import PagesApi, PagesApiError, _NoRedirect


API_URL = "https://api.github.com"
OIDC_URL = "https://oidc.example.test/token"
DEPLOYMENTS_URL = f"{API_URL}/repos/owner/repo/pages/deployments"
GITHUB_TOKEN = "fake-github-token"
REQUEST_TOKEN = "fake-request-token"
OIDC_TOKEN = "fake-issued-token"


class Response:
    def __init__(self, value=None, *, raw=None):
        self.raw = json.dumps(value).encode() if raw is None else raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.raw


class SyntheticTransport(BaseHandler):
    """Exercise urllib's redirect machinery without opening any sockets."""
    handler_order = 100

    def __init__(self, redirect_source, redirect_target, redirect_code):
        self.source = redirect_source
        self.target = redirect_target
        self.code = redirect_code
        self.calls = []

    def https_open(self, request):
        self.calls.append(request)
        headers = Message()
        code = 200
        value = {"status": "succeed"}
        if request.full_url == self.source:
            code = self.code
            headers["Location"] = self.target
            value = {}
        elif request.full_url == OIDC_URL:
            value = {"value": OIDC_TOKEN}
        response = addinfourl(io.BytesIO(json.dumps(value).encode()), headers,
                              request.full_url, code=code)
        response.msg = "Found" if code != 200 else "OK"
        return response

    http_open = https_open


def make_api(open_url=None):
    return PagesApi(API_URL, "owner/repo", GITHUB_TOKEN, OIDC_URL,
                    REQUEST_TOKEN, open_url=open_url)


class PagesHttpBoundaryTest(unittest.TestCase):
    def test_default_transport_never_follows_credentialed_redirects(self):
        routes = {
            "oidc": (OIDC_URL, lambda api: api.get_oidc_token()),
            "create": (DEPLOYMENTS_URL, lambda api: api.create(987, "a" * 40)),
            "status": (f"{DEPLOYMENTS_URL}/deployment-1",
                       lambda api: api.status("deployment-1")),
            "cancel": (f"{DEPLOYMENTS_URL}/deployment-1/cancel",
                       lambda api: api.cancel("deployment-1")),
        }
        targets = ("https://other.example.test/leak",
                   "http://other.example.test/leak",
                   "http://api.github.com/leak",
                   "https://api.github.com/another-endpoint")
        for route, (source, invoke) in routes.items():
            for code in (301, 302, 303, 307, 308):
                for target in targets:
                    with self.subTest(route=route, code=code, target=target):
                        transport = SyntheticTransport(source, target, code)

                        def opener_factory(*handlers):
                            self.assertTrue(any(isinstance(h, _NoRedirect) for h in handlers))
                            return build_opener(*handlers, transport)

                        with patch("distribution.deploy_pages.build_opener",
                                   side_effect=opener_factory), contextlib.redirect_stdout(io.StringIO()):
                            api = make_api()
                            with self.assertRaises(PagesApiError):
                                invoke(api)
                        self.assertEqual([r.full_url for r in transport.calls],
                                         [OIDC_URL, source] if route == "create" else [source])

    def test_oidc_mask_precedes_post_and_pages_headers_are_pinned(self):
        calls = []
        log = io.StringIO()
        token = "fake-issued%token\r\nnext-line"
        mask = "::add-mask::fake-issued%25token%0D%0Anext-line\n"

        def open_url(request, timeout):
            calls.append(request)
            if request.full_url == OIDC_URL:
                return Response({"value": token})
            self.assertEqual(log.getvalue(), mask)
            if request.full_url == DEPLOYMENTS_URL:
                return Response({"id": "deployment-1", "page_url": "https://pages.example.test/"})
            if request.get_method() == "GET":
                return Response({"status": "succeed"})
            return Response(raw=b"")

        with contextlib.redirect_stdout(log):
            api = make_api(open_url)
            deployment_id, page_url = api.create(987, "b" * 40)
            self.assertEqual(api.status(deployment_id, timeout=7.5), "succeed")
            api.cancel(deployment_id)

        self.assertEqual(page_url, "https://pages.example.test/")
        self.assertEqual(log.getvalue(), mask)
        oidc_headers = {k.lower(): v for k, v in calls[0].header_items()}
        self.assertEqual(oidc_headers["authorization"], f"Bearer {REQUEST_TOKEN}")
        self.assertEqual(calls[0].full_url, OIDC_URL)
        self.assertEqual(calls[0].get_method(), "GET")
        self.assertEqual(json.loads(calls[1].data), {
            "artifact_id": 987, "pages_build_version": "b" * 40, "oidc_token": token,
        })
        for request in calls[1:]:
            headers = {k.lower(): v for k, v in request.header_items()}
            self.assertEqual(headers["authorization"], f"Bearer {GITHUB_TOKEN}")
            self.assertEqual(headers["x-github-api-version"], "2022-11-28")
            self.assertEqual(headers["accept"], "application/vnd.github+json")
        self.assertEqual([r.get_method() for r in calls], ["GET", "POST", "GET", "POST"])

    def test_transport_errors_do_not_expose_credentials_or_response_bodies(self):
        private = " ".join((GITHUB_TOKEN, REQUEST_TOKEN, OIDC_TOKEN))
        for route in ("oidc", "pages"):
            for kind in ("http", "url", "protocol"):
                with self.subTest(route=route, error=kind):
                    def open_url(request, timeout):
                        if kind == "http":
                            raise HTTPError(request.full_url, 503, private, Message(),
                                            io.BytesIO(private.encode()))
                        if kind == "url":
                            raise URLError(private)
                        raise HTTPException(private)

                    api = make_api(open_url)
                    log = io.StringIO()
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        with self.assertRaises(PagesApiError) as caught:
                            api.get_oidc_token() if route == "oidc" else api.status("deployment-1")
                    output = str(caught.exception) + log.getvalue()
                    for credential in (GITHUB_TOKEN, REQUEST_TOKEN, OIDC_TOKEN):
                        self.assertNotIn(credential, output)

    def test_http_error_response_is_closed_before_discarding_it(self):
        for route in ("oidc", "pages"):
            with self.subTest(route=route):
                body = io.BytesIO(b"private response")
                def open_url(request, timeout):
                    raise HTTPError(request.full_url, 503, "private message", Message(), body)
                api = make_api(open_url)
                with self.assertRaises(PagesApiError):
                    api.get_oidc_token() if route == "oidc" else api.status("deployment-1")
                self.assertTrue(body.closed)

    def test_malformed_responses_fail_without_logging_raw_data(self):
        private = " ".join((GITHUB_TOKEN, REQUEST_TOKEN, OIDC_TOKEN))
        responses = (private.encode(), b"\xff", b"", b"[]", b"null", b"true",
                     json.dumps({"unexpected": private}).encode())
        for route in ("oidc", "pages"):
            for raw in responses:
                with self.subTest(route=route, raw=raw):
                    api = make_api(lambda request, timeout: Response(raw=raw))
                    log = io.StringIO()
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        with self.assertRaises(PagesApiError) as caught:
                            api.get_oidc_token() if route == "oidc" else api.status("deployment-1")
                    output = str(caught.exception) + log.getvalue()
                    for credential in (GITHUB_TOKEN, REQUEST_TOKEN, OIDC_TOKEN):
                        self.assertNotIn(credential, output)


if __name__ == "__main__":
    unittest.main()
