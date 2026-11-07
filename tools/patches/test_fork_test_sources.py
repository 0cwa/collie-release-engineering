import unittest

from fork_test_sources import classify


class ForkTestSourceTest(unittest.TestCase):
    def test_classifies_sources_without_turning_device_tests_into_unit_filters(self):
        self.assertEqual(classify('feature/foo/src/test/kotlin/org/x/FooTest.kt'),
                         ('unit', ('feature/foo', 'org.x.FooTest')))
        path = 'app/src/androidTest/java/org/x/DeviceTest.kt'
        self.assertEqual(classify(path), ('instrumentation', path))
        self.assertIsNone(classify('app/src/main/java/org/x/Foo.kt'))

    def test_rejects_test_sources_without_a_supported_gate(self):
        for path in ('feature/foo/src/androidTest/java/org/x/Foo.kt',
                     'app/src/androidTest/assets/data.json',
                     'app/src/testFixtures/java/org/x/Foo.kt',
                     'app/src/screenshotTest/java/org/x/Foo.kt'):
            with self.subTest(path=path):
                self.assertEqual(classify(path), ('unsupported', path))


if __name__ == '__main__':
    unittest.main()
