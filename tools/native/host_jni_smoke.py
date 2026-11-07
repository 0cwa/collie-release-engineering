"""Run a small offline Gradle/JUnit host-JNI guard fixture; not release proof."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import host_jni_guard
import native_driver
import verify_gradle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('lock', 'sources', 'stage', 'gradle-zip', 'gradle-distribution', 'junit', 'hamcrest', 'client', 'kotlin'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    a = parser.parse_args()
    host_jni_guard.verify(a.lock, a.sources, a.stage, a.sha256)
    gradle = verify_gradle.verify(a.gradle_zip, a.gradle_distribution, '87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396', top='gradle-9.6.0')
    for path in (a.junit, a.hamcrest, a.client, a.kotlin):
        if not path.is_file(): raise ValueError('Smoke-test classpath file missing')
    # The caller controls TMPDIR; task scratch is recommended.
    with tempfile.TemporaryDirectory(prefix='host-jni-gradle-smoke-') as tmp:
        root = Path(tmp)
        (root / 'src/test/java').mkdir(parents=True)
        (root / 'tmp').mkdir()
        (root / 'settings.gradle').write_text("rootProject.name = 'host-jni-guard-probe'\n")
        (root / 'build.gradle').write_text('''plugins { id 'java' }
repositories { }
dependencies {
  testImplementation files(System.getenv('PROBE_JUNIT'), System.getenv('PROBE_HAMCREST'), System.getenv('PROBE_CLIENT'), System.getenv('PROBE_KOTLIN'))
  if (project.hasProperty('addEvil')) testImplementation files('evil.jar')
  if (project.hasProperty('manifestEvil')) testImplementation files('manifest-evil.jar')
  if (project.hasProperty('multiEvil')) testImplementation files('multi-evil.jar')
}
test {
  jvmArgs '-Djava.io.tmpdir=' + file('tmp').absolutePath
  if (project.hasProperty('archEvil')) systemProperty 'os.arch', 'fake'
  if (project.hasProperty('providerEvil')) jvmArgumentProviders.add({ -> ['-Djava.library.path=/stock'] } as org.gradle.process.CommandLineArgumentProvider)
}
''')
        (root / 'src/test/java/NativeProbeTest.java').write_text('''import org.junit.Test;
import static org.junit.Assert.assertFalse;
import org.signal.libsignal.internal.Native;
public class NativeProbeTest {
  @Test public void patchedNativeCall() { assertFalse(Native.AccountEntropyPool_IsValid("invalid")); }
}
''')
        with ZipFile(root / 'evil.jar', 'w') as z:
            z.writestr('libsignal_jni_testing_amd64.so', b'competing fixture')
        with ZipFile(root / 'manifest-evil.jar', 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nClass-Path: companion.jar\r\n\r\n')
        with ZipFile(root / 'companion.jar', 'w') as z:
            z.writestr('libsignal_jni.so', b'competing fixture')
        with ZipFile(root / 'multi-evil.jar', 'w') as z:
            z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nMulti-Release: true\r\n\r\n')
            z.writestr('META-INF/versions/21/libsignal_jni.so', b'competing fixture')
        env = os.environ.copy()
        env.update(GRADLE_USER_HOME=str(root / 'gradle-home'), PROBE_JUNIT=str(a.junit.resolve()), PROBE_HAMCREST=str(a.hamcrest.resolve()), PROBE_CLIENT=str(a.client.resolve()), PROBE_KOTLIN=str(a.kotlin.resolve()))
        native_driver.audit_gradle_environment(env)
        init = a.stage.resolve() / 'host-jni.init.gradle'
        command = [str(gradle), '--project-dir', str(root), '--offline', '--no-daemon', '--init-script', str(init)]
        subprocess.run(command + ['test'], cwd=root, env=env, check=True)
        result = ET.parse(root / 'build/test-results/test/TEST-NativeProbeTest.xml').getroot()
        if (result.get('tests'), result.get('failures'), result.get('errors'), result.get('skipped')) != ('1','0','0','0'):
            raise ValueError('Expected one passing native JNI test')
        for option, message in (('addEvil', 'Competing host JNI archive resource'), ('manifestEvil', 'Unscanned JAR manifest Class-Path'), ('multiEvil', 'Competing host JNI archive resource'), ('archEvil', 'Conflicting host JNI JVM property'), ('providerEvil', 'Conflicting host JNI JVM argument provider')):
            negative = subprocess.run(command + ['-P' + option, 'test'], cwd=root, env=env, capture_output=True, text=True)
            if negative.returncode == 0 or message not in (negative.stdout + negative.stderr):
                raise ValueError('Competing host JNI resource was not rejected: ' + option)
        host_jni_guard.verify(a.lock, a.sources, a.stage, a.sha256)
        verify_gradle.verify(a.gradle_zip, a.gradle_distribution, '87a2216cc1f9122192d4e0fe905ffdf1b4c72cff797e9f733b174e157cadd396', top='gradle-9.6.0')
        print('PASS: one patched host JNI test; direct, manifest, multi-release and JVM-option conflicts rejected')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
