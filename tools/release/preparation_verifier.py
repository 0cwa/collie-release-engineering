"""Trusted operator-side verifier for a bounded Git release-preparation delta.

This is a machine check only. Operators must independently review the actual
project log and changelog bytes before approving the resulting receipt digest.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import runtime_receipts

VERIFIER_ID = 'collie-preparation-verifier/v1'
PROPERTY_KEYS = ('collieBuildRevision', 'collieBuildTimestamp', 'releaseVersionName', 'releaseVersionCode')
ASSIGNMENT = re.compile(r'^\s*([^\s:=]+)\s*(?:[=:]\s*|\s+)(.*?)\s*$')
TOPICS = {'setup', 'push', 'notifications', 'backups', 'privacy', 'security', 'updates', 'devices', 'settings', 'look', 'fixes', 'signal'}


def hardened_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_ATTR_NOSYSTEM='1', GIT_NO_LAZY_FETCH='1')
    return env


def git(repo, *args, binary=False):
    result = subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                             '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null', *args],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=hardened_env())
    return result.stdout if binary else result.stdout.decode('ascii', 'strict').strip()


def require_full_commit(repo, value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('commit identity must be a full lowercase SHA-1')
    if git(repo, 'cat-file', '-t', value) != 'commit' or git(repo, 'rev-parse', '--verify', value + '^{commit}') != value:
        raise ValueError('commit does not resolve to the exact local commit object')
    return value


def properties(data):
    values, retained = {}, []
    try:
        text = data.decode('utf-8', 'strict')
    except UnicodeError as error:
        raise ValueError('gradle.properties is not UTF-8') from error
    # Java Properties natural lines end only at CR, LF, or CRLF. In
    # particular, form feed is property whitespace, not a line boundary.
    for line in re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', text):
        if not line:
            continue
        stripped = line.lstrip()
        if not stripped.strip() or stripped.startswith(('#', '!')):
            retained.append(line)
            continue
        match = ASSIGNMENT.fullmatch(line.rstrip('\r\n'))
        if not match:
            raise ValueError('unsupported Gradle property syntax')
        key, value = match.groups()
        if '\\' in key or (len(value) - len(value.rstrip('\\'))) % 2:
            raise ValueError('escaped keys or continued property values are unsupported')
        if key.startswith('systemProp.org.gradle.project.') or key == 'org.gradle.jvmargs' and ('org.gradle.project.' in value or '\\' in value):
            raise ValueError('Gradle property alias or JVM override is unsupported')
        if key in PROPERTY_KEYS:
            if key in values or not re.fullmatch(r'[A-Za-z0-9._-]+', value) or line.rstrip('\r\n') != key + '=' + value:
                raise ValueError('duplicate or noncanonical release property')
            values[key] = value
        else:
            retained.append(line)
    return values, ''.join(retained)


def tree_entries(repo, tree):
    raw = git(repo, 'ls-tree', '-r', '-z', '--full-tree', tree, binary=True)
    result = {}
    for record in filter(None, raw.split(b'\0')):
        meta, raw_path = record.split(b'\t', 1)
        mode, kind, oid = meta.split()
        path = raw_path.decode('utf-8', 'strict')
        if path in result:
            raise ValueError('duplicate Git tree path')
        result[path] = {'blob': oid.decode('ascii'), 'mode': mode.decode('ascii'), 'type': kind.decode('ascii')}
    return result


def blob(repo, entry):
    if entry is None or entry['type'] != 'blob' or entry['mode'] not in ('100644', '100755'):
        raise ValueError('release preparation paths must be regular non-executable blobs')
    return git(repo, 'cat-file', 'blob', entry['blob'], binary=True)


def validate_project_log(content, version):
    try:
        text = content.decode('utf-8', 'strict').replace('\r\n', '\n')
    except UnicodeError as error:
        raise ValueError('project log is not UTF-8') from error
    if not text.endswith('\n') or '\r' in text:
        raise ValueError('project log must use newline-terminated UTF-8')
    blocks = re.split(r'\n\s*\n', text.strip())
    header = blocks[0].splitlines() if blocks else []
    if len(header) != 2 or header[0] != f'version: {version}' or not re.fullmatch(r'date: [0-9]{4}-[0-9]{2}-[0-9]{2}', header[1]):
        raise ValueError('project log release header is malformed')
    try:
        datetime.date.fromisoformat(header[1][6:])
    except ValueError as error:
        raise ValueError('project log release date is invalid') from error
    entries = 0
    for block in blocks[1:]:
        lines = block.splitlines()
        if not lines:
            raise ValueError('empty project log entry')
        sentence = False
        fields = set()
        sentence_lines = []
        for line in lines:
            if not line.strip():
                continue
            match = re.fullmatch(r'([a-z]+):\s*(.*)', line.strip())
            if not sentence and match and match.group(1) in {'topic', 'pr'}:
                key, value = match.groups()
                if key in fields or not value:
                    raise ValueError('project log field is malformed')
                fields.add(key)
                if key == 'topic' and value.lower() not in TOPICS:
                    raise ValueError('unknown project log topic')
            else:
                sentence = True
                sentence_lines.append(line.strip())
        if not sentence:
            raise ValueError('project log entry has no sentence')
        sentence_text = ' '.join(sentence_lines)
        if len(sentence_text.encode('utf-16-le')) // 2 > 120:
            raise ValueError('project log entry exceeds the app limit of 120 characters')
        entries += 1
    if entries == 0:
        raise ValueError('project log contains no entries')


def validate_changelog(content):
    if len(content) > 500 or any(byte > 127 for byte in content):
        raise ValueError('release changelog must be ASCII and at most 500 bytes')


def preparation(repo, baseline, plan_path, approved_plan_sha256, channel):
    context = runtime_receipts.plan(argparse.Namespace(release_plan=plan_path,
        approved_release_plan_sha256=approved_plan_sha256, channel=channel))
    approved, plan_sha = context
    if approved['mode'] != 'new-source' or approved['purpose'] != 'production':
        raise ValueError('preparation requires an approved production new-source plan')
    baseline = require_full_commit(repo, baseline)
    final = require_full_commit(repo, approved['source_commit'])
    subprocess.run(['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
                    '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null',
                    'merge-base', '--is-ancestor', baseline, final], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=hardened_env())
    head = require_full_commit(repo, git(repo, 'rev-parse', 'HEAD'))
    if head != final:
        raise ValueError('final checkout does not match approved release-plan source commit')
    # Reuse the established strict raw-worktree/index/store verification.
    runtime_receipts.source_worktree_matches_index(repo, final, git(repo, 'rev-parse', final + '^{tree}'), require_head_tree=True)
    baseline_tree = git(repo, 'rev-parse', baseline + '^{tree}')
    final_tree = git(repo, 'rev-parse', final + '^{tree}')
    source_time = git(repo, 'show', '--no-show-signature', '-s', '--format=%ct', baseline)
    before, before_other = properties(blob(repo, tree_entries(repo, baseline_tree)['gradle.properties']))
    after, after_other = properties(blob(repo, tree_entries(repo, final_tree)['gradle.properties']))
    if before_other != after_other:
        raise ValueError('non-release Gradle properties changed during preparation')
    values = after
    if set(values) != set(PROPERTY_KEYS):
        raise ValueError('prepared Gradle properties must contain each release property exactly once')
    if values['collieBuildRevision'] != baseline or values['collieBuildTimestamp'] != source_time:
        raise ValueError('release baseline or timestamp differs from resolved Git object')
    if values['releaseVersionName'] != approved['version_name'] or values['releaseVersionCode'] != str(approved['version_code']):
        raise ValueError('release version differs from approved plan')
    if not re.fullmatch(r'[1-9][0-9]*', values['releaseVersionCode']):
        raise ValueError('invalid release version code')
    edition = {'stable': 'android', 'trailblazer-beta': 'trailblazer-beta'}.get(channel)
    if edition is None:
        raise ValueError('unknown release channel')
    log_path = f'app/src/main/assets/collie-log/releases/{values["releaseVersionName"]}.txt'
    changelog_path = f'fastlane/metadata/{edition}/en-US/changelogs/{values["releaseVersionCode"]}.txt'
    old, new = tree_entries(repo, baseline_tree), tree_entries(repo, final_tree)
    diff = git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--ignore-submodules=none',
               '--name-status', '-z', baseline, final, binary=True).split(b'\0')
    if diff[-1:] == [b'']:
        diff.pop()
    if len(diff) % 2:
        raise ValueError('invalid raw Git preparation diff')
    changed = {}
    for i in range(0, len(diff), 2):
        status, path = (x.decode('utf-8', 'strict') for x in diff[i:i+2])
        if status not in ('A', 'M', 'D') or path in changed:
            raise ValueError('unsupported or duplicate preparation change')
        changed[path] = {'change': status, 'old': old.get(path), 'new': new.get(path)}
    if changed.get('gradle.properties', {}).get('change') != 'M':
        raise ValueError('release properties must be modified')
    allowed = {'gradle.properties', log_path, changelog_path}
    deletions = {path for path, entry in changed.items() if entry['change'] == 'D' and re.fullmatch(r'app/src/main/assets/collie-log/unreleased/[0-9]+\.txt', path)}
    if not deletions or set(changed) != allowed | deletions or changed[log_path]['change'] != 'A' or changed[changelog_path]['change'] != 'A':
        raise ValueError('preparation delta is missing required outputs or contains an unapproved path')
    for entry in changed.values():
        for tree_entry in (entry['old'], entry['new']):
            if tree_entry is not None and (tree_entry['type'] != 'blob' or tree_entry['mode'] != '100644'):
                raise ValueError('preparation changes must use ordinary non-executable files')
    validate_project_log(blob(repo, new[log_path]), values['releaseVersionName'])
    validate_changelog(blob(repo, new[changelog_path]))
    manifest = {'schema': 1, 'kind': 'release-preparation', 'verifier_id': VERIFIER_ID,
        'release_plan_sha256': plan_sha, 'channel': channel,
        'version_name': values['releaseVersionName'], 'version_code': int(values['releaseVersionCode']),
        'baseline_commit': baseline, 'baseline_tree': baseline_tree, 'baseline_committer_time': int(source_time),
        'final_commit': final, 'final_tree': final_tree,
        'delta': [{'path': path, **changed[path]} for path in sorted(changed)]}
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--baseline-commit', required=True, help='full commit SHA selected and pinned by the operator before preparation')
    parser.add_argument('--release-plan', type=Path, required=True)
    parser.add_argument('--approved-release-plan-sha256', required=True)
    parser.add_argument('--channel', choices=('stable', 'trailblazer-beta'), required=True)
    parser.add_argument('--output', type=Path, required=True, help='write canonical preparation.json outside app source')
    args = parser.parse_args()
    value = preparation(args.checkout, args.baseline_commit, args.release_plan,
                        args.approved_release_plan_sha256, args.channel)
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')
    checkout = args.checkout.resolve()
    # Retain the supplied final component: resolving it would follow a
    # dangling symlink before O_EXCL/O_NOFOLLOW can reject that path.
    output = args.output.parent.resolve() / args.output.name
    if output == checkout or checkout in output.parents:
        raise ValueError('preparation manifest output must be outside the source checkout')
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
    print('Machine verification only: review project log and changelog bytes independently before approving any receipt digest.')
    print('Wrote canonical preparation manifest; this output is not an approval or a trusted-verifier signature.')
    print(hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
