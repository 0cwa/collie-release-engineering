import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import report
from test_report import fixture, problem


class ReviewedFindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '.github/workflows/build-artifacts.yml'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('on: workflow_dispatch\npermissions: {contents: read}\n')
        self.data = json.loads(json.dumps(fixture()).replace('py/', 'actions/'))
        run = self.data['runs'][0]
        run['automationDetails']['id'] = '/language:actions/'
        run['artifacts'][0]['location']['uri'] = '.github/workflows/build-artifacts.yml'
        metadata = run['tool']['driver']['rules'][0]
        metadata['id'] = 'actions/untrusted-checkout/medium'
        metadata['properties'].update({'precision': 'medium', 'security-severity': '5.0'})
        finding = problem()
        finding['ruleId'] = metadata['id']
        finding['message']['text'] = 'Potential unsafe checkout of untrusted pull request on non-privileged workflow.'
        run['results'] = [finding]
        self.sarif = self.root / 'analysis.sarif'
        self.sarif.write_text(json.dumps(self.data))
        parsed = report.audit([self.sarif], 'actions')
        result = parsed['runs'][0]['findings'][0]
        self.review = {
            'id': 'manual-dispatch-checkout', 'disposition': 'false-positive', 'language': 'actions',
            'category': '/language:actions/', 'tool_version': '2.27.4',
            'location': result['locations'][0],
            'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
            'reason': 'Only a trusted maintainer can dispatch the selected source.',
            **{key: result[key] for key in ('rule', 'level', 'security_severity', 'query_kind', 'query_precision', 'message')},
        }
        self.reviews = self.root / 'reviews.json'

    def apply(self, review=None):
        self.sarif.write_text(json.dumps(self.data))
        self.reviews.write_text(json.dumps({'schema': 1, 'reviews': [review or self.review]}))
        result = report.audit([self.sarif], 'actions')
        report.apply_reviews(result, self.reviews, self.root)
        return result

    def test_exact_review_retains_raw_finding_and_sarif(self):
        before = self.sarif.read_bytes()
        result = self.apply()
        self.assertTrue(result['passed'])
        self.assertEqual(result['finding_count'], 1)
        self.assertEqual(result['reviewed_finding_count'], 1)
        self.assertEqual(result['unreviewed_finding_count'], 0)
        self.assertEqual(self.sarif.read_bytes(), before)
        self.assertIn('1 raw findings', report.markdown(result))
        self.assertIn(self.review['reason'], report.markdown(result))

    def test_intentional_use_is_visible_and_source_bound(self):
        review = {**self.review, 'disposition': 'intentional-use'}
        before = self.sarif.read_bytes()
        result = self.apply(review)
        self.assertTrue(result['passed'])
        self.assertEqual(result['false_positive_finding_count'], 0)
        self.assertEqual(result['intentional_use_finding_count'], 1)
        self.assertEqual(result['runs'][0]['findings'][0]['review']['disposition'], 'intentional-use')
        self.assertEqual(self.sarif.read_bytes(), before)
        self.assertIn('1 reviewed intentional uses', report.markdown(result))
        self.assertIn('manual-dispatch-checkout (intentional-use)', report.markdown(result))
        self.source.write_text('changed')
        result = self.apply(review)
        self.assertFalse(result['passed'])
        self.assertEqual(result['intentional_use_finding_count'], 0)

    def test_supported_dispositions_share_exact_identity_guards(self):
        for disposition in ('false-positive', 'intentional-use'):
            for key, value in (('rule', 'other'), ('query_kind', 'other'),
                               ('query_precision', 'other'), ('message', 'other'),
                               ('tool_version', 'other'), ('category', 'other')):
                with self.subTest(disposition=disposition, key=key):
                    result = self.apply({**self.review, 'disposition': disposition, key: value})
                    self.assertFalse(result['passed'])
                    self.assertEqual(result['unreviewed_finding_count'], 1)
            review = copy.deepcopy(self.review)
            review['disposition'] = disposition
            review['location']['start_line'] += 1
            self.assertFalse(self.apply(review)['passed'])
            self.data['runs'][0]['results'] *= 2
            self.assertFalse(self.apply({**self.review, 'disposition': disposition})['passed'])
            self.data['runs'][0]['results'].pop()

    def test_duplicate_reviews_and_unknown_dispositions_fail(self):
        for disposition in ('false-positive', 'intentional-use'):
            reviews = [{**self.review, 'disposition': disposition},
                       {**self.review, 'id': 'second', 'disposition': disposition}]
            self.reviews.write_text(json.dumps({'schema': 1, 'reviews': reviews}))
            result = report.audit([self.sarif], 'actions')
            report.apply_reviews(result, self.reviews, self.root)
            self.assertFalse(result['passed'])
            self.assertTrue(any('duplicates another review' in e for e in result['errors']))
        for disposition in ('confirmed-vulnerability', 'unknown', ''):
            result = self.apply({**self.review, 'disposition': disposition})
            self.assertFalse(result['passed'])
            self.assertEqual(result['unreviewed_finding_count'], 1)

    def test_other_language_reviews_cannot_match_actions(self):
        self.reviews.write_text(json.dumps({'schema': 1, 'reviews': [
            self.review, {**self.review, 'id': 'java-review', 'language': 'java-kotlin',
                          'disposition': 'intentional-use'}]}))
        result = report.audit([self.sarif], 'actions')
        report.apply_reviews(result, self.reviews, self.root)
        self.assertTrue(result['passed'])
        self.assertEqual(result['false_positive_finding_count'], 1)
        self.assertEqual(result['intentional_use_finding_count'], 0)

    def test_source_change_requires_a_new_review(self):
        self.source.write_text('on: pull_request_target\npermissions: write-all\n')
        result = self.apply()
        self.assertFalse(result['passed'])
        self.assertEqual(result['reviewed_finding_count'], 0)
        self.assertTrue(any('source bytes changed' in error for error in result['errors']))

    def test_removed_source_review_is_retired_without_counting_as_a_finding(self):
        self.source.unlink()
        self.data['runs'][0]['results'] = []
        result = self.apply()
        self.assertTrue(result['passed'])
        self.assertEqual(result['finding_count'], 0)
        self.assertEqual(result['reviewed_finding_count'], 0)
        self.assertEqual(result['retired_reviews'], [{
            'id': self.review['id'],
            'path': self.review['location']['uri'],
            'source_sha256': self.review['source_sha256'],
            'reason': 'source path absent from analyzed revision',
        }])
        self.assertIn('Retired source-bound reviews', report.markdown(result))
        self.assertIn(self.review['source_sha256'], report.markdown(result))

    def test_removed_source_review_does_not_suppress_stale_sarif_finding(self):
        self.source.unlink()
        result = self.apply()
        self.assertFalse(result['passed'])
        self.assertEqual(result['finding_count'], 1)
        self.assertEqual(result['reviewed_finding_count'], 0)
        self.assertEqual(result['unreviewed_finding_count'], 1)
        self.assertEqual(len(result['retired_reviews']), 1)

    def test_removed_source_review_still_requires_sha256_identity(self):
        self.source.unlink()
        review = copy.deepcopy(self.review)
        review['source_sha256'] = 'bad'
        result = self.apply(review)
        self.assertFalse(result['passed'])
        self.assertTrue(any('needs a SHA-256 source fingerprint' in error for error in result['errors']))

    def test_rule_location_severity_message_and_tool_changes_are_not_accepted(self):
        for key, value in (('rule', 'actions/code-injection/critical'), ('level', 'error'),
                           ('security_severity', '9.0'), ('query_kind', 'path-problem'),
                           ('query_precision', 'high'), ('message', 'different flow'),
                           ('tool_version', '2.28.0'), ('category', '/language:other/')):
            with self.subTest(key=key):
                review = {**self.review, key: value}
                result = self.apply(review)
                self.assertFalse(result['passed'])
                self.assertEqual(result['reviewed_finding_count'], 0)
        review = copy.deepcopy(self.review)
        review['location']['start_line'] += 1
        self.assertFalse(self.apply(review)['passed'])

    def test_additional_finding_still_fails(self):
        extra = copy.deepcopy(self.data['runs'][0]['results'][0])
        extra['locations'][0]['physicalLocation']['region']['startLine'] += 1
        self.data['runs'][0]['results'].append(extra)
        result = self.apply()
        self.assertFalse(result['passed'])
        self.assertEqual(result['finding_count'], 2)
        self.assertEqual(result['reviewed_finding_count'], 1)
        self.assertEqual(result['unreviewed_finding_count'], 1)

    def test_duplicate_match_cannot_silence_multiple_findings(self):
        self.data['runs'][0]['results'] *= 2
        result = self.apply()
        self.assertFalse(result['passed'])
        self.assertEqual(result['reviewed_finding_count'], 0)
        self.assertTrue(any('multiple findings' in error for error in result['errors']))

    def test_review_cannot_read_outside_the_checkout(self):
        review = copy.deepcopy(self.review)
        review['location']['uri'] = '../outside'
        result = self.apply(review)
        self.assertFalse(result['passed'])
        self.assertTrue(any('unsafe source path' in error for error in result['errors']))

    def test_review_does_not_override_failed_extraction(self):
        self.data['runs'][0]['invocations'][0]['executionSuccessful'] = False
        result = self.apply()
        self.assertFalse(result['passed'])
        self.assertEqual(result['reviewed_finding_count'], 1)


if __name__ == '__main__':
    unittest.main()
