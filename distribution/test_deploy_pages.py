import json
import unittest

from distribution.deploy_pages import PagesApi, PagesApiError, deploy_and_wait


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
    def test_effective_wait_can_exceed_action_600_second_cap(self):
        clock = FakeClock()
        api = FakeApi(["syncing_files", "syncing_files", "succeed"])
        page_url = deploy_and_wait(
            api, 1234, "a" * 40, wait_seconds=1800, poll_seconds=600.001,
            clock=clock.now, sleep=clock.sleep,
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
                clock=clock.now, sleep=clock.sleep,
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
            )
        self.assertEqual(api.cancelled, ["deployment-1"])

        terminal = FakeApi(["deployment_failed"])
        with self.assertRaisesRegex(PagesApiError, "deployment_failed"):
            deploy_and_wait(terminal, 1234, "d" * 40, clock=clock.now, sleep=clock.sleep)
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
