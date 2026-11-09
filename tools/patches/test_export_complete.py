import unittest

import export_complete


class ExportCompleteGroupingTests(unittest.TestCase):
    def test_relocated_module_paths_keep_their_existing_review_groups(self):
        self.assertEqual(export_complete.group_for_path('tooling/baseline-profile/src/main/Profile.kt'),
                         'module-pruning')
        self.assertEqual(export_complete.group_for_path('core/gms/tasks/src/main/Task.java'),
                         'google-services')
        self.assertEqual(export_complete.group_for_path('tooling/reproducible-builds/README.md'),
                         'documentation')
        self.assertEqual(export_complete.group_for_path('tooling/mk/android.mk'),
                         'build-and-release')


if __name__ == '__main__':
    unittest.main()
