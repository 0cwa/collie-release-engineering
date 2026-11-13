#!/usr/bin/env python3
"""Compare Gradle compiler inputs with successfully extracted CodeQL files."""
import argparse
import csv
import json
from pathlib import Path
import subprocess


def check(root, inputs, extracted):
    root = root.resolve()
    try:
        tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0'))
    except subprocess.CalledProcessError as error:
        raise ValueError('could not list tracked source files') from error
    expected = {'java': set(), 'kotlin': set()}
    tasks = {'java': [], 'kotlin': []}
    for line in inputs.read_text().splitlines():
        record = json.loads(line)
        language = record['language']
        if language not in expected or not isinstance(record['task'], str) or not isinstance(record['files'], list):
            raise ValueError('invalid compiler input record')
        tasks[language].append(record['task'])
        suffix = '.java' if language == 'java' else '.kt'
        for name in record['files']:
            path = Path(name)
            if not path.is_absolute() or not name.endswith(suffix):
                raise ValueError('invalid compiler source path')
            try:
                relative = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if relative in tracked:
                expected[language].add(relative)
    with extracted.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.reader(handle))
    if any(len(row) != 1 for row in rows):
        raise ValueError('invalid CodeQL file inventory')
    actual = {row[0] for row in rows}
    result = {}
    for language, files in expected.items():
        matched = files & actual
        result[language] = {'tasks': sorted(set(tasks[language])), 'expected': sorted(files),
                            'extracted': sorted(matched), 'missing': sorted(files - actual)}
    result['passed'] = all(expected[lang] and not result[lang]['missing'] for lang in expected)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--extracted', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(args.root, args.inputs, args.extracted)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        result = {'passed': False, 'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
