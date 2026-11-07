#!/usr/bin/env python3
"""Deterministic, full-tree publication and history-preserving integration.

An approval is the externally supplied SHA-256 of the complete plan. This tool
cannot approve its own plan. Run with the trusted verifier revision, without secrets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import commit_messages

SHA = re.compile(r'[0-9a-f]{40}\Z')


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM='1', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C',
               GIT_AUTHOR_NAME='Collie Source Publication',
               GIT_AUTHOR_EMAIL='source-publication@collie.im',
               GIT_COMMITTER_NAME='Collie Source Publication',
               GIT_COMMITTER_EMAIL='source-publication@collie.im',
               GIT_AUTHOR_DATE='2000-01-01T00:00:00Z',
               GIT_COMMITTER_DATE='2000-01-01T00:00:00Z')
    return env


def git(repo, *args, data=None):
    command = ['git', '-C', str(repo), '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'commit.gpgSign=false', *args]
    return subprocess.run(command, input=data, stdout=subprocess.PIPE,
                          check=True, env=environment()).stdout


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
    if git(repo, 'for-each-ref', '--format=%(refname)', 'refs/replace').strip():
        raise ValueError('replace refs are forbidden')


def tree(repo, revision):
    return git(repo, 'rev-parse', oid(revision) + '^{tree}').decode().strip()


def commit(repo, tree_id, parents, message):
    args = ['commit-tree', oid(tree_id)]
    for parent in parents:
        args += ['-p', oid(parent)]
    return git(repo, *args, data=message.encode()).decode().strip()


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
    if set(plan) != {'schema', 'signal', 'old_main', 'reconstructed_tree', 'layers', 'final_tree'} or type(plan['schema']) is not int or plan['schema'] != 1:
        raise ValueError('unknown or missing publication-plan fields')
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


def publish(repo, path, approved_digest, expected_main, *, messages=None, approved_messages_sha256=None, historical_messages=False):
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
        parent = commit(repo, layer['after_tree'], [parent], message)
        outputs.append(parent)
    generated = parent
    integration = commit(repo, plan['final_tree'], [plan['old_main'], generated],
                         commit_messages.message(wording['integration']) if wording is not None else 'Integrate reviewed Collie published source tree\n')
    verify(repo, plan, generated, integration, expected_main, messages=wording)
    return {'source_commit': generated, 'source_tree': plan['final_tree'],
            'integration_commit': integration, 'old_main': plan['old_main'],
            'layer_commits': outputs, 'plan_sha256': approved_digest, 'messages_sha256':approved_messages_sha256}


def verify(repo, plan, generated, integration, expected_main, *, messages=None):
    plain(repo)
    validate_plan(plan)
    verify_user_files(repo,plan['final_tree'],plan['old_main'])
    if expected_main != plan['old_main']:
        raise ValueError('stale base')
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
    args = parser.parse_args()
    print(json.dumps(publish(args.repo, args.plan, args.approved_plan_sha256, args.current_main, messages=args.messages, approved_messages_sha256=args.approved_messages_sha256, historical_messages=args.historical_messages), indent=2))
