"""Exercise offline native substitutions and the missing-artifact failure path."""
import functools
import hashlib
import http.server
import os
from pathlib import Path
import subprocess
import sys
import threading
from zipfile import ZipFile

from test_stage_artifacts import ArtifactStagingTest

fixture = ArtifactStagingTest('runTest')
fixture.setUp()
server = None
try:
    root = fixture.root
    upstream = root / 'upstream/test/holder/1'
    upstream.mkdir(parents=True)
    pom = upstream / 'holder-1.pom'
    modules = [('libsignal-android', '0.106.1', 'aar'), ('libsignal-client', '0.106.1', 'jar'), ('ringrtc-android', '2.74.0', 'aar')]
    dependencies = ''.join(f'<dependency><groupId>org.signal</groupId><artifactId>{m}</artifactId><version>{v}</version><type>{t}</type></dependency>' for m, v, t in modules)
    pom.write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>test</groupId><artifactId>holder</artifactId><version>1</version><packaging>pom</packaging><dependencies>' + dependencies + '</dependencies></project>')

    # This is a deliberately distinct synthetic stock artifact, cached over a
    # local HTTP Maven repository before the server is stopped. No stock binary
    # or network repository is needed for this fixture.
    stock = root / 'stock-http/org/signal/libsignal-client/0.106.1'
    stock.mkdir(parents=True)
    stock_pom = stock / 'libsignal-client-0.106.1.pom'
    stock_pom.write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>org.signal</groupId><artifactId>libsignal-client</artifactId><version>0.106.1</version></project>')
    stock_jar = stock / 'libsignal-client-0.106.1.jar'
    with ZipFile(stock_jar, 'w') as z:
        z.writestr('stock-fixture.txt', 'distinct unpatched fixture')
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = fixture.verification.read_text().replace('   </components>',
        f'      <component group="test" name="holder" version="1"><artifact name="holder-1.pom"><sha256 value="{sha(pom)}"/></artifact></component>\n'
        f'      <component group="org.signal" name="libsignal-client" version="0.106.1"><artifact name="{stock_pom.name}"><sha256 value="{sha(stock_pom)}"/></artifact><artifact name="{stock_jar.name}"><sha256 value="{sha(stock_jar)}"/></artifact></component>\n'
        '   </components>')
    fixture.verification.write_text(metadata)
    fixture.run_stage()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root / 'stock-http'))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = root / 'project'
    (project / 'gradle').mkdir(parents=True)
    (project / 'gradle/verification-metadata.xml').write_bytes((fixture.output / 'verification-metadata.xml').read_bytes())
    local_url = (root / 'upstream').as_uri()
    (project / 'settings.gradle').write_text(f"dependencyResolutionManagement {{ repositories {{ maven {{ url = uri('{local_url}') }}; maven {{ url = uri('http://127.0.0.1:{server.server_port}'); allowInsecureProtocol = true }} }} }}\nrootProject.name='smoke'\n")
    (project / 'build.gradle').write_text('''configurations { signal; molly; transitive; stock }
dependencies {
 signal 'org.signal:libsignal-android:0.106.1@aar'
 signal 'org.signal:libsignal-client:0.106.1@jar'
 signal 'org.signal:ringrtc-android:2.74.0@aar'
 molly 'im.molly:libsignal-android:0.105.4-1@aar'
 molly 'im.molly:libsignal-client:0.105.4-1@jar'
 molly 'im.molly:ringrtc-android:2.73.0-1@aar'
 transitive 'test:holder:1'
 stock 'org.signal:libsignal-client:0.106.1'
}
tasks.register('primeStock') { doLast { assert configurations.stock.files.size() == 1 } }
tasks.register('checkNative') { doLast {
 [configurations.signal, configurations.molly, configurations.transitive].each { c ->
   def nativeArtifacts = c.resolvedConfiguration.resolvedArtifacts.findAll { it.moduleVersion.id.group != 'test' }
   assert nativeArtifacts.size() == 3
   assert nativeArtifacts.every { it.moduleVersion.id.group == 'im.collie.native' }
   nativeArtifacts.each { assert it.file.isFile() }
   println(c.name + ': ' + nativeArtifacts.collect { it.moduleVersion.id.toString() }.sort())
 }
} }
''')
    gradle = os.environ.get('COLLIE_NATIVE_TEST_GRADLE')
    if not gradle or not Path(gradle).is_file():
        raise SystemExit('Set COLLIE_NATIVE_TEST_GRADLE to a verified Gradle executable')
    env = os.environ.copy()
    env['GRADLE_USER_HOME'] = str(root / 'gradle-home')
    base = [gradle, '--project-dir', str(project), '--no-daemon', '--dependency-verification=strict']
    subprocess.run(base + ['primeStock'], cwd=project, env=env, check=True)
    server.shutdown()
    server.server_close()
    server = None
    offline = base + ['--offline', '--init-script', str(fixture.output / 'native.init.gradle')]
    subprocess.run(offline + ['checkNative'], cwd=project, env=env, check=True)
    collie_client = fixture.output / 'maven/im/collie/native/libsignal-client/0.106.1-collie.1/libsignal-client-0.106.1-collie.1.jar'
    collie_client.unlink()
    failed = subprocess.run(offline + ['checkNative'], cwd=project, env=env, capture_output=True, text=True)
    if failed.returncode == 0 or 'im.collie.native:libsignal-client:0.106.1-collie.1' not in (failed.stdout + failed.stderr):
        print('negative return code:', failed.returncode, file=sys.stderr)
        print((failed.stdout + failed.stderr)[-2500:], file=sys.stderr)
        raise SystemExit('Missing Collie artifact did not fail as expected with synthetic stock cached')
    print('PASS: missing Collie client fails even with a cached synthetic stock client')
finally:
    if server is not None:
        server.shutdown()
        server.server_close()
    fixture.tearDown()
