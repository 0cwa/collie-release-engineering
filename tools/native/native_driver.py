"""Receipt-bound provisional native integration for the release build driver.

A staged binary receipt is not a source build proof. Release assembly remains blocked.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys



# Gradle automatically executes user init scripts and accepts JVM injection from
# these variables before our explicitly verified init script can take effect.
def audit_gradle_environment(env=None):
    env = os.environ if env is None else env
    for key in ('GRADLE_OPTS', 'JAVA_OPTS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'GRADLE_HOME'):
        if env.get(key):
            raise ValueError(f'native diagnostics require {key} to be unset')
    home = Path(env['GRADLE_USER_HOME']).expanduser() if env.get('GRADLE_USER_HOME') else Path.home() / '.gradle'
    for name in ('init.gradle', 'init.gradle.kts', 'gradle.properties'):
        item = home / name
        if item.exists() or item.is_symlink():
            raise ValueError(f'native diagnostics cannot use user Gradle {name}; use a clean Gradle user home')
    init_dir = home / 'init.d'
    if init_dir.is_symlink():
        raise ValueError('native diagnostics cannot use a symlinked Gradle init.d')
    if init_dir.exists():
        if not init_dir.is_dir():
            raise ValueError('native diagnostics require Gradle init.d to be a directory')
        if any(p.name.endswith(('.gradle', '.gradle.kts')) for p in init_dir.iterdir()):
            raise ValueError('native diagnostics cannot use user Gradle init.d scripts; use a clean Gradle user home')


def helper_for(repo: Path):
    path = repo / 'tools/native/stage_artifacts.py'
    if not path.is_file() or path.is_symlink():
        raise ValueError('native artifact gate is missing or not a regular file')
    spec = importlib.util.spec_from_file_location('collie_native_artifact_gate', path)
    if spec is None or spec.loader is None:
        raise ValueError('cannot load native artifact gate')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def integrate(repo: Path, app: Path, lock: Path, input_maven: Path, proof: Path, original: bytes):
    helper = helper_for(repo)
    metadata = app / 'gradle/verification-metadata.xml'
    if metadata.is_symlink() or not metadata.is_file() or metadata.read_bytes() != original:
        raise ValueError('native staging requires the unchanged source verification metadata')
    proof.mkdir(parents=True, exist_ok=True)
    before = proof / 'verification-metadata.before.xml'
    stage = proof / 'artifact-stage'
    if before.exists() or stage.exists():
        raise ValueError('native integration proof already exists; use a fresh report directory')
    before.write_bytes(original)
    try:
        try:
            receipt = helper.stage(lock, input_maven, stage, before)
        except helper.Failure as exc:
            raise ValueError(str(exc)) from exc
        expected = (stage / 'verification-metadata.xml').read_bytes()
        temporary = metadata.with_name('verification-metadata.native.tmp')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError('native verification temporary file already exists')
        temporary.write_bytes(expected)
        temporary.replace(metadata)
        verify(repo, app, lock, proof, original)
        return receipt
    except Exception:
        # The caller supplied an unchanged app; restore that exact file on any failure.
        if not metadata.is_symlink():
            metadata.write_bytes(original)
        raise


def verify(repo: Path, app: Path, lock: Path, proof: Path, original: bytes):
    audit_gradle_environment()
    helper = helper_for(repo)
    before = proof / 'verification-metadata.before.xml'
    stage = proof / 'artifact-stage'
    metadata = app / 'gradle/verification-metadata.xml'
    if before.is_symlink() or not before.is_file() or before.read_bytes() != original:
        raise ValueError('native integration base verification metadata changed')
    try:
        receipt = helper.verify_stage(lock, stage, before)
    except helper.Failure as exc:
        raise ValueError(str(exc)) from exc
    staged_metadata = stage / 'verification-metadata.xml'
    if metadata.is_symlink() or not metadata.is_file() or metadata.read_bytes() != staged_metadata.read_bytes():
        raise ValueError('native integration verification metadata changed')
    return receipt
