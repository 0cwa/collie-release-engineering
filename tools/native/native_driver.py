"""Receipt-bound provisional native integration for the release build driver.

A staged binary receipt is not a source build proof. Release assembly remains blocked.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys


def audit_fresh_environment(project, env):
    """Reject inherited build overrides and Cargo configuration for fresh builds.

    Dependency caches may be supplied, but their authenticity is a separate
    requirement. This is an execution guard, not a hermetic-build attestation.
    """
    blocked = ('CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET_DIR', 'RUSTC_WRAPPER',
               'RUSTC_WORKSPACE_WRAPPER', 'RUSTC', 'RUSTDOC', 'RUSTFLAGS',
               'CARGO_ENCODED_RUSTFLAGS', 'CCACHE_PREFIX', 'CCACHE_DIR',
               'SCCACHE_DIR', 'SCCACHE_BUCKET', 'GOMA_DIR', 'GOMACC_PATH',
               'CC_WRAPPER', 'CXX_WRAPPER', 'NINJAFLAGS', 'NINJA_ARGS',
               'GN_ARGS', 'GN_DEFINES', 'CC', 'CXX', 'AR', 'LD', 'RUSTDOCFLAGS',
               'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'ARFLAGS', 'RANLIB',
               'HOST_CC', 'HOST_CXX', 'TARGET_CC', 'TARGET_CXX', 'MAKEFLAGS',
               'LD_PRELOAD',
               'LD_LIBRARY_PATH', 'LIBRARY_PATH',
               'CARGO_ENCODED_RUSTDOCFLAGS', 'BASH_ENV', 'ENV', 'PYTHONPATH',
               'PYTHONHOME', 'PYTHONUSERBASE', 'RUSTC_BOOTSTRAP',
               'USE_PREBUILD', 'BUILD_WHAT', 'M2_HOME', 'MAVEN_HOME',
               'MAVEN_USER_HOME', 'MAVEN_OPTS', 'MAVEN_ARGS')
    for key in blocked:
        if env.get(key):
            raise ValueError('Fresh diagnostic requires ' + key + ' to be unset')
    for key, value in env.items():
        if value and key.startswith(('SISO_', 'RBE_', 'RECLIENT_', 'BASH_FUNC_',
                                     'CARGO_BUILD_', 'CARGO_TARGET_', 'CARGO_PROFILE_',
                                     'ORG_GRADLE_PROJECT_', 'CC_', 'CXX_', 'AR_', 'LD_',
                                     'CFLAGS_', 'CXXFLAGS_', 'CPPFLAGS_', 'LDFLAGS_', 'CMAKE_')):
            raise ValueError('Fresh diagnostic does not permit inherited build override: ' + key)
    maven_home = Path.home() / '.m2'
    if maven_home.is_symlink():
        raise ValueError('Fresh diagnostic cannot use a symlinked Maven local directory')
    if os.path.lexists(maven_home / 'settings.xml'):
        raise ValueError('Fresh diagnostic cannot use user Maven settings.xml')
    maven_repository = maven_home / 'repository'
    if os.path.lexists(maven_repository) and (maven_repository.is_symlink() or
            not maven_repository.is_dir() or any(maven_repository.iterdir())):
        raise ValueError('Fresh diagnostic requires an absent or empty Maven local repository')
    cargo_home = Path(env['CARGO_HOME']).expanduser() if env.get('CARGO_HOME') else Path.home() / '.cargo'
    if not cargo_home.is_absolute():
        raise ValueError('Fresh diagnostic requires an absolute CARGO_HOME')
    for directory in [cargo_home, *(ancestor / '.cargo' for ancestor in project.parents)]:
        for name in ('config', 'config.toml'):
            if os.path.lexists(directory / name):
                raise ValueError('Fresh diagnostic cannot use external Cargo configuration: ' + str(directory / name))
    # Tracked project configuration is covered by the independently replayed tree.
    # Local ignored configuration must not override the checked-in build.
    import prepare_sources
    for current, directories, files in os.walk(project, followlinks=False):
        if '.git' in directories:
            directories.remove('.git')
        current = Path(current)
        if '.cargo' in directories:
            directory = current / '.cargo'
            if directory.is_symlink():
                raise ValueError('Fresh diagnostic cannot use a symlinked Cargo configuration directory: ' + str(directory))
            for name in ('config', 'config.toml'):
                path = directory / name
                if not os.path.lexists(path):
                    continue
                if not path.is_file() or path.is_symlink():
                    raise ValueError('Fresh diagnostic Cargo configuration must be a regular tracked file: ' + str(path))
                prepare_sources.git(project, 'ls-files', '--error-unmatch', path.relative_to(project).as_posix())
            directories.remove('.cargo')
        if '.cargo' in files:
            raise ValueError('Fresh diagnostic Cargo configuration directory is not a directory: ' + str(current / '.cargo'))



# Gradle automatically executes user init scripts and accepts JVM injection from
# these variables before our explicitly verified init script can take effect.
def audit_gradle_environment(env=None):
    env = os.environ if env is None else env
    for key in ('GRADLE_OPTS', 'JAVA_OPTS', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'GRADLE_HOME'):
        if env.get(key):
            raise ValueError(f'native diagnostics require {key} to be unset')
    home = Path(env['GRADLE_USER_HOME']).expanduser() if env.get('GRADLE_USER_HOME') else Path.home() / '.gradle'
    if not home.is_absolute():
        raise ValueError('native diagnostics require an absolute GRADLE_USER_HOME')
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
