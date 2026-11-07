import copy
import hashlib
import json
from pathlib import Path
import unittest
import release_profiles
import fork_profiles
import forest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'molly-build.json').read_text())

class ProfilesTest(unittest.TestCase):
    def test_old_names_select_full_collie_with_notification_modes_and_filenames_preserved(self):
        expected = {
            'molly-minimal': (False, 'Molly-Minimal', 'Collie'),
            'molly-continued': (False, 'Molly-Continued', 'Collie'),
            'molly-foss-continued': (True, 'Molly-FOSS-Continued', 'Collie FOSS'),
            'collie': (False, 'Collie', 'Collie'),
            'collie-foss': (True, 'Collie-FOSS', 'Collie FOSS'),
        }
        trees = set()
        for name, (free, filename, title) in expected.items():
            selected, profile = release_profiles.select(CONFIG, name, ROOT)
            for test in ('org.thoughtcrime.securesms.backup.v2.util.ChatStyleColorTest',
                         'org.thoughtcrime.securesms.recipients.Recipient_getChatColorsTest',
                         'org.thoughtcrime.securesms.conversation.colors.CollieChatColorsTest',
                         'org.thoughtcrime.securesms.registration.v2.LocalBackupFolderScanTest'):
                self.assertIn(test, selected['android']['tests'])
            self.assertEqual((profile['firebase_free'], profile['app']['file_name'], profile['app']['title']), (free, filename, title))
            self.assertEqual(profile['app']['package_id'], 'im.collie.app')
            self.assertEqual(profile['base_layer'], 'followups/view-once-open-guard')
            self.assertEqual(len(profile['instrumentation_sources']), 6)
            trees.add(profile['target_tree'])
        self.assertEqual(len(trees), 1)

    def test_current_and_historical_port_boundaries(self):
        series = ROOT / release_profiles.PORT_SERIES
        provenance = json.loads((series.parent / 'provenance.json').read_text())
        records = [(series.parent / name, {'sha256': hashlib.sha256((series.parent / name).read_bytes()).hexdigest()}) for name in forest.lines(series)]
        with patch.object(fork_profiles.verify_source_bundles, 'verify'):
            for name in release_profiles.NAMES:
                _, profile = release_profiles.select(CONFIG, name, ROOT)
                self.assertEqual(len(fork_profiles.port_prefix(ROOT, ROOT / 'molly-build.json', profile, records)), 63)
            for boundary, count in [('followups/native-catalog', 59), ('followups/unifiedpush-registration-retry', 62)]:
                layer = next(layer for layer in provenance['layers'] if layer['id'] == boundary)
                selection = {'base_layer': boundary, 'base_checkpoint': layer['source_commit'], 'base_tree': layer['after_tree']}
                self.assertEqual(len(fork_profiles.port_prefix(ROOT, ROOT / 'molly-build.json', selection, records)), count)

    def test_foss_cannot_be_silently_changed_to_fcm(self):
        config = copy.deepcopy(CONFIG)
        config['profiles']['molly-foss-continued']['firebase_free'] = False
        with self.assertRaises(release_profiles.ProfileError):
            release_profiles.select(config, 'molly-foss-continued', ROOT)

    def test_old_shortened_or_retention_layer_compositions_rejected(self):
        for name in release_profiles.NAMES:
            for layers in ([], [release_profiles.FIREBASE, release_profiles.RETENTION]):
                config = copy.deepcopy(CONFIG)
                config['profiles'][name]['fork_series'] = layers
                with self.subTest(name=name, layers=layers), self.assertRaises(release_profiles.ProfileError):
                    release_profiles.select(config, name, ROOT)

if __name__ == '__main__':
    unittest.main()
