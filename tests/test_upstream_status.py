import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("upstream_status", ROOT / "tools/upstreams/status.py")
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
PINNED = "8f4ad8577344e844945ee8e73d95fd1fcc7208bc"


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                            env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class UpstreamStatusTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.signal = self.upstream("signal")
        self.molly = self.upstream("molly")
        self.johan = self.upstream("johan")
        self.app = self.dir / "app"
        self.app.mkdir()
        git(self.app, "init", "-q", "-b", "main")
        self.commit(self.app, "collie.txt", "app")
        self.mark_merge_tool(supported=True)

    def upstream(self, name):
        repo = self.dir / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "uploadpack.allowFilter", "true")
        return repo

    def commit(self, repo, name, text):
        (repo / name).write_text(text, encoding="utf-8")
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", text)
        return git(repo, "rev-parse", "HEAD")

    def mark_merge_tool(self, supported):
        tool = self.app / status.MERGE_TOOL
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("--expected-commit\n" if supported else "nothing\n", encoding="utf-8")
        git(self.app, "add", "-A")
        git(self.app, "commit", "-q", "-m", "merge tool")

    def merge_into_app(self, upstream, trailer, commit, layer="upgrade"):
        git(self.app, "fetch", "-q", "--no-tags", str(upstream), "+refs/*:refs/remotes/" + upstream.name + "/*")
        git(self.app, "merge", "-q", "--no-ff", "--allow-unrelated-histories", "-X", "theirs", "-m",
            f"Merge {trailer}\n\nLayer: {layer}\nUpstream: {trailer}", commit)

    def registry(self):
        """A registry root whose Johan pins point at the fixture's commits."""
        root = self.dir / "registry"
        (root / "upstreams/johanw666").mkdir(parents=True, exist_ok=True)
        for name in ("sources.json", "johanw666/review.json"):
            text = (ROOT / "upstreams" / name).read_text(encoding="utf-8").replace(PINNED, self.johan_base)
            (root / "upstreams" / name).write_text(text, encoding="utf-8")
        return root

    def setup_johan(self, *tags):
        if hasattr(self, "johan_base"):
            return
        self.johan_base = self.commit(self.johan, "j.txt", "base")
        git(self.johan, "tag", "v8.36.0.0-JW")
        for tag in tags:
            self.commit(self.johan, "j.txt", tag)
            git(self.johan, "tag", "-a", "-m", tag, tag)

    def report(self, **options):
        self.setup_johan(*options.pop("johan_tags", ()))
        urls = {"signal": str(self.signal), "molly": str(self.molly), "johanw666": str(self.johan)}
        report = status.collect(self.registry(), self.app, "main", self.dir / "cache", urls=urls, **options)
        return {source["id"]: source for source in report["sources"]}, report

    def test_new_signal_commits_are_counted_and_the_command_pins_the_peeled_commit(self):
        old = self.commit(self.signal, "s.txt", "one")
        git(self.signal, "tag", "-a", "-m", "v8.36.3", "v8.36.3")
        self.merge_into_app(self.signal, "signalapp/Signal-Android v8.36.3", old)
        self.commit(self.signal, "s.txt", "two")
        git(self.signal, "tag", "v8.37.0")
        new = self.commit(self.signal, "s.txt", "three")
        git(self.signal, "tag", "-a", "-m", "v8.37.2", "v8.37.2")
        git(self.signal, "tag", "v8.9.0")  # sorts after v8.37 as text, before it as a version
        sources, report = self.report()
        signal = sources["signal"]
        self.assertNotIn("error", signal)
        self.assertEqual(signal["last_merged"]["commit"], old)
        self.assertEqual(signal["candidate"]["name"], "v8.37.2")
        self.assertEqual(signal["candidate"]["commit"], new)
        self.assertEqual(signal["waiting"], 2)
        self.assertEqual(signal["newer_tags_included"], ["v8.37.0"])
        self.assertTrue(signal["last_merged_is_ancestor"])
        self.assertEqual(signal["command"],
                         f"python3 tooling/upstream/merge-upstream.py signal v8.37.2 --expected-commit {new}")
        self.assertTrue(report["app"]["expected_commit_option"])
        self.assertIn(f"--expected-commit {new}", status.render(report))

    def test_molly_with_nothing_new_and_a_newer_release_branch(self):
        first = self.commit(self.molly, "m.txt", "one")
        git(self.molly, "branch", "molly-8.34")
        self.merge_into_app(self.molly, f"mollyim/mollyim-android molly-8.34@{first}", first)
        self.commit(self.molly, "m.txt", "two")
        git(self.molly, "branch", "molly-8.36")
        git(self.molly, "branch", "molly-8.9")
        git(self.molly, "branch", "feature-x")
        git(self.molly, "reset", "-q", "--hard", first)  # main is not a release branch
        molly = self.report()[0]["molly"]
        self.assertEqual(molly["candidate"]["name"], "molly-8.34")
        self.assertEqual(molly["waiting"], 0)
        self.assertEqual(molly["newer_release_branches"], ["molly-8.36"])
        self.assertIn("nothing", status.render(self.report()[1]))

    def test_molly_branch_is_chosen_explicitly_and_new_commits_give_a_range(self):
        first = self.commit(self.molly, "m.txt", "one")
        git(self.molly, "branch", "molly-8.34")
        self.merge_into_app(self.molly, f"mollyim/mollyim-android molly-8.34@{first}", first)
        git(self.molly, "checkout", "-q", "-b", "molly-8.36")
        tip = self.commit(self.molly, "m.txt", "two")
        sources, report = self.report(molly_branch="molly-8.36")
        molly = sources["molly"]
        self.assertEqual(molly["candidate"]["commit"], tip)
        self.assertEqual(molly["waiting"], 1)
        self.assertEqual(molly["branch_selection"], "--molly-branch")
        self.assertIn(f"{first[:12]}..{tip[:12]}", status.render(report))

    def test_rewritten_upstream_branch_is_reported(self):
        first = self.commit(self.molly, "m.txt", "one")
        git(self.molly, "branch", "molly-8.34")
        self.merge_into_app(self.molly, f"mollyim/mollyim-android molly-8.34@{first}", first)
        git(self.molly, "checkout", "-q", "--orphan", "rewritten")
        rewritten = self.commit(self.molly, "other.txt", "rewritten history")
        git(self.molly, "branch", "-M", "molly-8.34")
        molly = self.report()[0]["molly"]
        self.assertEqual(molly["candidate"]["commit"], rewritten)
        self.assertFalse(molly["last_merged_is_ancestor"])
        self.assertIn("rewritten", status.render(self.report()[1]))

    def test_trailer_commit_must_match_the_second_parent_and_only_upgrade_merges_count(self):
        first = self.commit(self.molly, "m.txt", "one")
        git(self.molly, "branch", "molly-8.34")
        self.merge_into_app(self.molly, f"mollyim/mollyim-android molly-8.34@{'0' * 40}", first)
        self.assertIn("second parent", self.report()[0]["molly"]["error"])
        git(self.app, "reset", "-q", "--hard", "HEAD^")
        self.merge_into_app(self.molly, f"mollyim/mollyim-android molly-8.34@{first}", first, layer="feature")
        self.assertIn("no Layer: upgrade merge", self.report()[0]["molly"]["error"])

    def test_johan_lists_new_tags_numerically_and_flags_a_moved_pin(self):
        sources, report = self.report(johan_tags=("v8.36.2.0-JW", "v8.9.0.0-JW", "v8.37.0.0-JW"))
        johan = sources["johanw666"]
        self.assertEqual([tag["ref"] for tag in johan["new_tags"]],
                         ["refs/tags/v8.36.2.0-JW", "refs/tags/v8.37.0.0-JW"])
        self.assertEqual(johan["action"], "manual review")
        self.assertFalse(johan["reviewed"]["pinned_tag_moved"])
        self.assertIn("manual review", status.render(report))
        self.commit(self.johan, "j.txt", "moved")
        git(self.johan, "tag", "-f", "v8.36.0.0-JW")
        self.assertTrue(status.collect(self.registry(), self.app, "main", self.dir / "cache",
                                       urls={"signal": str(self.signal), "molly": str(self.molly),
                                             "johanw666": str(self.johan)})["sources"][2]["reviewed"]["pinned_tag_moved"])

    def test_missing_expected_commit_option_is_noted_and_nothing_is_written_to_the_app(self):
        first = self.commit(self.signal, "s.txt", "one")
        git(self.signal, "tag", "v8.36.3")
        self.commit(self.signal, "s.txt", "two")
        git(self.signal, "tag", "v8.36.4")
        self.merge_into_app(self.signal, "signalapp/Signal-Android v8.36.3", first)
        self.mark_merge_tool(supported=False)
        before = (git(self.app, "rev-parse", "HEAD"), git(self.app, "for-each-ref"), git(self.app, "status", "--short"),
                  sorted(path.name for path in (self.app / ".git").iterdir()))
        sources, report = self.report()
        self.assertFalse(report["app"]["expected_commit_option"])
        self.assertIn("app PR #227", status.render(report))
        self.assertEqual(sources["signal"]["waiting"], 1)
        self.assertEqual(before, (git(self.app, "rev-parse", "HEAD"), git(self.app, "for-each-ref"),
                                  git(self.app, "status", "--short"),
                                  sorted(path.name for path in (self.app / ".git").iterdir())))

    def test_json_is_the_same_data_as_the_text(self):
        first = self.commit(self.signal, "s.txt", "one")
        git(self.signal, "tag", "v8.36.3")
        self.merge_into_app(self.signal, "signalapp/Signal-Android v8.36.3", first)
        self.report()
        result = subprocess.run(
            ["python3", str(ROOT / "tools/upstreams/status.py"), "--root", str(self.registry()), "--app", str(self.app),
             "--revision", "main", "--cache", str(self.dir / "cache"), "--json",
             "--url", f"signal={self.signal}", "--url", f"molly={self.molly}", "--url", f"johanw666={self.johan}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
        self.assertEqual(result.returncode, 1, result.stderr)  # Molly has no merge in this fixture
        data = json.loads(result.stdout)
        self.assertEqual([source["id"] for source in data["sources"]], ["signal", "molly", "johanw666"])
        self.assertEqual(data["sources"][0]["waiting"], 0)
        self.assertIn("error", data["sources"][1])


if __name__ == "__main__":
    unittest.main()
