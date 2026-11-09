import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest import mock

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'tools/native'))
from test_stage_artifacts import ArtifactStagingTest
import publication_plan
spec=importlib.util.spec_from_file_location('native_layer',ROOT/'source/layers/native_repository.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class NativeSourceLayerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ArtifactStagingTest('runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        f=self.fixture;f.run_stage()
        self.plan=publication_plan.plan(f.lock_path,f.output,f.verification,m.ORIGIN)
        self.metadata=(f.output/'verification-metadata.xml').read_bytes()
        self.files={
          'settings.gradle.kts':b'''dependencyResolutionManagement {
  repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
  repositories {
    mavenLocal {
      content {
        includeGroup("im.molly")
        includeGroup("org.signal")
      }
    }
  }
}
// To build libsignal from source, test
val libsignalClientPath = "external"
// Main app
include(":app")
''',
          'build.gradle.kts':b'// fixture project\n',
          'gradle/libs.versions.toml':b'''[versions]
libsignal-client = "0.106.1-collie.1"
[libraries]
libsignal-client = { module = "im.collie.native:libsignal-client", version.ref = "libsignal-client" }
libsignal-android = { module = "im.collie.native:libsignal-android", version.ref = "libsignal-client" }
signal-ringrtc = "im.collie.native:ringrtc-android:2.74.0-collie.1"
''',
          'gradle/verification-metadata.xml':f.verification.read_bytes(),
          'gradle.properties':b'# fixture\n',
          'build-inputs/native-publication.json':b'{"status":"BLOCKED"}',
          'app/build.gradle.kts':b'throw GradleException("Direct Android builds BLOCKED: fixture")\n',
          'docs/BUILD.md':b'fixture not an Android build\n'}
    def test_tracked_graph_replaces_init_and_local_native_inputs_without_opening_gate(self):
        changes=m.materialize(self.files,self.fixture.lock,self.plan,self.metadata)
        settings=changes['settings.gradle.kts'].decode();build=changes['build.gradle.kts'].decode()
        self.assertNotIn('mavenLocal',settings);self.assertNotIn('libsignalClientPath',settings)
        self.assertIn('exclusiveContent',settings);self.assertIn('ignoreGradleMetadataRedirection()',settings)
        self.assertNotIn('artifact()',settings);self.assertNotIn('app/build.gradle.kts',changes)
        self.assertEqual(changes['gradle/verification-metadata.xml'],self.metadata)
        self.assertEqual(build.count('substitute(module('),6)
        self.assertEqual(json.loads(changes['build-inputs/native-publication.json'])['publication_status'],'NOT PUBLISHED')
    def test_native_substitution_origin_or_incomplete_plan_rejected(self):
        for mutation in ('origin','binary','missing-pom'):
            plan=copy.deepcopy(self.plan)
            if mutation=='origin':plan['repository']='https://wrong.invalid/'
            if mutation=='binary':plan['files'][0]['sha256']='a'*64
            if mutation=='missing-pom':plan['files']=[f for f in plan['files'] if not f['path'].endswith('libsignal-client-0.106.1-collie.1.pom')]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):m.materialize(self.files,self.fixture.lock,plan,self.metadata)
    def test_metadata_and_gate_tamper_rejected(self):
        with self.assertRaises(ValueError):m.materialize(self.files,self.fixture.lock,self.plan,self.metadata+b'\n')
        files=dict(self.files);files['app/build.gradle.kts']=b'// removed acceptance gate\n'
        with self.assertRaises(ValueError):m.materialize(files,self.fixture.lock,self.plan,self.metadata)

    def test_cli_reads_and_updates_root_build_guide_from_reviewed_source_tree(self):
        app=self.fixture.root/'cli-app';app.mkdir()
        cli_files=dict(self.files)
        cli_files['BUILD.md']=cli_files.pop('docs/BUILD.md')
        for name,data in cli_files.items():
            path=app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
        subprocess.run(['git','-C',str(app),'init','-q'],check=True,env=env)
        subprocess.run(['git','-C',str(app),'add','-A'],check=True,env=env)
        subprocess.run(['git','-C',str(app),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','-qm','Fixture reviewed source tree'],check=True,env=env)
        tree=subprocess.check_output(['git','-C',str(app),'rev-parse','HEAD^{tree}'],text=True,env=env).strip()
        with mock.patch.object(m,'REVIEWED_G2_TREE',tree), \
             mock.patch.object(m,'verified_stage',return_value=(self.fixture.lock,self.plan,self.metadata)):
            m.main(['--app',str(app),'--engineering',str(ROOT),'--verified-stage',str(self.fixture.output)])
        self.assertIn(b'Native JVM regression readiness is separate',(app/'BUILD.md').read_bytes())
        self.assertFalse((app/'docs/BUILD.md').exists())
