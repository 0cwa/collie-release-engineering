"""Stage one local Linux host JNI for test-only use; never a release proof."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

import prepare_sources
import stage_artifacts

class Failure(Exception): pass

def require(value, message):
    if not value: raise Failure(message)

def init_text(resource, empty, sha):
    resource_uri = json.dumps(resource.resolve().as_uri())
    empty_path = json.dumps(empty.resolve().as_uri())
    return f'''import java.security.MessageDigest
import java.util.zip.ZipFile

gradle.projectsEvaluated {{
 allprojects {{ project ->
  project.tasks.withType(org.gradle.api.tasks.testing.Test).configureEach {{ task ->
    def nativeDir = new File(new URI({resource_uri}))
    def fallbackDir = new File(new URI({empty_path}))
    if (task.systemProperties.containsKey('java.library.path') || task.jvmArgs.any {{ it.startsWith('-Djava.library.path') }}) throw new GradleException('Conflicting host JNI library path')
    task.classpath = project.files(nativeDir) + task.classpath
    task.systemProperty('java.library.path', fallbackDir.absolutePath)
    task.systemProperty('collie.host.jni.path', new File(nativeDir, 'libsignal_jni_testing_amd64.so').absolutePath)
    task.systemProperty('collie.host.jni.sha256', '{sha}')
    task.outputs.upToDateWhen {{ false }}
    task.outputs.cacheIf {{ false }}
    doFirst {{
      def names = ['libsignal_jni_testing_amd64.so','libsignal_jni_testing.so','libsignal_jni_amd64.so','libsignal_jni.so'] as Set
      def nativeFile = new File(nativeDir, 'libsignal_jni_testing_amd64.so')
      def empty = new File(new URI({empty_path}))
      if (!empty.isDirectory() || java.nio.file.Files.isSymbolicLink(empty.toPath()) || empty.list().length != 0) throw new GradleException('Host JNI fallback directory is not empty')
      if (!nativeDir.isDirectory() || java.nio.file.Files.isSymbolicLink(nativeDir.toPath()) || (nativeDir.list() as Set) != (['libsignal_jni_testing_amd64.so'] as Set)) throw new GradleException('Host JNI resource directory contains unexpected entries')
      if (!nativeFile.isFile() || java.nio.file.Files.isSymbolicLink(nativeFile.toPath())) throw new GradleException('Host JNI resource is missing or a symlink')
      def hash = MessageDigest.getInstance('SHA-256')
      nativeFile.withInputStream {{ stream ->
        byte[] buffer = new byte[1048576]
        int n
        while ((n = stream.read(buffer)) != -1) hash.update(buffer, 0, n)
      }}
      if (hash.digest().encodeHex().toString() != '{sha}') throw new GradleException('Host JNI resource hash mismatch')
      if (task.classpath.files.toList().count {{ it == nativeDir }} != 1) throw new GradleException('Host JNI resource directory is not present exactly once')
      def guardedProperties = ['java.library.path', 'os.arch', 'os.name', 'collie.host.jni.path', 'collie.host.jni.sha256']
      def conflictingArg = {{ arg -> guardedProperties.any {{ key -> arg.toString() == '-D' + key || arg.toString().startsWith('-D' + key + '=') }} }}
      if (task.systemProperties.get('collie.host.jni.path') != nativeFile.absolutePath || task.systemProperties.get('collie.host.jni.sha256') != '{sha}' || task.systemProperties.get('java.library.path') != empty.absolutePath || task.systemProperties.containsKey('os.arch') || task.systemProperties.containsKey('os.name') || task.jvmArgs.any(conflictingArg)) throw new GradleException('Conflicting host JNI JVM property')
      task.jvmArgumentProviders.each {{ provider ->
        if (provider.asArguments().any(conflictingArg)) throw new GradleException('Conflicting host JNI JVM argument provider')
      }}
      task.classpath.files.each {{ f ->
        if (f == nativeDir) return
        if (f.isDirectory()) {{
          names.each {{ n -> if (new File(f, n).exists()) throw new GradleException('Competing host JNI classpath resource: ' + n) }}
        }} else if (f.isFile()) {{
          try {{
            def archive = new ZipFile(f)
            try {{
              def entries = archive.entries()
              int manifests = 0
              while (entries.hasMoreElements()) {{
                def entry = entries.nextElement()
                def name = entry.name
                if (names.contains(name) || names.any {{ n -> name ==~ ('META-INF/versions/[0-9]+/' + java.util.regex.Pattern.quote(n)) }}) throw new GradleException('Competing host JNI archive resource: ' + name)
                if (name.equalsIgnoreCase('META-INF/MANIFEST.MF')) {{
                  manifests++
                  if (manifests > 1) throw new GradleException('Duplicate JAR manifest')
                  archive.getInputStream(entry).withCloseable {{ input ->
                    def manifest = new java.util.jar.Manifest(input)
                    def extra = manifest.mainAttributes.getValue(java.util.jar.Attributes.Name.CLASS_PATH)
                    if (extra != null && !extra.trim().isEmpty()) throw new GradleException('Unscanned JAR manifest Class-Path')
                  }}
                }}
              }}
            }} finally {{ archive.close() }}
          }} catch (java.util.zip.ZipException ex) {{ throw new GradleException('Cannot inspect host test classpath file: ' + f, ex) }}
        }} else if (!f.exists() && !java.nio.file.Files.isSymbolicLink(f.toPath())) {{
          return // Gradle commonly includes absent resource output directories.
        }} else {{ throw new GradleException('Host test classpath entry cannot be inspected: ' + f) }}
      }}
    }}
  }}
 }}
}}
'''

def verify_elf(path):
    with path.open('rb') as f: header = f.read(20)
    require(len(header) == 20 and header[:6] == b'\x7fELF\x02\x01' and int.from_bytes(header[16:18], 'little') == 3 and int.from_bytes(header[18:20], 'little') == 62, 'Host JNI must be an x86-64 Linux ELF shared library')

def stage(lock, sources, library, sha, output):
    require(re.fullmatch(r'[0-9a-f]{64}', sha or '') is not None, 'Expected SHA-256 is required')
    library = library.absolute()
    require(library.is_file() and not library.is_symlink(), 'Host JNI must be a regular file')
    verify_elf(library)
    require(stage_artifacts.digest(library) == sha, 'Host JNI hash differs')
    proof = prepare_sources.verify_prepared(lock, sources)
    output = output.absolute()
    require(not output.exists(), 'Host JNI stage already exists')
    output.mkdir(parents=True)
    created = output.stat()
    try:
        resource = output / 'resource'
        empty = output / 'empty-library-path'
        resource.mkdir(); empty.mkdir()
        target = resource / 'libsignal_jni_testing_amd64.so'
        # Snapshot a single test artifact, never hard-link the mutable build output.
        fd = os.open(library, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as source, target.open('xb') as dest:
            require(stat.S_ISREG(os.fstat(source.fileno()).st_mode), 'Host JNI must be a regular file')
            before = os.fstat(source.fileno())
            shutil.copyfileobj(source, dest, 1024 * 1024)
            after = os.fstat(source.fileno())
            require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Host JNI source changed during snapshot')
        target.chmod(0o600)
        verify_elf(target)
        require(stage_artifacts.digest(target) == sha, 'Staged host JNI hash differs')
        script = init_text(resource, empty, sha)
        (output / 'host-jni.init.gradle').write_text(script)
        receipt = {'schema':1, 'scope':'local test input only; source-to-binary provenance and runtime privacy unverified', 'release_allowed':False, 'lock_sha256':stage_artifacts.digest(lock), 'source_tree':proof['sources']['libsignal']['prepared_tree'], 'testing_jni_sha256':sha, 'init_sha256':hashlib.sha256(script.encode()).hexdigest()}
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        return receipt
    except Exception:
        if output.is_dir() and not output.is_symlink():
            current = output.stat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                shutil.rmtree(output)
        raise

def verify(lock, sources, output, sha):
    require(re.fullmatch(r'[0-9a-f]{64}', sha or '') is not None, 'Expected SHA-256 is required')
    output = output.absolute()
    require(output.is_dir() and not output.is_symlink(), 'Host JNI stage must be a real directory')
    output = output.resolve(strict=True)
    proof = prepare_sources.verify_prepared(lock, sources)
    resource = output / 'resource'
    empty = output / 'empty-library-path'
    target = stage_artifacts.safe_file(output, resource / 'libsignal_jni_testing_amd64.so')
    verify_elf(target)
    require(stage_artifacts.digest(target) == sha, 'Staged host JNI hash differs')
    require(empty.is_dir() and not empty.is_symlink() and not any(empty.iterdir()), 'Host JNI fallback directory is not empty')
    require(resource.is_dir() and not resource.is_symlink() and {p.name for p in resource.iterdir()} == {target.name}, 'Unexpected host JNI resource files')
    expected_script = init_text(resource, empty, sha).encode()
    script = stage_artifacts.safe_file(output, output / 'host-jni.init.gradle').read_bytes()
    require(script == expected_script, 'Host JNI init script differs from expected transformation')
    receipt = json.loads(stage_artifacts.safe_file(output, output / 'receipt.json').read_text())
    expected_receipt = {'schema':1, 'scope':'local test input only; source-to-binary provenance and runtime privacy unverified', 'release_allowed':False, 'lock_sha256':stage_artifacts.digest(lock), 'source_tree':proof['sources']['libsignal']['prepared_tree'], 'testing_jni_sha256':sha, 'init_sha256':hashlib.sha256(expected_script).hexdigest()}
    require(receipt == expected_receipt, 'Host JNI receipt differs from verified inputs')
    return receipt

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for x in ('lock','sources','output'): p.add_argument('--'+x, type=Path, required=True)
    p.add_argument('--library', type=Path)
    p.add_argument('--verify', action='store_true')
    p.add_argument('--sha256', required=True)
    a=p.parse_args()
    try:
        if a.verify:
            require(a.library is None, 'Do not pass --library to verification')
            result = verify(a.lock,a.sources,a.output,a.sha256)
        else:
            require(a.library is not None, '--library is required for staging')
            result = stage(a.lock,a.sources,a.library,a.sha256,a.output)
        print(json.dumps(result, indent=2))
    except (Failure, prepare_sources.Failure, stage_artifacts.Failure, OSError, ValueError) as e:
        print('Host JNI stage failed: '+str(e), file=sys.stderr);return 1
    return 0
if __name__ == '__main__': sys.exit(main())
