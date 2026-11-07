import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import snapshot


class SnapshotTests(unittest.TestCase):
    def test_pagination_keeps_every_page_and_filter(self):
        first = [{"number": number} for number in range(100)]
        final = [{"number": 100}]
        with patch.object(snapshot, "request", side_effect=[first, final]) as request:
            result = list(snapshot.pages("owner/repo", "alerts", "token", state="open", ref="refs/heads/main"))
        self.assertEqual(result, first + final)
        self.assertEqual([call.kwargs["page"] for call in request.call_args_list], [1, 2])
        for call in request.call_args_list:
            self.assertEqual(call.kwargs["ref"], "refs/heads/main")
            self.assertEqual(call.kwargs["state"], "open")

    def test_invalid_page_fails_instead_of_reporting_zero_alerts(self):
        with patch.object(snapshot, "request", return_value={"message": "access denied"}):
            with self.assertRaisesRegex(ValueError, "Expected a list"):
                list(snapshot.pages("owner/repo", "alerts", "token"))

    def test_api_failure_is_not_an_empty_alert_list(self):
        failure = HTTPError("https://api.github.com", 403, "Forbidden", {}, None)
        with patch.object(snapshot, "request", side_effect=failure):
            with self.assertRaises(HTTPError):
                list(snapshot.pages("owner/repo", "alerts", "token"))

    def test_unreadable_settings_preserve_alerts_and_remain_unknown(self):
        alert = {
            "number": 3, "html_url": "https://github.com/owner/repo/security/code-scanning/3",
            "state": "open", "rule": {"id": "js/xss-through-dom", "description": "DOM XSS",
                                      "security_severity_level": "high", "severity": "error"},
            "most_recent_instance": {
                "ref": "refs/heads/main", "commit_sha": "a" * 40,
                "category": "/language:javascript-typescript",
                "location": {"path": "main.js", "start_line": 14},
                "message": {"text": "DOM text becomes markup"},
            },
        }
        analyses = [
            {"id": 2, "category": "/language:javascript-typescript", "commit_sha": "a" * 40},
            {"id": 1, "category": "/language:javascript-typescript", "commit_sha": "b" * 40},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), \
                 patch("sys.argv", ["snapshot.py", "--repository", "owner/repo", "--output", str(output)]), \
                 patch.object(snapshot, "pages", side_effect=[[alert], analyses]), \
                 patch.object(snapshot, "request", side_effect=HTTPError("https://api.github.com", 403, "Forbidden", {}, None)), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                snapshot.main()
            result = json.loads(output.read_text())
        self.assertEqual(result["default_setup"], {"state": "unknown", "http_status": 403})
        self.assertEqual(result["open_alerts"][0]["number"], 3)
        self.assertEqual(len(result["latest_analyses"]), 1)
        self.assertEqual(result["latest_analyses"][0]["id"], 2)
        self.assertNotIn("test-token", stdout.getvalue())

    def test_rejects_repository_values_that_could_change_the_api_path(self):
        for repository in ("../secrets", "owner/repo?query", "owner/repo/extra", "https://example.com"):
            with self.subTest(repository=repository), \
                 patch("sys.argv", ["snapshot.py", "--repository", repository, "--output", "unused.json"]), \
                 patch.object(snapshot, "pages") as pages, \
                 patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    snapshot.main()
                pages.assert_not_called()


if __name__ == "__main__":
    unittest.main()
