#!/usr/bin/env python3
"""Deterministic, full-tree publication and history-preserving integration.

An approval is the externally supplied SHA-256 of the complete plan. This tool
cannot approve its own plan. Run with the trusted verifier revision, without secrets.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import commit_messages

SHA = re.compile(r'[0-9a-f]{40}\Z')
HISTORICAL_PUBLICATION_TIMESTAMP = '2000-01-01T00:00:00Z'
PLAN_V1_FIELDS = {'schema', 'signal', 'old_main', 'reconstructed_tree', 'layers', 'final_tree'}
PLAN_V2_FIELDS = PLAN_V1_FIELDS | {'publication_timestamp'}


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', LC_ALL='C',
               GIT_AUTHOR_NAME='Collie Source Publication',
               GIT_AUTHOR_EMAIL='source-publication@collie.im',
               GIT_COMMITTER_NAME='Collie Source Publication',
               GIT_COMMITTER_EMAIL='source-publication@collie.im')
    return env


def git(repo, *args, data=None, env=None, config=()):
    command = ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'commit.gpgSign=false']
    for key, value in config:
        command += ['-c', f'{key}={value}']
    command += list(args)
    return subprocess.run(command, input=data, stdout=subprocess.PIPE,
                          check=True, env=environment() if env is None else env).stdout


def oid(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError('expected full SHA-1 Git object identity')
    return value


def plain(repo):
    directory = Path(git(repo, 'rev-parse', '--absolute-git-dir').decode().strip())
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
    for root in {directory, common}:
        for path in ('info/grafts', 'objects/info/alternates', 'shallow'):
            if (root / path).exists():
                raise ValueError('grafts, alternates and shallow history are forbidden')
    if any((common / 'objects/pack').glob('*.promisor')):
        raise ValueError('promisor object packs are forbidden')
    result = subprocess.run(['git', '-C', str(repo), 'config', '--local', '--get', 'extensions.partialClone'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment())
    if result.returncode == 0 and result.stdout.strip():
        raise ValueError('partial clone configuration is forbidden')
    if result.returncode not in (0, 1):
        raise ValueError('cannot verify plain Git object configuration')
    result = subprocess.run(['git', '-C', str(repo), 'config', '--local', '--get-regexp', r'^remote\..*\.promisor$'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment())
    if result.returncode == 0 and result.stdout.strip():
        raise ValueError('promisor remotes are forbidden')
    if result.returncode not in (0, 1):
        raise ValueError('cannot verify plain Git object configuration')
    if git(repo, 'for-each-ref', '--format=%(refname)', 'refs/replace').strip():
        raise ValueError('replace refs are forbidden')


def tree(repo, revision):
    return git(repo, 'rev-parse', oid(revision) + '^{tree}').decode().strip()


def validate_publication_timestamp(value, *, allow_historical=False):
    if not isinstance(value, str):
        raise ValueError('publication timestamp must be UTC RFC3339 text')
    try:
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError('publication timestamp must be UTC RFC3339 text') from error
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        raise ValueError('publication timestamp must use canonical UTC seconds')
    if value == HISTORICAL_PUBLICATION_TIMESTAMP and not allow_historical:
        raise ValueError('new publication cannot use the historical fixture timestamp')
    return value


def commit(repo, tree_id, parents, message, *, publication_timestamp=None, historical=False,
           signing_key=None, author_name=None, author_email=None,
           committer_name=None, committer_email=None):
    args = ['commit-tree']
    config = ()
    env = environment()
    if author_name is not None:
        env['GIT_AUTHOR_NAME'] = author_name
    if author_email is not None:
        env['GIT_AUTHOR_EMAIL'] = author_email
    if signing_key is not None:
        if committer_name is None or committer_email is None:
            raise ValueError('signed commits require an approved committer identity')
        args.append('-S')
        config = (('gpg.format', 'ssh'),
                  ('gpg.ssh.program', str(Path(__file__).with_name('ssh-sign-with-agent'))),
                  ('user.signingkey', str(Path(signing_key).resolve())))
        env.update(GIT_COMMITTER_NAME=committer_name,
                   GIT_COMMITTER_EMAIL=committer_email)
    args.append(oid(tree_id))
    for parent in parents:
        args += ['-p', oid(parent)]
    if publication_timestamp is not None:
        publication_timestamp = validate_publication_timestamp(
            publication_timestamp, allow_historical=historical)
        env.update(GIT_AUTHOR_DATE=publication_timestamp,
                   GIT_COMMITTER_DATE=publication_timestamp)
    return git(repo, *args, data=message.encode(), env=env, config=config).decode().strip()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field: ' + key)
        result[key] = value
    return result


def load_plan(raw):
    return validate_plan(json.loads(raw, object_pairs_hook=unique_object))


def validate_plan(plan):
    if not isinstance(plan, dict) or type(plan.get('schema')) is not int:
        raise ValueError('unknown or missing publication-plan fields')
    if ((plan['schema'] == 1 and set(plan) != PLAN_V1_FIELDS)
            or (plan['schema'] == 2 and set(plan) != PLAN_V2_FIELDS)
            or plan['schema'] not in (1, 2)):
        raise ValueError('unknown or missing publication-plan fields')
    if plan['schema'] == 2:
        validate_publication_timestamp(plan['publication_timestamp'])
    for key in ('signal', 'old_main', 'reconstructed_tree', 'final_tree'):
        oid(plan[key])
    if not isinstance(plan['layers'], list) or not plan['layers']:
        raise ValueError('reviewed layers required')
    previous, reconstructed = None, False
    for layer in plan['layers']:
        if set(layer) != {'name', 'before_tree', 'after_tree', 'provenance', 'kind'}:
            raise ValueError('unknown or missing layer fields')
        if layer['kind'] not in ('mechanical', 'compatibility', 'feature', 'standalone-build', 'publication'):
            raise ValueError('invalid publication layer kind')
        for key in ('name', 'provenance'):
            if not isinstance(layer[key], str) or not layer[key].strip() or '\x00' in layer[key]:
                raise ValueError('layer name and provenance required')
        oid(layer['before_tree']); oid(layer['after_tree'])
        if previous is not None and previous != layer['before_tree']:
            raise ValueError('layer order/checkpoint continuity differs')
        if layer['kind'] in ('standalone-build', 'publication') and not reconstructed:
            raise ValueError('overlay precedes exact reconstructed checkpoint')
        previous = layer['after_tree']
        reconstructed |= previous == plan['reconstructed_tree']
    if not reconstructed or previous != plan['final_tree']:
        raise ValueError('missing complete reconstruction/final checkpoint')
    return plan


def verify_user_files(repo,published,application_base):
    """Preserve approved repository content, alongside full final-tree proof."""
    for name in ('README.md','LICENSE','NOTICE','assets'):
        expected=git(repo,'ls-tree','--full-tree',oid(application_base),'--',name)
        if git(repo,'ls-tree','--full-tree',oid(published),'--',name)!=expected:
            raise ValueError('published user-facing content differs from approved application base: '+name)


def publish(repo, path, approved_digest, expected_main, *, messages=None, approved_messages_sha256=None,
            historical_messages=False, historical_publication=False):
    plain(repo)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != approved_digest:
        raise ValueError('publication plan differs from external approval')
    plan = load_plan(raw)
    wording = None
    if messages is None:
        if not historical_messages:raise ValueError('reviewed message input required; historical rendering must be explicit')
    else:
        message_raw=messages.read_bytes()
        if hashlib.sha256(message_raw).hexdigest()!=approved_messages_sha256:raise ValueError('message input differs from external approval')
        wording=commit_messages.validate(json.loads(message_raw,object_pairs_hook=unique_object),plan,approved_digest)
    if oid(expected_main) != plan['old_main']:
        raise ValueError('current base differs from approved base')
    historical = historical_publication or historical_messages
    if plan['schema'] == 1:
        if not historical:
            raise ValueError('new publication plan must bind a publication_timestamp')
        publication_timestamp = HISTORICAL_PUBLICATION_TIMESTAMP
    else:
        publication_timestamp = plan['publication_timestamp']
    if tree(repo, plan['signal']) != plan['layers'][0]['before_tree']:
        raise ValueError('first layer does not start at exact Signal tree')
    verify_user_files(repo,plan['final_tree'],plan['old_main'])
    parent = plan['signal']
    outputs = []
    for index,layer in enumerate(plan['layers']):
        if tree(repo, parent) != layer['before_tree']:
            raise ValueError('parent complete tree differs')
        if git(repo, 'cat-file', '-t', layer['after_tree']).strip() != b'tree':
            raise ValueError('checkpoint is not a complete Git tree')
        message = (layer['name'] + '\n\nComposite/generated publication layer. Original authorship and decisions:\n'
                   + layer['provenance'] + '\n')
        if wording is not None:message=commit_messages.message({k:wording['layers'][index][k] for k in ('subject','body')})
        parent = commit(repo, layer['after_tree'], [parent], message,
                        publication_timestamp=publication_timestamp,
                        historical=plan['schema'] == 1)
        outputs.append(parent)
    generated = parent
    integration = commit(repo, plan['final_tree'], [plan['old_main'], generated],
                         commit_messages.message(wording['integration']) if wording is not None else 'Integrate reviewed Collie published source tree\n',
                         publication_timestamp=publication_timestamp,
                         historical=plan['schema'] == 1)
    verify(repo, plan, generated, integration, expected_main, messages=wording,
           publication_timestamp=publication_timestamp, historical=plan['schema'] == 1)
    return {'source_commit': generated, 'source_tree': plan['final_tree'],
            'integration_commit': integration, 'old_main': plan['old_main'],
            'layer_commits': outputs, 'plan_sha256': approved_digest, 'messages_sha256':approved_messages_sha256}


def verify(repo, plan, generated, integration, expected_main, *, messages=None,
           publication_timestamp=None, historical=False):
    plain(repo)
    validate_plan(plan)
    verify_user_files(repo,plan['final_tree'],plan['old_main'])
    if expected_main != plan['old_main']:
        raise ValueError('stale base')
    publication_timestamp = publication_timestamp or plan.get('publication_timestamp')
    expected_epoch = None
    if publication_timestamp is not None:
        validate_publication_timestamp(publication_timestamp, allow_historical=historical)
        expected_epoch = str(int(datetime.strptime(publication_timestamp, '%Y-%m-%dT%H:%M:%SZ')
                                 .replace(tzinfo=timezone.utc).timestamp()))

    def check_timestamp(revision):
        actual = git(repo, 'show', '-s', '--format=%at%x00%ct', oid(revision)).decode().strip().split('\x00')
        if actual != [expected_epoch, expected_epoch]:
            raise ValueError('generated commit dates differ from approved publication timestamp')

    if expected_epoch is not None:
        check_timestamp(generated)
        check_timestamp(integration)
    if tree(repo, generated) != plan['final_tree'] or tree(repo, integration) != plan['final_tree']:
        raise ValueError('complete final tree differs')
    parents = git(repo, 'show', '-s', '--format=%P', oid(integration)).decode().split()
    if parents != [plan['old_main'], generated]:
        raise ValueError('integration parents differ')
    if messages is not None:
        commit_messages.validate(messages,plan,messages['publication_plan_sha256'])
        actual=git(repo,'cat-file','commit',integration).split(b'\n\n',1)[1]
        if actual!=commit_messages.message(messages['integration']).encode():raise ValueError('integration wording differs')
    cursor = generated
    for index in reversed(range(len(plan['layers']))):
        layer=plan['layers'][index]
        if expected_epoch is not None:
            check_timestamp(cursor)
        if messages is not None:
            actual=git(repo,'cat-file','commit',cursor).split(b'\n\n',1)[1]
            expected=commit_messages.message({k:messages['layers'][index][k] for k in ('subject','body')}).encode()
            if actual!=expected:raise ValueError('layer wording differs')
        if tree(repo, cursor) != layer['after_tree']:
            raise ValueError('published intermediate checkpoint differs')
        parents = git(repo, 'show', '-s', '--format=%P', cursor).decode().split()
        if len(parents) != 1:
            raise ValueError('publication layer must have exactly one parent')
        cursor = parents[0]
    if cursor != plan['signal']:
        raise ValueError('publication history must start at authentic Signal commit')
    git(repo, 'merge-base', '--is-ancestor', plan['signal'], generated)
    git(repo, 'merge-base', '--is-ancestor', plan['old_main'], integration)


def round_trip(repo, before, reviewed, destination):
    """Export every changed byte/mode for a reviewed ordinary application commit."""
    plain(repo)
    if destination.exists():
        raise ValueError('refusing to overwrite an existing patch')
    parents = git(repo, 'show', '-s', '--format=%P', oid(reviewed)).decode().split()
    if parents != [oid(before)]:
        raise ValueError('round-trip requires exact reviewed single-parent delta')
    destination.write_bytes(git(repo, 'diff', '--binary', '--full-index', '--no-ext-diff',
                               '--no-textconv', '--no-renames', before, reviewed, '--'))
    return {'before_tree': tree(repo, before), 'after_tree': tree(repo, reviewed),
            'patch_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True)
    parser.add_argument('--current-main', required=True)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--messages',type=Path)
    selection.add_argument('--historical-messages',action='store_true',help='reproduce already-published historical candidate wording only')
    parser.add_argument('--approved-messages-sha256')
    parser.add_argument('--historical-publication', action='store_true',
                        help='replay a schema-1 publication plan with its original fixture timestamp')
    args = parser.parse_args()
    print(json.dumps(publish(args.repo, args.plan, args.approved_plan_sha256, args.current_main,
                             messages=args.messages, approved_messages_sha256=args.approved_messages_sha256,
                             historical_messages=args.historical_messages,
                             historical_publication=args.historical_publication), indent=2))
