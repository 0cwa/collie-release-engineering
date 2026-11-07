from pathlib import Path
import subprocess
import tempfile
import unittest

import fetch_reviewed_profiles as fetcher


class ReviewedProfilesFetchTests(unittest.TestCase):
    def test_fetch_populates_an_isolated_repo_and_rejects_wrong_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, dest = root / 'source', root / 'dest'
            source.mkdir()
            dest.mkdir()
            def git(repo, *args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE, text=True).strip()
            git(source, 'init', '-q')
            git(source, 'config', 'user.name', 'Test')
            git(source, 'config', 'user.email', 'test@example.invalid')
            git(source, 'config', 'uploadpack.allowFilter', 'true')
            (source / 'tools/patches').mkdir(parents=True)
            (source / 'tools/patches/release_profiles.py').write_text('reviewed\n')
            (source / 'molly-build.json').write_text('{}\n')
            (source / 'patches/ports/signal-8.33.2').mkdir(parents=True)
            (source / 'patches/ports/signal-8.33.2/provenance.json').write_text('{"layers": []}\n')
            git(source, 'add', '.')
            git(source, 'commit', '-qm', 'reviewed profiles')
            commit = git(source, 'rev-parse', 'HEAD')
            git(dest, 'init', '-q')
            self.assertFalse((dest / '.git/objects/info/alternates').exists())
            self.assertNotEqual(subprocess.run(['git', '-C', str(dest), 'cat-file', '-e', commit], stderr=subprocess.DEVNULL).returncode, 0)
            fetcher.fetch(dest, source.as_uri(), commit)
            self.assertEqual(git(dest, 'show', commit + ':tools/patches/release_profiles.py'), 'reviewed')
            self.assertEqual(git(dest, 'show', commit + ':patches/ports/signal-8.33.2/provenance.json'), '{"layers": []}')
            with self.assertRaises(subprocess.CalledProcessError):
                fetcher.fetch(dest, source.as_uri(), '0' * 40)
            git(source, 'rm', '-q', 'patches/ports/signal-8.33.2/provenance.json')
            git(source, 'commit', '-qm', 'missing provenance')
            with self.assertRaises(subprocess.CalledProcessError):
                fetcher.fetch(dest, source.as_uri(), git(source, 'rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
