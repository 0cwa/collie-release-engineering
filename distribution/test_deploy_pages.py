import json
import unittest
from unittest.mock import Mock

from distribution.deploy_pages import (
    DeploymentGuard, GUARD_ENVIRONMENT, GUARD_KIND, GUARD_TASK, PagesApi, PagesApiError, deploy_and_wait,
)


class FakeClock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeApi:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.cancelled = []
        self.created = []

    def create(self, artifact_id, build_version):
        self.created.append((artifact_id, build_version))
        return "deployment-1", "https://pages.example.test/"

    def status(self, _deployment_id, *, timeout=30):
        self.status_timeouts = getattr(self, "status_timeouts", []) + [timeout]
        value = next(self.statuses)
        if isinstance(value, Exception):
            raise value
        return value

    def cancel(self, deployment_id):
        self.cancelled.append(deployment_id)


class Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.value


class DeployPagesTest(unittest.TestCase):
    def test_current_guard_requires_latest_record_and_authoritative_status(self):
        api = Mock()
        api.repository = "0cwa/collie-release-engineering"
        guard = DeploymentGuard(api, build_version="a"*40, run_id=5, run_attempt=2, artifact_id=23)
        record = {"id":123, "sha":"a"*40, "environment":GUARD_ENVIRONMENT, "task":GUARD_TASK,
                  "payload":{"kind":GUARD_KIND,"run_id":5,"run_attempt":2,"artifact_id":22}}
        api.deployment_guard_records.return_value = [record]
        api.pages_environment_deployments.return_value = [{"id":124,"sha":"a"*40}]
        statuses = {
            123: {"created_at":"2026-11-18T01:00:00Z","state":"success","description":"pages-terminal:deploy-a:succeed"},
            124: {"created_at":"2026-11-18T01:00:01Z","state":"success",
                  "log_url":f"https://github.com/{api.repository}/actions/runs/5/job/9",
                  "environment_url":"https://repo.collie.im/"},
        }
        api.deployment_guard_statuses.side_effect = lambda identity: [statuses[identity]]
        api.status.return_value = "succeed"
        guard.require_current(123,same_attempt=True)
        api.status.assert_called_once_with("deploy-a",interruptible=False)
        api.deployment_guard_records.return_value = [record,dict(record,id=124)]
        with self.assertRaisesRegex(PagesApiError,"approved predecessor"):
            guard.require_current(123)
        api.deployment_guard_records.return_value = [record]
        api.status.return_value = "deployment_failed"
        with self.assertRaisesRegex(PagesApiError,"Pages API"):
            guard.require_current(123)
        api.status.return_value = "succeed"
        statuses[124]["log_url"] = f"https://github.com/{api.repository}/actions/runs/6/job/9"
        with self.assertRaisesRegex(PagesApiError,"latest Pages environment"):
            guard.require_current(123)
        record["payload"]["run_attempt"] = 1
        with self.assertRaisesRegex(PagesApiError,"different run attempt"):
            guard.require_current(123,same_attempt=True)
        api.create.assert_not_called()

    def test_historical_success_can_be_verified_after_pages_hides_its_status(self):
        api = Mock()
        api.repository = "0cwa/collie-release-engineering"
        guard = DeploymentGuard(api, build_version="c"*40, run_id=7, run_attempt=1, artifact_id=23)

        def record(identity, sha, run_id, created_at):
            return {"id": identity, "sha": sha, "created_at": created_at, "environment": GUARD_ENVIRONMENT,
                    "task": GUARD_TASK, "payload": {"kind": GUARD_KIND, "run_id": run_id}}

        past = record(11, "a"*40, 5, "2026-11-18T17:59:37Z")
        current = record(21, "b"*40, 6, "2026-11-18T21:29:58Z")
        old_job = f"https://github.com/{api.repository}/actions/runs/5/job/100"
        current_job = f"https://github.com/{api.repository}/actions/runs/6/job/101"
        api.deployment_guard_records.return_value = [past, current]
        api.pages_environment_deployments.return_value = [
            {"id": 20, "sha": current["sha"]}, {"id": 10, "sha": past["sha"]},
        ]
        statuses = {
            11: [{"created_at":"2026-11-18T17:59:55Z","state":"success",
                  "description":"pages-terminal:past:succeed"}],
            21: [{"created_at":"2026-11-18T21:30:14Z","state":"success",
                  "description":"pages-terminal:current:succeed"}],
            10: [{"created_at":"2026-11-18T21:30:18Z","state":"inactive","log_url":old_job},
                 {"created_at":"2026-11-18T18:00:11Z","state":"success","log_url":old_job,
                  "environment_url":"https://repo.collie.im/"},
                 {"created_at":"2026-11-18T17:58:29Z","state":"in_progress","log_url":old_job}],
            20: [{"created_at":"2026-11-18T21:30:19Z","state":"success","log_url":current_job,
                  "environment_url":"https://repo.collie.im/"}],
        }
        api.deployment_guard_statuses.side_effect = lambda identity: statuses[identity]

        pages_api = PagesApi("https://api.github.com", api.repository, "fixture", "https://oidc.example.test", "fixture")
        pages_api._json_request = lambda _method, url, **_kwargs: {"status": "succeed" if url.endswith("/current") else ""}
        api.status.side_effect = pages_api.status
        guard.reconcile()
        api.create.assert_not_called()

        incorrect_states = (
            (10, 2, "log_url", current_job),
            (10, 1, "created_at", "2026-11-18T17:59:54Z"),
            (10, 0, "state", "success"),
            (20, 0, "log_url", old_job),
            (11, 0, "state", "in_progress"),
        )
        for deployment_id, index, key, value in incorrect_states:
            with self.subTest(deployment_id=deployment_id, index=index, key=key):
                original = statuses[deployment_id][index][key]
                statuses[deployment_id][index][key] = value
                try:
                    with self.assertRaises(PagesApiError):
                        guard.reconcile()
                finally:
                    statuses[deployment_id][index][key] = original

        duplicate = dict(past, id=12)
        statuses[12] = [dict(statuses[11][0], description="pages-terminal:another-past:succeed")]
        api.deployment_guard_records.return_value = [past, duplicate, current]
        with self.assertRaises(PagesApiError):
            guard.reconcile()
        api.deployment_guard_records.return_value = [past, current]

        statuses[30] = statuses[10]
        api.pages_environment_deployments.return_value = [
            {"id": 20, "sha": current["sha"]}, {"id": 30, "sha": past["sha"]},
        ]
        with self.assertRaises(PagesApiError):
            guard.reconcile()
        api.pages_environment_deployments.return_value = [
            {"id": 20, "sha": current["sha"]}, {"id": 10, "sha": past["sha"]},
        ]

        with self.subTest("conflicting latest status"):
            statuses[10].append(dict(statuses[10][0], state="in_progress"))
            try:
                with self.assertRaises(PagesApiError):
                    guard.reconcile()
            finally:
                statuses[10].pop()

        with self.subTest("malformed timestamp"):
            statuses[10].append(dict(statuses[10][0], created_at="tomorrow"))
            try:
                with self.assertRaises(PagesApiError):
                    guard.reconcile()
            finally:
                statuses[10].pop()

        api.status.side_effect = PagesApiError("transport failed")
        with self.assertRaises(PagesApiError):
            guard.reconcile()

    def test_effective_wait_can_exceed_action_600_second_cap(self):
        clock = FakeClock()
        api = FakeApi(["syncing_files", "syncing_files", "succeed"])
        page_url = deploy_and_wait(
            api, 1234, "a" * 40, wait_seconds=1800, poll_seconds=600.001,
            clock=clock.now, sleep=clock.sleep, baseline_check=lambda:None,
        )
        self.assertGreater(clock.value, 600)
        self.assertEqual(page_url, "https://pages.example.test/")
        self.assertEqual(api.cancelled, [])
        self.assertEqual(api.status_timeouts, [30, 30, 30])

    def test_final_deadline_cancels_deployment(self):
        clock = FakeClock()
        api = FakeApi(["syncing_files", "syncing_files"])
        with self.assertRaisesRegex(TimeoutError, "final wait deadline"):
            deploy_and_wait(
                api, 1234, "b" * 40, wait_seconds=1200, poll_seconds=1199.5,
                clock=clock.now, sleep=clock.sleep, baseline_check=lambda:None,
            )
        self.assertEqual(clock.value, 1200)
        self.assertEqual(api.cancelled, ["deployment-1"])
        self.assertEqual(api.status_timeouts, [30, 0.5])

    def test_interruption_cancels_and_terminal_failure_does_not(self):
        clock = FakeClock()
        api = FakeApi(["syncing_files"])
        with self.assertRaisesRegex(InterruptedError, "interrupted"):
            deploy_and_wait(
                api, 1234, "c" * 40, wait_seconds=1200, poll_seconds=5,
                clock=clock.now, sleep=clock.sleep,
                interrupted=lambda: clock.value >= 5,
                baseline_check=lambda:None,
            )
        self.assertEqual(api.cancelled, ["deployment-1"])

        terminal = FakeApi(["deployment_failed"])
        with self.assertRaisesRegex(PagesApiError, "deployment_failed"):
            deploy_and_wait(terminal, 1234, "d" * 40, clock=clock.now, sleep=clock.sleep, baseline_check=lambda:None)
        self.assertEqual(terminal.cancelled, [])

    def test_pages_api_uses_uploaded_artifact_and_oidc_token(self):
        calls = []

        def open_url(request, timeout):
            calls.append((request, timeout))
            if request.full_url == "https://oidc.example.test/token":
                return Response({"value": "oidc-token-value"})
            if request.method == "POST" and request.full_url.endswith("/pages/deployments"):
                return Response({"id": "deployment-2", "page_url": "https://pages.example.test/"})
            if request.method == "POST":
                return Response({})
            return Response({"status": "succeed"})

        api = PagesApi(
            "https://api.github.com", "owner/repo", "github-api-token",
            "https://oidc.example.test/token", "oidc-request-token", open_url,
        )
        deployment_id, page_url = api.create(987, "e" * 40)
        self.assertEqual((deployment_id, page_url), ("deployment-2", "https://pages.example.test/"))
        payload = json.loads(calls[1][0].data)
        self.assertEqual(payload["artifact_id"], 987)
        self.assertEqual(payload["pages_build_version"], "e" * 40)
        self.assertEqual(payload["oidc_token"], "oidc-token-value")
        self.assertEqual(api.status(deployment_id), "succeed")
        api.cancel(deployment_id)
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
