"""Classify fork test sources by the gate that runs them."""
import re


UNIT = re.compile(r'^(?P<module>.+?)/src/test/(?:java|kotlin)/(?P<name>.+)\.(?:kt|java)$')
DEVICE = re.compile(r'^app/src/androidTest/(?:java|kotlin)/.+\.(?:kt|java)$')
OTHER = re.compile(r'(^|/)src/(androidTest|testFixtures|screenshotTest\w*)/')


def classify(path):
    """Return a test kind and identity, or None for a non-test path."""
    match = UNIT.match(path)
    if match:
        return 'unit', (match['module'], match['name'].replace('/', '.'))
    if DEVICE.match(path):
        return 'instrumentation', path
    if OTHER.search(path):
        return 'unsupported', path
    return None
