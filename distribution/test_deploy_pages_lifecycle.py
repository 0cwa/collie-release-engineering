"""Offline regressions for deployment cleanup and failure boundaries."""

import contextlib
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import textwrap
import unittest
from http.client import BadStatusLine, IncompleteRead
from unittest import mock
from urllib.error import URLError

from distribution import deploy_pages
from distribution.deploy_pages import PagesApi, PagesApiError, deploy_and_wait


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class ScriptedApi:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.created = []
        self.cancelled = []

    def create(self, artifact_id, build_version):
        self.created.append((artifact_id, build_version))
        return "deployment-1", "https://pages.example.test/"

    def status(self, deployment_id, *, timeout=30):
        value = next(self.statuses)
        if isinstance(value, BaseException):
            raise value
        return value

    def cancel(self, deployment_id):
        self.cancelled.append(deployment_id)


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if isinstance(self.value, BaseException):
            raise self.value
        if isinstance(self.value, bytes):
            return self.value
        return json.dumps(self.value).encode()


def api_with_responses(statuses, *, created=None, cancelled=b""):
    """Exercise the real request adapter, replacing only its network opener."""
    responses = iter(statuses)
    calls = []

    def open_url(request, timeout):
        method = request.get_method()
        calls.append((method, request.full_url, timeout))
        if request.full_url == "https://oidc.example.test/token":
            return Response({"value": "dummy-oidc-token"})
        if request.full_url.endswith("/cancel"):
            return Response(cancelled)
        if method == "POST":
            return Response(created if created is not None else {
                "id": "deployment-1", "page_url": "https://pages.example.test/",
            })
        return Response(next(responses))

    api = PagesApi(
        "https://api.github.com", "owner/repo", "dummy-github-token",
        "https://oidc.example.test/token", "dummy-request-token", open_url,
    )
    return api, calls


class DeployPagesLifecycleTest(unittest.TestCase):
    def run_deployment(self, api, **kwargs):
        clock = Clock()
        return deploy_and_wait(
            api, 1234, "a" * 40, clock=clock.now, sleep=clock.sleep, **kwargs,
        )

    def test_interruption_before_creation_has_no_side_effect(self):
        api = ScriptedApi([])
        with self.assertRaises(InterruptedError):
            self.run_deployment(api, interrupted=lambda: True)
        self.assertEqual(api.created, [])
        self.assertEqual(api.cancelled, [])

    def test_all_known_terminal_failures_stop_without_cancellation(self):
        for status in (
            "deployment_failed", "deployment_perms_error",
            "deployment_content_failed", "deployment_cancelled", "deployment_lost",
        ):
            with self.subTest(status=status):
                api = ScriptedApi([status])
                with self.assertRaisesRegex(PagesApiError, status):
                    self.run_deployment(api)
                self.assertEqual(api.cancelled, [])

    def test_temporary_and_unrecognized_states_do_not_promote_success(self):
        api = ScriptedApi([
            "unknown_status", "not_found", "deployment_attempt_error",
            "syncing_files", "future_nonterminal_status", "succeed",
        ])
        self.assertEqual(self.run_deployment(api), "https://pages.example.test/")
        self.assertEqual(api.cancelled, [])
        self.assertEqual(list(api.statuses), [])

    def test_api_error_limit_is_cumulative_across_successful_pending_polls(self):
        api = ScriptedApi(
            [PagesApiError("temporary API failure")] * 5
            + ["syncing_files"]
            + [PagesApiError("temporary API failure")] * 5
            + ["succeed"]
        )
        with self.assertRaises(PagesApiError):
            self.run_deployment(api)
        self.assertEqual(api.cancelled, ["deployment-1"])
        self.assertEqual(list(api.statuses), ["succeed"])

    def test_truncated_and_malformed_http_responses_are_retryable_errors(self):
        api, calls = api_with_responses([
            IncompleteRead(b'{"stat', 30), BadStatusLine("invalid HTTP status line"),
            {"status": "succeed"},
        ])
        self.assertEqual(self.run_deployment(api), "https://pages.example.test/")
        self.assertEqual(len([call for call in calls if call[0] == "GET"
                              and "/pages/deployments/" in call[1]]), 3)
        self.assertFalse(any(url.endswith("/cancel") for _, url, _ in calls))

    def test_protocol_errors_exhaust_limit_and_cancel_once(self):
        api, calls = api_with_responses([IncompleteRead(b"", 1)] * 10)
        with self.assertRaises(PagesApiError):
            self.run_deployment(api)
        cancellations = [call for call in calls if call[1].endswith("/cancel")]
        self.assertEqual(len(cancellations), 1)
        self.assertGreater(cancellations[0][2], 0)
        self.assertLessEqual(cancellations[0][2], 5)

    def test_cancellation_transport_error_preserves_failure(self):
        api, calls = api_with_responses(
            [IncompleteRead(b"", 1)] * 10,
            cancelled=IncompleteRead(b"", 1),
        )
        with self.assertRaisesRegex(PagesApiError, "cancellation request failed"):
            self.run_deployment(api)
        self.assertEqual(sum(url.endswith("/cancel") for _, url, _ in calls), 1)

    def test_interrupted_status_cancels_once(self):
        api = ScriptedApi([InterruptedError("Pages deployment interrupted")])
        with self.assertRaises(InterruptedError):
            self.run_deployment(api)
        self.assertEqual(api.cancelled, ["deployment-1"])

    def test_interruption_after_create_before_first_poll_cancels_known_id(self):
        api = ScriptedApi([])

        def interrupted_clock():
            raise InterruptedError("Pages deployment interrupted")

        with self.assertRaises(InterruptedError):
            deploy_and_wait(api, 1234, "a" * 40, clock=interrupted_clock)
        self.assertEqual(len(api.created), 1)
        self.assertEqual(api.cancelled, ["deployment-1"])

    def test_interrupted_sleep_cancels_once(self):
        api = ScriptedApi(["syncing_files"])
        clock = Clock()

        def interrupted_sleep(seconds):
            raise InterruptedError("Pages deployment interrupted")

        with self.assertRaises(InterruptedError):
            deploy_and_wait(api, 1234, "a" * 40, clock=clock.now, sleep=interrupted_sleep)
        self.assertEqual(api.cancelled, ["deployment-1"])

    def test_http_and_oidc_adapters_propagate_interruption(self):
        for error in (InterruptedError("Pages deployment interrupted"),
                      URLError(InterruptedError("Pages deployment interrupted"))):
            with self.subTest(error=type(error).__name__):
                def interrupted_open(request, timeout):
                    raise error

                api = PagesApi(
                    "https://api.github.com", "owner/repo", "dummy-github-token",
                    "https://oidc.example.test/token", "dummy-request-token", interrupted_open,
                )
                with self.assertRaises(InterruptedError):
                    api.status("deployment-1")
                with self.assertRaises(InterruptedError):
                    api.get_oidc_token()

    def test_oidc_protocol_failure_is_sanitized(self):
        api, _ = api_with_responses([])
        api.open_url = lambda *args, **kwargs: Response(BadStatusLine("sensitive-response-text"))
        with self.assertRaises(PagesApiError) as failure:
            api.get_oidc_token()
        self.assertNotIn("sensitive-response-text", str(failure.exception))

    def test_success_observed_after_deadline_does_not_advance(self):
        clock = Clock()
        api = ScriptedApi([])

        def late_status(deployment_id, *, timeout=30):
            clock.value = 1801
            return "succeed"

        api.status = late_status
        with self.assertRaises(TimeoutError):
            deploy_and_wait(api, 1234, "a" * 40, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(api.cancelled, ["deployment-1"])

    def test_known_creation_id_is_cancelled_if_output_url_is_malformed(self):
        api, calls = api_with_responses([], created={"id": "deployment-1", "page_url": 123})
        with self.assertRaises(PagesApiError):
            self.run_deployment(api)
        self.assertEqual(sum(url.endswith("/deployment-1/cancel") for _, url, _ in calls), 1)

    def test_ambiguous_create_failure_is_never_retried(self):
        api = ScriptedApi([])

        def ambiguous_create(artifact_id, build_version):
            api.created.append((artifact_id, build_version))
            raise PagesApiError("Pages API request failed at the transport layer")

        api.create = ambiguous_create
        with self.assertRaises(PagesApiError):
            self.run_deployment(api)
        self.assertEqual(len(api.created), 1)
        self.assertEqual(api.cancelled, [])

    def test_main_restores_original_signal_handlers(self):
        original = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        try:
            with mock.patch.object(deploy_pages, "PagesApi", return_value=ScriptedApi(["succeed"])), \
                 mock.patch.object(sys, "argv", ["deploy_pages.py", "--artifact-id", "1234"]), \
                 mock.patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}, clear=True), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(deploy_pages.main(), 0)
            for number, handler in original.items():
                self.assertEqual(signal.getsignal(number), handler)
        finally:
            for number, handler in original.items():
                signal.signal(number, handler)

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_signals_interrupt_blocked_io_and_second_signal_allows_cleanup(self):
        # A socketpair exercises a real blocking read without contacting any service.
        source = textwrap.dedent("""
            import os
            import signal
            import socket
            import sys
            from distribution import deploy_pages

            os.environ['GITHUB_SHA'] = 'a' * 40
            sys.argv = ['deploy_pages.py', '--artifact-id', '1234']

            class Api:
                def __init__(self, *args):
                    pass
                def create(self, *args):
                    return 'deployment-1', 'https://pages.example.test/'
                def status(self, *args, **kwargs):
                    reader, writer = socket.socketpair()
                    reader.settimeout(30)
                    print('STATUS_WAITING', flush=True)
                    try:
                        reader.recv(1)
                    finally:
                        reader.close()
                        writer.close()
                def cancel(self, *args):
                    # Runner escalation must not interrupt the one cleanup attempt.
                    os.kill(os.getpid(), signal.SIGTERM)
                    print('CANCELLED', flush=True)

            deploy_pages.PagesApi = Api
            sys.exit(deploy_pages.main())
        """)
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", source],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=5), "child did not reach status polling")
                    self.assertEqual(process.stdout.readline().strip(), "STATUS_WAITING")
                    process.send_signal(signum)
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.fail("signal did not interrupt a blocking status read promptly")
                    self.assertEqual(process.returncode, 1, stderr)
                    self.assertEqual(stdout.count("CANCELLED"), 1, stdout + stderr)
                    self.assertIn("interrupted", stderr.lower())
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
