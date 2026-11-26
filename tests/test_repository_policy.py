"""Advisory policy coverage must not turn incomplete evidence into protection."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import repository_policy as checker


APP = '0cwa/collieim-android'
ENGINEERING = '0cwa/collie-release-engineering'
ROLE = {'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'always'}
IMMUTABLE = {'type': 'update', 'parameters': {'update_allows_fetch_and_merge': False}}


def observed_tag(include, rules, bypass=None):
    return {
        'target': 'tag', 'enforcement': 'active',
        'conditions': {'ref_name': {'include': include, 'exclude': []}},
        'bypass_actors': [] if bypass is None else bypass,
        'rules': rules,
    }


class PolicyTest(unittest.TestCase):
    def test_required_checks_treat_missing_and_null_integration_as_unbound(self):
        desired = {'type': 'required_status_checks', 'parameters': {
            'required_status_checks': [{'context': 'Fast lint'}]}}
        observed = copy.deepcopy(desired)
        observed['parameters']['required_status_checks'][0]['integration_id'] = None
        self.assertTrue(checker.same_rule(observed, desired))
        self.assertTrue(checker.same_rule(desired, observed))
        observed['parameters']['required_status_checks'][0]['integration_id'] = 123
        self.assertFalse(checker.same_rule(observed, desired))
        self.assertFalse(checker.same_rule(desired, observed))
        self.assertTrue(checker.same_rule(observed, copy.deepcopy(observed)))

    def setUp(self):
        self.policy = checker.read_json(checker.POLICY)
        self.app = self.policy['repositories'][APP]
        self.creation = next(x['request'] for x in self.app['rulesets']
                             if x['request']['name'] == 'Authorize Collie release tag creation')
        self.immutable = next(x['request'] for x in self.app['rulesets']
                              if x['request']['name'] == 'Keep Collie release tags immutable')

    def status(self, rulesets, request=None, ref='refs/tags/v*', rule=IMMUTABLE):
        return checker.rule_status(rulesets, request or self.immutable, ref, rule, 'refs/heads/main')[0]

    def test_both_tag_namespaces_and_creation_bypass_are_explicit(self):
        for request in (self.creation, self.immutable):
            self.assertEqual(request['conditions']['ref_name'], {
                'include': ['refs/tags/v*', 'refs/tags/trailblazer-v*'], 'exclude': []})
        self.assertEqual(self.creation['rules'], [{'type': 'creation'}])
        self.assertEqual(self.creation['bypass_actors'], [ROLE])
        self.assertEqual(self.immutable['bypass_actors'], [])
        self.assertEqual({x['type'] for x in self.immutable['rules']}, {'update', 'deletion'})

    def test_current_combined_rule_does_not_establish_immutability_or_beta_coverage(self):
        current = observed_tag(['refs/tags/v*'],
                               [{'type': 'creation'}, IMMUTABLE, {'type': 'deletion'}], [ROLE])
        self.assertEqual(self.status([current], self.creation, rule={'type': 'creation'}), 'OK')
        self.assertEqual(self.status([current]), 'DRIFT')
        self.assertEqual(self.status([current], ref='refs/tags/trailblazer-v*'), 'DRIFT')
        self.assertEqual(self.status([current], self.creation, 'refs/tags/trailblazer-v*', {'type': 'creation'}), 'DRIFT')

    def test_separate_immutable_rule_cannot_be_bypassed_by_legacy_rule(self):
        legacy = observed_tag(['refs/tags/v*'], [IMMUTABLE], [ROLE])
        for ref in self.immutable['conditions']['ref_name']['include']:
            self.assertEqual(self.status([legacy, self.immutable], ref=ref), 'OK')
        weakened = copy.deepcopy(self.immutable)
        weakened['bypass_actors'] = [ROLE]
        self.assertEqual(self.status([weakened]), 'DRIFT')

    def test_another_creation_rule_can_block_the_preserved_maintainer(self):
        blocker = observed_tag(['refs/tags/v*'], [{'type': 'creation'}])
        self.assertEqual(self.status([self.creation, blocker], self.creation, rule={'type': 'creation'}), 'DRIFT')

    def test_hidden_bypass_and_partial_pattern_coverage_are_unknown(self):
        hidden = copy.deepcopy(self.immutable)
        del hidden['bypass_actors']
        self.assertEqual(self.status([hidden]), 'UNKNOWN')
        partial = copy.deepcopy(self.immutable)
        partial['conditions']['ref_name']['exclude'] = ['refs/tags/v8.35.2']
        self.assertEqual(self.status([partial]), 'UNKNOWN')
        wildcard = copy.deepcopy(self.immutable)
        wildcard['conditions']['ref_name']['include'] = ['refs/tags/*']
        self.assertEqual(self.status([wildcard]), 'UNKNOWN')

    def test_unknown_enforcement_target_and_malformed_rules_prevent_creation_ok(self):
        changes = [
            ('enforcement', 'future-active-mode'),
            ('target', 'future-target'),
            ('rules', [{}]),
            ('rules', [None]),
            ('rules', [{'type': 1}]),
        ]
        for key, value in changes:
            other = observed_tag(['refs/tags/v*'], [{'type': 'creation'}])
            other[key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.status([self.creation, other], self.creation,
                                             rule={'type': 'creation'}), 'UNKNOWN')
        for enforcement in ('disabled', 'evaluate'):
            inactive = observed_tag(['refs/tags/v*'], [{'type': 'creation'}])
            inactive['enforcement'] = enforcement
            self.assertEqual(self.status([self.creation, inactive], self.creation,
                                         rule={'type': 'creation'}), 'OK')

    def test_disabled_rule_and_excluded_namespace_do_not_count(self):
        disabled = copy.deepcopy(self.immutable)
        disabled['enforcement'] = 'disabled'
        self.assertEqual(self.status([disabled]), 'DRIFT')
        excluded = copy.deepcopy(self.immutable)
        excluded['conditions']['ref_name']['exclude'] = ['refs/tags/v*']
        self.assertEqual(self.status([excluded]), 'DRIFT')

    def test_malformed_parameter_type_cannot_match_a_reviewed_boolean(self):
        malformed = copy.deepcopy(self.immutable)
        malformed['rules'][0]['parameters']['update_allows_fetch_and_merge'] = 0
        self.assertEqual(self.status([malformed]), 'DRIFT')
        malformed['rules'][0]['parameters'] = None
        self.assertEqual(self.status([malformed]), 'DRIFT')

    def test_current_main_can_be_protected_by_separate_rulesets(self):
        desired = self.app['rulesets'][0]['request']
        signatures = copy.deepcopy(desired)
        signatures['rules'] = [{'type': 'required_signatures'}]
        pr_rules = copy.deepcopy(desired)
        pr_rules['rules'] = [copy.deepcopy(x) for x in desired['rules'] if x['type'] != 'required_signatures']
        for rule in desired['rules']:
            self.assertEqual(self.status([pr_rules, signatures], desired, 'refs/heads/main', rule), 'OK')
        pr_rules['rules'][-1]['parameters']['required_status_checks'] = [{'context': 'Fast lint'}]
        self.assertEqual(self.status([pr_rules, signatures], desired, 'refs/heads/main', desired['rules'][-1]), 'DRIFT')

    def test_main_requires_code_owner_review_in_both_repositories(self):
        for repository, name in (
            (APP, 'Protect app main'),
            (ENGINEERING, 'Protect release engineering main'),
        ):
            with self.subTest(repository=repository):
                ruleset = next(x for x in self.policy['repositories'][repository]['rulesets']
                               if x['request']['name'] == name)
                self.assertEqual(ruleset['phase'], 'current')
                parameters = next(x['parameters'] for x in ruleset['request']['rules']
                                  if x['type'] == 'pull_request')
                self.assertIs(parameters['require_code_owner_review'], True)
                if repository == APP:
                    self.assertEqual(parameters['allowed_merge_methods'], ['merge', 'squash'])

    def test_non_main_signature_bypass_does_not_count_as_protected_beta_source(self):
        desired = self.app['rulesets'][1]['request']
        other = {
            'target': 'branch', 'enforcement': 'active',
            'bypass_actors': [{'actor_id': 1490936, 'actor_type': 'User', 'bypass_mode': 'always'}],
            'conditions': {'ref_name': {'include': ['~ALL'], 'exclude': ['refs/heads/main']}},
            'rules': [{'type': 'required_signatures'}],
        }
        self.assertEqual(self.status([other], desired, 'refs/heads/trailblazer-beta', {'type': 'required_signatures'}), 'DRIFT')

    def test_incomplete_snapshot_is_unknown_not_no_protection(self):
        for state in (None, {'errors': ['forbidden']}, {'branches': ['main'], 'rulesets': []}):
            snapshot = {'schema': 1, 'repositories': {APP: state}}
            app_report = [x for x in checker.report(self.policy, snapshot) if x['repository'] == APP]
            self.assertEqual(len(app_report), 1)
            self.assertEqual(app_report[0]['status'], 'UNKNOWN')

    def test_existing_beta_branch_does_not_authorize_a_source_route(self):
        snapshot = {'schema': 1, 'repositories': {APP: {
            'default_branch': 'main', 'branches': ['main', 'trailblazer-beta'],
            'rulesets': [x['request'] for x in self.app['rulesets']], 'errors': [],
        }}}
        findings = checker.report(self.policy, snapshot)
        item = next(x for x in findings if x['item'] == 'refs/heads/trailblazer-beta')
        self.assertEqual(item['status'], 'GATED')
        self.assertIn('do not establish release authorization', item['detail'])

    def test_stable_maintenance_ref_is_retired(self):
        retired_ref = 'refs/heads/release/stable/8.35'
        self.assertNotIn(retired_ref, [x['ref'] for x in self.app['source_refs']])
        for ruleset in self.app['rulesets']:
            self.assertNotEqual(ruleset['phase'], 'proposal-stable-maintenance')
            self.assertNotIn(retired_ref, ruleset['request']['conditions']['ref_name']['include'])

    def test_invalid_fixture_returns_two_without_a_network_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text('{}')
            with (patch.object(checker, 'collect', side_effect=AssertionError('offline requested')),
                  contextlib.redirect_stderr(io.StringIO())):
                self.assertEqual(checker.main(['--snapshot', str(path)]), 2)

    def test_offline_drift_is_advisory_and_performs_no_get(self):
        snapshot = {'schema': 1, 'repositories': {APP: {
            'default_branch': 'main', 'branches': ['main'], 'rulesets': [], 'errors': [],
        }}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps(snapshot))
            output = io.StringIO()
            with (patch.object(checker, 'collect', side_effect=AssertionError('offline requested')),
                  contextlib.redirect_stdout(output)):
                code = checker.main(['--snapshot', str(path), '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(any(x['status'] == 'DRIFT' for x in json.loads(output.getvalue())['findings']))

    def test_remote_transport_only_uses_get_with_fixed_host(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as run:
            self.assertEqual(checker.github_get('repos/0cwa/collieim-android'), {})
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                  '-H', 'Accept: application/vnd.github+json', 'repos/0cwa/collieim-android'])
        self.assertNotIn('shell', kwargs)

    def test_collection_reads_all_pages_and_every_ruleset_detail(self):
        paths = []
        def get(path):
            paths.append(path)
            if path == f'repos/{APP}':
                return {'default_branch': 'main'}
            if '/branches?' in path:
                return [{'name': f'work/{n}'} for n in range(100)] if path.endswith('page=1') else [{'name': 'main'}]
            if '/rulesets?' in path:
                return [{'id': 17}]
            if '/rulesets/17?' in path:
                return self.creation
            raise AssertionError(path)
        snapshot = checker.collect({'repositories': {APP: self.app}}, get)
        state = snapshot['repositories'][APP]
        self.assertEqual(len(state['branches']), 101)
        self.assertEqual(state['rulesets'], [self.creation])
        self.assertEqual(state['errors'], [])
        self.assertEqual(len(paths), 5)

    def test_collection_failure_cannot_report_empty_rules_as_authoritative(self):
        def get(path):
            if path == f'repos/{APP}':
                return {'default_branch': 'main'}
            raise subprocess.CalledProcessError(1, ['gh', 'api', '--method', 'GET', path])
        snapshot = checker.collect({'repositories': {APP: self.app}}, get)
        self.assertTrue(snapshot['repositories'][APP]['errors'])
        self.assertEqual(checker.report({'repositories': {APP: self.app}}, snapshot)[0]['status'], 'UNKNOWN')

    def test_repeated_branch_or_ruleset_across_pages_marks_collection_unknown(self):
        for repeated in ('branch', 'ruleset'):
            def get(path):
                if path == f'repos/{APP}':
                    return {'default_branch': 'main'}
                if '/branches?' in path:
                    if repeated == 'branch':
                        return ([{'name': f'work/{n}'} for n in range(100)]
                                if path.endswith('page=1') else [{'name': 'work/99'}])
                    return [{'name': 'main'}]
                if '/rulesets?' in path:
                    return ([{'id': n} for n in range(1, 101)]
                            if path.endswith('page=1') else [{'id': 100}])
                raise AssertionError('Should reject duplicate IDs before reading details')
            snapshot = checker.collect({'repositories': {APP: self.app}}, get)
            with self.subTest(repeated=repeated):
                self.assertTrue(snapshot['repositories'][APP]['errors'])
                self.assertEqual(checker.report({'repositories': {APP: self.app}}, snapshot)[0]['status'], 'UNKNOWN')

    def test_duplicate_identifiers_in_offline_snapshot_are_unknown(self):
        state = {'default_branch': 'main', 'branches': ['main'], 'rulesets': [], 'errors': []}
        for key, value in (('branches', ['main', 'main']), ('rulesets', [{'id': 1}, {'id': 1}])):
            snapshot = {'schema': 1, 'repositories': {APP: dict(state, **{key: value})}}
            with self.subTest(key=key):
                self.assertEqual(checker.report({'repositories': {APP: self.app}}, snapshot)[0]['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
