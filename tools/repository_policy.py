#!/usr/bin/env python3
"""Report repository ruleset drift using GET requests or an offline snapshot.

This is an operator report, not a release verifier or a CI gate. It never applies
the proposed rulesets, creates refs, or changes the accepted release sources.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


POLICY = Path(__file__).resolve().parents[1] / '.github/repository-policy.json'
REPOSITORIES = {
    '0cwa/collieim-android',
    '0cwa/collie-release-engineering',
    '0cwa/colliesocket',
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError(f'{path}: expected a schema 1 object')
    if not isinstance(value.get('repositories'), dict):
        raise ValueError(f'{path}: repositories must be an object')
    return value


def github_get(endpoint: str) -> Any:
    """The only remote operation in this tool. No shell or write method."""
    result = subprocess.run(
        ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
         '-H', 'Accept: application/vnd.github+json', endpoint],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def pages(endpoint: str, get=github_get) -> list[dict[str, Any]]:
    items = []
    separator = '&' if '?' in endpoint else '?'
    for page in range(1, 101):
        values = get(f'{endpoint}{separator}per_page=100&page={page}')
        if not isinstance(values, list) or any(not isinstance(x, dict) for x in values):
            raise ValueError(f'GET {endpoint}: expected an object array')
        items.extend(values)
        if len(values) < 100:
            return items
    raise ValueError(f'GET {endpoint}: pagination limit reached; snapshot incomplete')


def collect(policy: dict[str, Any], get=github_get) -> dict[str, Any]:
    snapshot: dict[str, Any] = {'schema': 1, 'repositories': {}}
    for repository in policy['repositories']:
        if repository not in REPOSITORIES:
            raise ValueError(f'unreviewed repository: {repository}')
        state: dict[str, Any] = {'errors': []}
        snapshot['repositories'][repository] = state
        try:
            base = f'repos/{repository}'
            state['default_branch'] = get(base)['default_branch']
            state['branches'] = [x['name'] for x in pages(f'{base}/branches', get)]
            if (any(not isinstance(x, str) for x in state['branches']) or
                    len(state['branches']) != len(set(state['branches']))):
                raise ValueError('invalid or duplicate branch names; snapshot incomplete')
            summaries = pages(f'{base}/rulesets?includes_parents=true', get)
            ids = [x['id'] for x in summaries]
            if any(type(x) is not int or x <= 0 for x in ids) or len(ids) != len(set(ids)):
                raise ValueError('invalid or duplicate ruleset IDs; snapshot incomplete')
            state['rulesets'] = []
            for ruleset_id in ids:
                state['rulesets'].append(get(
                    f'{base}/rulesets/{ruleset_id}?includes_parents=true'))
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            # Missing permissions and incomplete reads are unknown, never an empty
            # ruleset list that could be mistaken for an unprotected repository.
            state['errors'].append(f'Could not complete GET snapshot: {exc}')
    return snapshot


def relation(pattern: str, wanted: str, default_ref: str) -> bool | None:
    """Conservative coverage, not a reimplementation of GitHub's fnmatch.

    True covers the complete requested ref/namespace; False is disjoint. Other
    wildcard relationships remain unknown instead of overstating protection.
    """
    if pattern == '~DEFAULT_BRANCH':
        pattern = default_ref
    if pattern == '~ALL' or pattern == wanted:
        return True
    wild = '*?['
    pattern_prefix = pattern[:min((pattern.find(x) for x in wild if x in pattern),
                                 default=len(pattern))]
    wanted_prefix = wanted[:min((wanted.find(x) for x in wild if x in wanted),
                               default=len(wanted))]
    if pattern_prefix == pattern and wanted_prefix == wanted:
        return False
    if not pattern_prefix.startswith(wanted_prefix) and not wanted_prefix.startswith(pattern_prefix):
        return False
    return None


def covers(ruleset: dict[str, Any], wanted: str, default_ref: str) -> bool | None:
    conditions = ruleset.get('conditions')
    if not isinstance(conditions, dict) or set(conditions) != {'ref_name'}:
        return None
    names = conditions['ref_name']
    if not isinstance(names, dict) or set(names) != {'include', 'exclude'}:
        return None
    if any(not isinstance(names[x], list) or
           any(not isinstance(y, str) for y in names[x]) for x in ('include', 'exclude')):
        return None
    included = [relation(x, wanted, default_ref) for x in names['include']]
    excluded = [relation(x, wanted, default_ref) for x in names['exclude']]
    if True in excluded or all(x is False for x in included):
        return False
    if True in included and all(x is False for x in excluded):
        return True
    return None


def actors(value: Any) -> list[str] | None:
    # GitHub can omit bypass actors for readers without sufficient visibility.
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        return None
    return sorted(json.dumps(x, sort_keys=True) for x in value)


def same_rule(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    if actual.get('type') != desired['type']:
        return False
    if desired.get('parameters') and not isinstance(actual.get('parameters'), dict):
        return False
    for key, value in desired.get('parameters', {}).items():
        observed = actual.get('parameters', {}).get(key)
        if key in ('required_status_checks', 'allowed_merge_methods'):
            if not isinstance(observed, list):
                return False
            def encoded(item):
                if key == 'required_status_checks' and isinstance(item, dict):
                    item = {k: v for k, v in item.items()
                            if k != 'integration_id' or v is not None}
                return json.dumps(item, sort_keys=True)
            if sorted(encoded(x) for x in value) != sorted(encoded(x) for x in observed):
                return False
        elif type(observed) is not type(value) or observed != value:
            return False
    return True


def rule_status(observed: list[dict[str, Any]], request: dict[str, Any],
                wanted: str, rule: dict[str, Any], default_ref: str) -> tuple[str, str]:
    matched = False
    unknown = False
    conflicting_creation = False
    for actual in observed:
        if (actual.get('enforcement') not in ('active', 'disabled', 'evaluate') or
                actual.get('target') not in ('branch', 'tag', 'push')):
            unknown = True
            continue
        if actual.get('enforcement') != 'active' or actual.get('target') != request['target']:
            continue
        scope = covers(actual, wanted, default_ref)
        if scope is False:
            continue
        rules = actual.get('rules')
        if not isinstance(rules, list):
            unknown = True
            continue
        if any(not isinstance(x, dict) or not isinstance(x.get('type'), str)
               or not x['type'] for x in rules):
            unknown = True
        candidates = [x for x in rules if isinstance(x, dict) and x.get('type') == rule['type']]
        if not candidates:
            continue
        bypass = actors(actual.get('bypass_actors'))
        if scope is None or bypass is None:
            unknown = True
            continue
        same_bypass = bypass == actors(request['bypass_actors'])
        if rule['type'] == 'creation' and not same_bypass:
            # Another creation restriction still applies even when the desired
            # creation rule exists. Do not say the maintainer can create tags.
            conflicting_creation = True
        if same_bypass and any(same_rule(x, rule) for x in candidates):
            matched = True
    if conflicting_creation:
        return 'DRIFT', 'A matching creation rule has different bypass actors; review their combined effect.'
    if matched and (rule['type'] != 'creation' or not unknown):
        return 'OK', 'Covered by active rulesets with the reviewed parameters and bypass actors.'
    if unknown:
        return 'UNKNOWN', 'Rule details, bypass visibility, or pattern coverage need manual review.'
    return 'DRIFT', 'No active ruleset coverage with the reviewed parameters and bypass actors.'


def report(policy: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings = []
    for repository, desired in policy['repositories'].items():
        if repository not in REPOSITORIES:
            raise ValueError(f'unreviewed repository: {repository}')
        state = snapshot['repositories'].get(repository)

        def add(status: str, item: str, phase: str, detail: str) -> None:
            findings.append(dict(repository=repository, status=status, item=item,
                                 phase=phase, detail=detail))

        if (not isinstance(state, dict) or state.get('errors') != [] or
                not isinstance(state.get('rulesets'), list) or
                any(not isinstance(x, dict) for x in state['rulesets']) or
                not isinstance(state.get('branches'), list) or
                any(not isinstance(x, str) for x in state['branches']) or
                not isinstance(state.get('default_branch'), str)):
            add('UNKNOWN', 'snapshot', 'observation',
                'Complete repository metadata, branch listing, and ruleset details unavailable.')
            continue
        ids = [x['id'] for x in state['rulesets'] if 'id' in x]
        if (any(type(x) is not int or x <= 0 for x in ids) or len(ids) != len(set(ids)) or
                len(state['branches']) != len(set(state['branches']))):
            add('UNKNOWN', 'snapshot', 'observation',
                'Invalid or duplicate branch names/ruleset IDs; snapshot incomplete.')
            continue
        default_ref = 'refs/heads/' + state['default_branch']
        add('OK' if state['default_branch'] == desired['default_branch'] else 'DRIFT',
            'default branch', 'current', f"Observed {state['default_branch']}; expected {desired['default_branch']}.")
        for source in desired['source_refs']:
            ref = source['ref']
            if '*' in ref:
                prefix = ref[:-1]
                names = [x for x in state['branches']
                         if ('refs/heads/' + x).startswith(prefix)
                         and '/' not in ('refs/heads/' + x)[len(prefix):]]
            else:
                names = [x for x in state['branches'] if 'refs/heads/' + x == ref]
            if source['phase'] == 'current':
                add('OK' if names else 'DRIFT', ref, source['phase'],
                    'Source branch exists.' if names else 'Required source branch is absent.')
            else:
                add('GATED', ref, source['phase'],
                    ('Matching branches observed: ' + ', '.join(names) + '. ' if names else 'No matching branch observed. ')
                    + 'Presence and rule coverage do not establish release authorization or source eligibility.')
        for proposal in desired['rulesets']:
            request = proposal['request']
            for wanted in request['conditions']['ref_name']['include']:
                for rule in request['rules']:
                    status, detail = rule_status(state['rulesets'], request, wanted, rule, default_ref)
                    add(status, f"{wanted}: {rule['type']}", proposal['phase'], detail)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, default=POLICY)
    parser.add_argument('--snapshot', type=Path, help='Offline schema 1 snapshot; makes no network requests.')
    parser.add_argument('--json', action='store_true', help='Print the drift report as JSON.')
    args = parser.parse_args(argv)
    try:
        policy = read_json(args.policy)
        snapshot = read_json(args.snapshot) if args.snapshot else collect(policy)
        findings = report(policy, snapshot)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'repository policy: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({'schema': 1, 'scope': 'rulesets only', 'findings': findings}, indent=2))
    else:
        print('Ruleset coverage only; classic branch protection, environments and release evidence are not evaluated.')
        for finding in findings:
            print('{status} [{phase}] {repository} {item}: {detail}'.format(**finding))
        print('Advisory report: no rules or refs changed; OK is not release approval. Known drift does not fail this command.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
