import hashlib
import json
from pathlib import Path
import unittest

SITE=Path(__file__).with_name('site')


class SiteAssetManifestTests(unittest.TestCase):
    def test_icon_origin_matches_the_path_at_its_historical_revision(self):
        manifest=json.loads((SITE/'ASSETS.json').read_text())
        icon=manifest['files']['assets/collie-icon.svg']
        self.assertEqual(icon['source_revision'],'62db1d3e13d53286a720c3cebc6e26561a982dd5')
        self.assertEqual(icon['origin'],'0cwa/collieim-android assets/collie-icon.svg')
        self.assertEqual(icon['sha256'],hashlib.sha256((SITE/'assets/collie-icon.svg').read_bytes()).hexdigest())
