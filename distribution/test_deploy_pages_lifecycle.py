"""Offline regressions for deployment cleanup and failure boundaries."""

import contextlib
import io
import inspect
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from urllib.parse import urlparse
from http.client import BadStatusLine, IncompleteRead
from unittest import mock
from urllib.error import URLError

from distribution import deploy_pages
from distribution.deploy_pages import DeploymentGuard, PagesApi, PagesApiError, deploy_and_wait


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


class NullGuard:
    def __init__(self, *_args, **_kwargs):
        self.records = []

    def __bool__(self):
        return False

    def acquire(self):
        self.records.append("acquire")

    def record_pages_id(self, deployment_id):
        self.records.append(("pages-id", deployment_id))

    def finish(self, state, description):
        self.records.append((state, description))


class PersistentDeploymentApi:
    """Mock only GitHub's REST transport; guard state survives API client instances."""

    def __init__(self, path, *, create_mode="normal", page_states=()):
        self.path = Path(path)
        self.create_mode = create_mode
        self.page_states = list(page_states)
        self.calls = []
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"records": [], "statuses": {}, "pages": [], "page_statuses": {}}

    def save(self):
        self.path.write_text(json.dumps(self.state))

    def deployment_guard_records(self):
        self.calls.append("list-guard")
        return self.state["records"]

    def deployment_guard_statuses(self, deployment_id):
        return self.state["statuses"].get(str(deployment_id), [])

    def create_deployment_guard(self, *, build_version, run_id, run_attempt, artifact_id):
        self.calls.append("create-guard")
        record = {"id": 501, "environment": deploy_pages.GUARD_ENVIRONMENT,
                  "task": deploy_pages.GUARD_TASK,
                  "payload": {"kind": deploy_pages.GUARD_KIND, "run_id": run_id,
                              "run_attempt": run_attempt, "artifact_id": artifact_id}}
        self.state["records"].append(record)
        self.save()
        return record["id"]

    def set_deployment_guard_status(self, deployment_id, state, description):
        self.calls.append(("guard-status", state, description))
        values = self.state["statuses"].setdefault(str(deployment_id), [])
        values.append({"state": state, "description": description,
                       "created_at": f"2026-11-16T00:00:{len(values):02d}Z"})
        self.save()

    def create(self, artifact_id, build_version):
        self.calls.append("create-pages")
        self.state["pages"].append({"id": "pages-77", "artifact_id": artifact_id,
                                     "build_version": build_version})
        self.save()
        if self.create_mode == "response-lost":
            raise PagesApiError("Pages create response was lost")
        return "pages-77", "https://pages.example.test/"

    def status(self, deployment_id, *, timeout=30, interruptible=True):
        if deployment_id == "pages-77":
            if self.page_states:
                value = self.page_states.pop(0)
            else:
                value = self.state["page_statuses"].get(deployment_id, "syncing_files")
            self.state["page_statuses"][deployment_id] = value
            self.save()
            return value
        raise PagesApiError("unknown Pages ID")

    def cancel(self, deployment_id):
        self.calls.append(("cancel", deployment_id))


class PersistentGitHubRest:
    """Durable fake GitHub REST store used through the real PagesApi adapter."""

    def __init__(self, path, *, lose_pages_create_response=False):
        self.path = Path(path)
        self.lose_pages_create_response = lose_pages_create_response
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"records": [], "statuses": {}, "pages": [],
                          "pages_status": {}, "next_id": 700}
            self.save()

    def save(self):
        self.path.write_text(json.dumps(self.state))

    def open(self, request, timeout):
        method = request.get_method()
        parsed = urlparse(request.full_url)
        path = parsed.path
        body = json.loads(request.data) if request.data else None
        if parsed.path == "/token":
            return Response({"value": "fixture-oidc-token"})
        if path.endswith("/repos/owner/repo/deployments") and method == "GET":
            return Response(self.state["records"])
        if path.endswith("/repos/owner/repo/deployments") and method == "POST":
            deployment_id = self.state["next_id"]
            self.state["next_id"] += 1
            self.state["records"].append({"id": deployment_id,
                "environment": body["environment"], "task": body["task"],
                "payload": body["payload"]})
            self.state["statuses"][str(deployment_id)] = []
            self.save()
            return Response({"id": deployment_id})
        status_match = re.search(r"/deployments/(\d+)/statuses$", path)
        if status_match and method == "GET":
            return Response(self.state["statuses"].get(status_match.group(1), []))
        if status_match and method == "POST":
            values = self.state["statuses"].setdefault(status_match.group(1), [])
            values.append({**body, "created_at": f"2026-11-16T00:00:{len(values):02d}Z"})
            self.save()
            return Response({})
        if path.endswith("/pages/deployments") and method == "POST":
            deployment_id = f"pages-{len(self.state['pages']) + 1}"
            self.state["pages"].append({"id": deployment_id, "payload": body})
            self.state["pages_status"][deployment_id] = "syncing_files"
            self.save()
            if self.lose_pages_create_response:
                raise URLError("simulated response loss after server accepted request")
            return Response({"id": deployment_id, "page_url": "https://pages.example.test/"})
        page_status = re.search(r"/pages/deployments/([^/]+)$", path)
        if page_status and method == "GET":
            return Response({"status": self.state["pages_status"][page_status.group(1)]})
        if path.endswith("/cancel") and method == "POST":
            return Response({})
        raise AssertionError(f"unhandled fixture GitHub API request: {method} {path}")


def persistent_pages_api(backend):
    return PagesApi("https://api.github.com", "owner/repo", "fixture-token",
                    "https://oidc.example.test/token", "fixture-request-token", backend.open)


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
        kwargs.setdefault("baseline_check", lambda: None)
        return deploy_and_wait(
            api, 1234, "a" * 40, clock=clock.now, sleep=clock.sleep, **kwargs,
        )

    def test_interruption_before_creation_has_no_side_effect(self):
        api = ScriptedApi([])
        with self.assertRaises(InterruptedError):
            self.run_deployment(api, interrupted=lambda: True)
        self.assertEqual(api.created, [])
        self.assertEqual(api.cancelled, [])

    def test_production_cli_requires_an_explicit_captured_baseline(self):
        with mock.patch.object(sys,'argv',['deploy_pages.py','--artifact-id','1234']), \
                self.assertRaises(SystemExit) as error:
            deploy_pages.main()
        self.assertEqual(error.exception.code,2)

    def test_deploy_order_is_reconcile_exact_baseline_reserve_then_pages_post(self):
        calls=[]
        class Guard:
            def __bool__(self):return True
            def reconcile(self):calls.append('reconcile')
            def acquire(self):calls.append('reserve')
            def record_pages_id(self,_value):pass
            def finish(self,*_args):pass
        class Api(ScriptedApi):
            def create(self,*args):calls.append('pages-post');return super().create(*args)
        api=Api(['succeed'])
        deploy_and_wait(api,1234,'a'*40,guard=Guard(),baseline_check=lambda:calls.append('baseline'))
        self.assertEqual(calls,['reconcile','baseline','reserve','pages-post'])

    def test_old_deployment_finishing_after_capture_blocks_stable_before_reservation(self):
        live={'inventory':'captured-before-old-beta-finished'}
        captured=live['inventory'];calls=[]
        class Guard:
            def __bool__(self):return True
            def reconcile(self):
                calls.append('reconcile-old-deployment')
                # The old Beta deployment completes and changes the served site
                # after this Stable candidate captured its baseline.
                live['inventory']='newer-beta-inventory'
            def acquire(self):calls.append('reserve')
            def record_pages_id(self,_value):pass
            def finish(self,*_args):pass
        api=ScriptedApi(['succeed'])
        def compare_captured_baseline():
            calls.append('compare-captured-baseline')
            if live['inventory']!=captured:raise ValueError('stale captured site baseline')
        with self.assertRaisesRegex(ValueError,'stale captured site baseline'):
            deploy_and_wait(api,1234,'a'*40,guard=Guard(),baseline_check=compare_captured_baseline)
        self.assertEqual(calls,['reconcile-old-deployment','compare-captured-baseline'])
        self.assertEqual(api.created,[])

    def test_failed_job_rerun_rechecks_old_prepared_baseline_before_new_post(self):
        current={'site':'newer-publication'}
        api=ScriptedApi(['succeed'])
        with self.assertRaisesRegex(ValueError,'does not match this run preparation'):
            deploy_and_wait(api,1234,'a'*40,baseline_check=lambda:
                (_ for _ in ()).throw(ValueError('captured baseline does not match this run preparation')))
        self.assertEqual(api.created,[])

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
            deploy_and_wait(api, 1234, "a" * 40, clock=interrupted_clock, baseline_check=lambda: None)
        self.assertEqual(len(api.created), 1)
        self.assertEqual(api.cancelled, ["deployment-1"])

    def test_interrupted_sleep_cancels_once(self):
        api = ScriptedApi(["syncing_files"])
        clock = Clock()

        def interrupted_sleep(seconds):
            raise InterruptedError("Pages deployment interrupted")

        with self.assertRaises(InterruptedError):
            deploy_and_wait(api, 1234, "a" * 40, clock=clock.now, sleep=interrupted_sleep, baseline_check=lambda: None)
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
            deploy_and_wait(api, 1234, "a" * 40, clock=clock.now, sleep=clock.sleep, baseline_check=lambda: None)
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

    def _persistent_guard(self, api, run_id=12):
        return DeploymentGuard(api, build_version="a" * 40, run_id=run_id,
                               run_attempt=1, artifact_id=1234)

    def _reconcile_and_reserve(self, guard):
        guard.reconcile()
        guard.acquire()

    def test_runner_loss_after_prepost_hold_blocks_a_fresh_workflow_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-api-state.json"
            first = PersistentDeploymentApi(state)
            self._reconcile_and_reserve(self._persistent_guard(first))
            self.assertNotIn("create-pages", first.calls)

            second = PersistentDeploymentApi(state)
            with self.assertRaisesRegex(PagesApiError, "remains active; publication held"):
                self._reconcile_and_reserve(self._persistent_guard(second, run_id=13))
            self.assertNotIn("create-pages", second.calls)

    def test_lost_pages_create_response_retains_persistent_hold_and_prevents_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-api-state.json"
            first = PersistentDeploymentApi(state, create_mode="response-lost")
            guard = self._persistent_guard(first)
            with self.assertRaisesRegex(PagesApiError, "response was lost"):
                deploy_and_wait(first, 1234, "a" * 40, guard=guard, baseline_check=lambda: None)
            self.assertEqual(first.calls[:3], ["list-guard", "create-guard",
                                               ("guard-status", "in_progress",
                                                "Pages deployment reserved; Pages deployment ID not yet recorded")])
            self.assertEqual(first.calls.count("create-pages"), 1)

            second = PersistentDeploymentApi(state)
            with self.assertRaisesRegex(PagesApiError, "remains active; publication held"):
                self._reconcile_and_reserve(self._persistent_guard(second, run_id=13))
            self.assertNotIn("create-pages", second.calls)

    def test_lost_create_response_hold_survives_fresh_real_http_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-rest.json"
            backend = PersistentGitHubRest(state, lose_pages_create_response=True)
            api = persistent_pages_api(backend)
            guard = DeploymentGuard(api, build_version="a" * 40, run_id=12,
                                   run_attempt=1, artifact_id=1234)
            with self.assertRaisesRegex(PagesApiError, "creation outcome may be unknown"):
                deploy_and_wait(api, 1234, "a" * 40, guard=guard, baseline_check=lambda: None)
            self.assertEqual(len(backend.state["pages"]), 1)
            self.assertEqual(backend.state["statuses"]["700"][-1]["state"], "in_progress")

            # A fresh client sees the REST-backed deployment marker and blocks
            # before sending a second Pages create request.
            backend2 = PersistentGitHubRest(state)
            api2 = persistent_pages_api(backend2)
            guard2 = DeploymentGuard(api2, build_version="b" * 40, run_id=13,
                                     run_attempt=1, artifact_id=1235)
            with self.assertRaisesRegex(PagesApiError, "remains active; publication held"):
                self._reconcile_and_reserve(guard2)
            self.assertEqual(len(backend2.state["pages"]), 1)

    def test_cancel_204_without_terminal_status_keeps_guard_active_across_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-api-state.json"
            clock = Clock()
            api = PersistentDeploymentApi(state, page_states=["syncing_files"] * 1000)
            guard = self._persistent_guard(api)
            with self.assertRaisesRegex(PagesApiError, "durable publication hold retained"):
                deploy_and_wait(api, 1234, "a" * 40, wait_seconds=601,
                                poll_seconds=601, clock=clock.now, sleep=clock.sleep,
                                guard=guard, baseline_check=lambda: None)
            self.assertTrue(any(call == ("cancel", "pages-77") for call in api.calls))
            statuses = api.state["statuses"]["501"]
            self.assertEqual(statuses[-1]["state"], "in_progress")
            self.assertEqual(statuses[-1]["description"], "pages-deployment-id:pages-77")

            later = PersistentDeploymentApi(state, page_states=["syncing_files"])
            with self.assertRaisesRegex(PagesApiError, "remains active; publication held"):
                self._reconcile_and_reserve(self._persistent_guard(later, run_id=13))
            self.assertNotIn("create-pages", later.calls)

    def test_only_verified_terminal_pages_status_resolves_guard_for_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-api-state.json"
            first = PersistentDeploymentApi(state, page_states=["succeed"])
            url = deploy_and_wait(first, 1234, "a" * 40, guard=self._persistent_guard(first), baseline_check=lambda: None)
            self.assertEqual(url, "https://pages.example.test/")
            self.assertEqual(first.state["statuses"]["501"][-1]["state"], "success")

            second = PersistentDeploymentApi(state)
            self._reconcile_and_reserve(self._persistent_guard(second, run_id=13))
            self.assertIn("create-guard", second.calls)

    def test_guard_terminal_claim_alone_cannot_clear_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "github-api-state.json"
            first = PersistentDeploymentApi(state)
            guard = self._persistent_guard(first)
            self._reconcile_and_reserve(guard)
            guard.record_pages_id("pages-77")
            guard.finish("success", "pages-terminal:pages-77:succeed")

            # The durable marker says success, but the authoritative Pages status
            # store still says the remote deployment is active.
            later = PersistentDeploymentApi(state)
            with self.assertRaisesRegex(PagesApiError, "matching authoritative Pages status"):
                self._reconcile_and_reserve(self._persistent_guard(later, run_id=13))

    def test_main_restores_original_signal_handlers(self):
        original = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        try:
            with mock.patch.object(deploy_pages, "PagesApi", return_value=ScriptedApi(["succeed"])), \
                 mock.patch.object(deploy_pages, "DeploymentGuard", NullGuard), \
                 mock.patch.object(sys, "argv", ["deploy_pages.py", "--artifact-id", "1234", "--feed-baseline", "fixture.json"]), \
                 mock.patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1"}, clear=True), \
                 mock.patch("feed.verify_deploy_baseline"), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(deploy_pages.main(), 0)
            for number, handler in original.items():
                self.assertEqual(signal.getsignal(number), handler)
        finally:
            for number, handler in original.items():
                signal.signal(number, handler)

    def run_main(self, api, *, clock=None):
        clock = clock or Clock()
        run = deploy_pages.deploy_and_wait
        stderr = io.StringIO()
        with mock.patch.object(deploy_pages, "PagesApi", return_value=api), \
             mock.patch.object(deploy_pages, "DeploymentGuard", NullGuard), \
             mock.patch.object(sys, "argv", ["deploy_pages.py", "--artifact-id", "1234", "--feed-baseline", "fixture.json"]), \
             mock.patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1"}, clear=True), \
             mock.patch("feed.verify_deploy_baseline"), \
             mock.patch.object(deploy_pages, "deploy_and_wait", side_effect=lambda *a, **kw: run(
                 *a, **kw, clock=clock.now, sleep=clock.sleep,
             )), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            result = deploy_pages.main()
        return result, stderr.getvalue()

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_signals_during_creation_result_handoff_cancel_known_id(self):
        # Signal at each boundary after a usable response, including before the
        # caller has assigned its ID. Use actual handlers, not a fake exception.
        boundaries = (
            (PagesApi._json_request, "        return value"),
            (PagesApi.create, "        return str(deployment_id), page_url"),
            (deploy_pages.deploy_and_wait, "        deadline = clock() + wait_seconds"),
        )
        for signum in (signal.SIGINT, signal.SIGTERM):
            for function, statement in boundaries:
                with self.subTest(signal=signum, boundary=statement):
                    api, calls = api_with_responses([])
                    source, start = inspect.getsourcelines(function)
                    target = start + next(i for i, line in enumerate(source)
                                          if line.rstrip() == statement)
                    signalled = []

                    def trace(frame, event, arg):
                        if (not signalled and event == "line"
                                and frame.f_code is function.__code__
                                and frame.f_lineno == target):
                            signalled.append(True)
                            os.kill(os.getpid(), signum)
                        return trace

                    previous = sys.gettrace()
                    sys.settrace(trace)
                    try:
                        result, stderr = self.run_main(api)
                    finally:
                        sys.settrace(previous)
                    self.assertEqual(signalled, [True])
                    self.assertEqual(result, 1)
                    self.assertIn("cancellation requested", stderr)
                    self.assertEqual(sum(url.endswith("/deployment-1/cancel")
                                         for _, url, _ in calls), 1)
                    self.assertFalse(any(method == "GET" and "/pages/deployments/" in url
                                         for method, url, _ in calls))

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_signal_during_creation_response_close_retains_buffered_id(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            for wrapped in (False, True):
                with self.subTest(signal=signum, wrapped=wrapped):
                    api, calls = api_with_responses([])
                    open_url = api.open_url

                    class SignalOnClose:
                        def __enter__(self):
                            return Response({"id": "deployment-1", "page_url": "https://pages.test/"})

                        def __exit__(self, *_args):
                            try:
                                os.kill(os.getpid(), signum)
                            except InterruptedError as error:
                                if wrapped:
                                    raise URLError(error) from None
                                raise

                    def open_with_signal_on_close(request, timeout):
                        response = open_url(request, timeout)
                        if request.full_url.endswith("/pages/deployments"):
                            return SignalOnClose()
                        return response

                    api.open_url = open_with_signal_on_close
                    result, stderr = self.run_main(api)
                    self.assertEqual(result, 1)
                    self.assertIn("cancellation requested", stderr)
                    self.assertEqual(sum(url.endswith("/deployment-1/cancel")
                                         for _, url, _ in calls), 1)

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_first_signal_during_non_signal_cleanup_allows_request_to_finish(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            for reason in ("malformed_metadata", "error_budget", "deadline"):
                with self.subTest(signal=signum, reason=reason):
                    clock = Clock()
                    api, calls = api_with_responses(
                        [IncompleteRead(b"", 1)] * 10 if reason == "error_budget" else [],
                        created={"id": "deployment-1", "page_url": 123}
                        if reason == "malformed_metadata" else None,
                    )
                    open_url = api.open_url
                    completed = []

                    def signal_during_cancel(request, timeout):
                        if request.full_url.endswith("/cancel"):
                            os.kill(os.getpid(), signum)
                            response = open_url(request, timeout)
                            completed.append(True)
                            return response
                        if reason == "deadline" and "/pages/deployments/" in request.full_url:
                            clock.value = 1801
                            return Response({"status": "syncing_files"})
                        return open_url(request, timeout)

                    api.open_url = signal_during_cancel
                    result, stderr = self.run_main(api, clock=clock)
                    self.assertEqual(result, 1)
                    self.assertEqual(completed, [True])
                    self.assertEqual(sum(url.endswith("/deployment-1/cancel")
                                         for _, url, _ in calls), 1)
                    self.assertIn("cancellation requested", stderr)
                    self.assertNotIn("cancellation request failed", stderr)

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_first_signal_still_interrupts_creation_network_waits(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            for route in ("oidc", "create"):
                with self.subTest(signal=signum, route=route):
                    api, calls = api_with_responses([])
                    open_url = api.open_url
                    reached_after_signal = []

                    def interrupt_open(request, timeout):
                        is_oidc = request.full_url == "https://oidc.example.test/token"
                        if is_oidc == (route == "oidc"):
                            os.kill(os.getpid(), signum)
                            reached_after_signal.append(True)
                        return open_url(request, timeout)

                    api.open_url = interrupt_open
                    result, stderr = self.run_main(api)
                    self.assertEqual(result, 1)
                    self.assertIn("interrupted", stderr)
                    self.assertEqual(reached_after_signal, [])
                    self.assertFalse(any(url.endswith("/cancel") for _, url, _ in calls))
                    self.assertEqual(len(calls), 0 if route == "oidc" else 1)

    @unittest.skipUnless(os.name == "posix", "The publication runner uses POSIX signals")
    def test_signals_interrupt_blocked_io_and_second_signal_allows_cleanup(self):
        # A socketpair exercises a real blocking read without contacting any service.
        source = textwrap.dedent("""
            import os
            import signal
            import socket
            import sys
            from pathlib import Path
            from distribution import deploy_pages

            # Match the production script's module search path even with no
            # caller-supplied PYTHONPATH; this subprocess uses python -c.
            sys.path.insert(0, str(Path(deploy_pages.__file__).resolve().parent))
            os.environ['GITHUB_SHA'] = 'a' * 40
            os.environ['GITHUB_RUN_ID'] = '12'
            os.environ['GITHUB_RUN_ATTEMPT'] = '1'
            sys.argv = ['deploy_pages.py', '--artifact-id', '1234']
            sys.argv += ['--feed-baseline', 'fixture.json']
            import feed
            feed.verify_deploy_baseline = lambda _path: None

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
            deploy_pages.DeploymentGuard = lambda *args, **kwargs: type('Guard', (), {
                '__bool__': lambda self: False,
                'acquire': lambda self: None,
                'record_pages_id': lambda self, value: None,
                'finish': lambda self, state, description: None,
            })()
            sys.exit(deploy_pages.main())
        """)
        with tempfile.TemporaryDirectory(prefix="pages-signal-") as temporary:
            script = Path(temporary) / "deployment-step.sh"
            # Actions sends cancellation to the shell's PID, not its children.
            # Match the workflow's exec so Python becomes that entry process.
            script.write_text(
                f"exec {shlex.quote(sys.executable)} -B -c {shlex.quote(source)}\n",
                encoding="utf-8",
            )
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(signum=signum):
                    process = subprocess.Popen(
                        ["/bin/bash", "-e", str(script)],
                        cwd=Path(__file__).resolve().parents[1],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        start_new_session=True,
                        env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'},
                    )
                    try:
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            self.assertTrue(selector.select(timeout=5), "child did not reach status polling")
                        first_line=process.stdout.readline().strip()
                        if not first_line:
                            _,stderr=process.communicate(timeout=2)
                            self.fail('child exited before status polling: '+stderr)
                        self.assertEqual(first_line, "STATUS_WAITING")
                        process.send_signal(signum)
                        try:
                            stdout, stderr = process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.fail("entry-process signal did not interrupt the status read promptly")
                        self.assertEqual(process.returncode, 1, stderr)
                        self.assertEqual(stdout.count("CANCELLED"), 1, stdout + stderr)
                        self.assertIn("interrupted", stderr.lower())
                    finally:
                        # Also clean up a child if a broken shell invocation fails.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
