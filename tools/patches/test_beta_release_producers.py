"""Parser-backed Beta collect/sign/publish coverage with test-only transports."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
import migration_receipts as receipts
import release_artifacts as artifacts
import release_collect
import release_publish
import release_sign
import runtime_receipts as runtime
import stage_preparation
import version_policy


class BetaProducerTests(unittest.TestCase):
    channel='trailblazer-beta'
    package='im.collie.app.trailblazer'
    certificate='a515c03498bce8852b835544910b09ccc11a3abbed23e826e9c62c002680fe98'
    version='9.99.1-2-fixture'
    version_code=999001
    run_id='918273645'
    run_attempt='2'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.checkout=self.root/'source-checkout';self.checkout.mkdir()
        subprocess.run(['git','init','-q',str(self.checkout)],check=True)
        subprocess.run(['git','-C',str(self.checkout),'config','user.name','Fixture'],check=True)
        subprocess.run(['git','-C',str(self.checkout),'config','user.email','fixture@example.invalid'],check=True)
        (self.checkout/'gradle.properties').write_text('ordinary=fixture\n')
        pending=self.checkout/'app/src/main/assets/collie-log/unreleased/12.txt'
        pending.parent.mkdir(parents=True);pending.write_text('topic: fixes\npr: 12\nFixes a fixture issue.\n')
        subprocess.run(['git','-C',str(self.checkout),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.checkout),'commit','-qm','fixture baseline'],check=True)
        self.baseline=subprocess.check_output(['git','-C',str(self.checkout),'rev-parse','HEAD'],text=True).strip()
        baseline_time=subprocess.check_output(['git','-C',str(self.checkout),'show','-s','--format=%ct','HEAD'],text=True).strip()
        (self.checkout/'gradle.properties').write_text(
            f'ordinary=fixture\ncollieBuildRevision={self.baseline}\ncollieBuildTimestamp={baseline_time}\n'
            f'releaseVersionName={self.version}\nreleaseVersionCode={self.version_code}\n')
        pending.unlink()
        log=self.checkout/f'app/src/main/assets/collie-log/releases/{self.version}.txt'
        log.parent.mkdir(parents=True)
        log.write_text(f'version: {self.version}\ndate: 2026-11-17\n\ntopic: fixes\npr: 12\nFixes a fixture issue.\n')
        edition='trailblazer-beta' if self.channel=='trailblazer-beta' else 'android'
        changelog=self.checkout/f'fastlane/metadata/{edition}/en-US/changelogs/{self.version_code}.txt'
        changelog.parent.mkdir(parents=True);changelog.write_bytes(b'- Fixes a fixture issue.\n')
        subprocess.run(['git','-C',str(self.checkout),'add','-A'],check=True)
        subprocess.run(['git','-C',str(self.checkout),'commit','-qm','prepared fixture source'],check=True)
        subprocess.run(['git','-C',str(self.checkout),'branch','-M','trailblazer-beta'],check=True)
        self.commit=subprocess.check_output(['git','-C',str(self.checkout),'rev-parse','HEAD'],text=True).strip()
        self.tree=subprocess.check_output(['git','-C',str(self.checkout),'rev-parse','HEAD^{tree}'],text=True).strip()
        self.engineering='b'*40;self.workflow='c'*40
        self.source={
            'schema':2,'kind':'source','payload':{
                'upstream_commit':'1'*40,'engineering_revision':self.engineering,
                'generator_revision':'2'*40,'verifier_revision':'3'*40,'layers':[],
                'reconstructed_tree':'4'*40,'overlays':[],'published_commit':self.commit,
                'published_tree':self.tree},
            'evidence':[{'status':'PASS','description':'Synthetic Beta source fixture','sha256':'5'*64}]}
        self.source_path=self.root/'source-receipt.json';self.write_json(self.source_path,self.source)
        self.source_sha=receipts.digest(self.source,profile=self.channel)
        self.config={'android':{'build_tools':'36.0.0'}}
        self.config_path=self.root/'build-config.json';self.write_json(self.config_path,self.config)
        self.toolchain_path=self.root/'toolchain.bin';self.toolchain_path.write_bytes(b'test toolchain')
        self.dependencies_path=self.root/'dependencies.lock';self.dependencies_path.write_bytes(b'test dependency pins')
        self.native_path=self.root/'native-inputs.json';self.native_path.write_bytes(b'test native input manifest')
        self.report_path=self.root/'unit-report.json';self.report_path.write_bytes(b'{"result":"passed"}\n')
        self.test_reports_path=self.root/'test-reports.json'
        self.write_json(self.test_reports_path,{'schema':1,'reports':[{
            'name':'unit-tests','status':'PASS','description':'Pinned unit test fixture',
            'file':self.report_path.name,'sha256':artifacts.sha256(self.report_path)}]})
        self.build_inputs={
            'schema':1,'recipe_revision':'6'*40,
            'config_sha256':artifacts.sha256(self.config_path),
            'toolchain_sha256':artifacts.sha256(self.toolchain_path),
            'dependencies_sha256':artifacts.sha256(self.dependencies_path),
            'native_inputs_sha256':artifacts.sha256(self.native_path),
            'trust_level':'test-only producer fixture'}
        self.build_inputs_path=self.root/'build-inputs.json';self.write_json(self.build_inputs_path,self.build_inputs)
        self.release_version='9.99.0-fixture'
        self.inventory_path=self.root/'beta-published-inventory.json'
        self.write_json(self.inventory_path,[{'release':self.release_version,'code':999000,
            'sha256':'a'*64,'package':self.package}])
        self.ledger_path=self.root/'beta-version-ledger.json'
        self.plan={
            'schema':1,'kind':'release-plan','mode':'new-source','purpose':'production',
            'source_commit':self.commit,'engineering_revision':self.engineering,
            'workflow_revision':self.workflow,'package':self.package,
            'version_code':self.version_code,'version_name':self.version,
            'certificate_sha256':self.certificate,'source_receipt_sha256':self.source_sha,
            'build_inputs_sha256':artifacts.sha256(self.build_inputs_path),
            'required_test_reports':['unit-tests'],'channel':self.channel,
            'tag':'trailblazer-v'+self.version,'title':'Collie TrailBlazer Beta '+self.version,
            'prerelease':True}
        self.plan_path=self.root/'release-plan.json';self.write_json(self.plan_path,self.plan)
        self.plan_sha=artifacts.sha256(self.plan_path)
        self.build_tools=self.make_test_tools()
        self.apks=self.root/'unsigned-apks';self.apks.mkdir()
        self.apk_path=self.apks/'trailblazer-beta.apk'
        import zipfile
        with zipfile.ZipFile(self.apk_path,'w') as apk:apk.writestr('AndroidManifest.xml',b'test only')
        self.run_metadata={
            'id':int(self.run_id),'run_attempt':int(self.run_attempt),
            'head_repository':{'full_name':'0cwa/collieim-android'},
            'head_branch':'trailblazer-beta','head_sha':self.commit,
            'path':runtime.BETA_BUILD_WORKFLOW,'workflow_id':runtime.BETA_BUILD_WORKFLOW_ID,
            'referenced_workflows':[],
            'status':'completed','conclusion':'success','event':'workflow_dispatch'}
        self.beta_run_context=patch.object(runtime,'github_run_metadata',return_value=json.dumps(self.run_metadata))
        self.beta_run_context.start();self.addCleanup(self.beta_run_context.stop)
        self.manifest_path=self.apks/'manifest.json'

    def write_json(self,path,value):
        path.write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n')

    def make_script(self,path,body):
        path.write_text('#!/usr/bin/env python3\n'+textwrap.dedent(body))
        path.chmod(0o755)

    def make_test_tools(self):
        tools=self.root/'build-tools'/'36.0.0';tools.mkdir(parents=True)
        self.make_script(tools/'aapt2',f"""
            print(\"package: name='{self.package}' versionCode='{self.version_code}' versionName='{self.version}'\")
        """)
        self.make_script(tools/'zipalign',"""
            import pathlib,sys,shutil
            args=sys.argv[1:]
            if '-c' not in args: shutil.copyfile(args[-2],args[-1])
        """)
        self.make_script(tools/'apksigner',f"""
            import pathlib,sys,shutil
            args=sys.argv[1:]
            if args[0]=='sign':
                out=pathlib.Path(args[args.index('--out')+1])
                shutil.copyfile(args[-1],out)
                out.write_bytes(out.read_bytes()+b'\\nTEST-ONLY-SIGNATURE\\n')
            else:
                print('Verified using v2 scheme (APK Signature Scheme v2): true')
                print('Verified using v3 scheme (APK Signature Scheme v3): true')
                print('Signer #1 certificate SHA-256 digest: {self.certificate}')
        """)
        return tools

    def common_plan_args(self):
        return ['--channel',self.channel,'--release-plan',str(self.plan_path),
                '--approved-release-plan-sha256',self.plan_sha]

    def collect(self,*,expected=0,baseline=None):
        args=['release_collect','--directory',str(self.apks),'--build-tools',str(self.build_tools),
              '--config',str(self.config_path),'--commit',self.commit,'--workflow-revision',self.workflow,
              '--run-id',self.run_id,'--run-attempt',self.run_attempt,'--version-name',self.version,
              '--version-code',str(self.version_code),'--certificate-sha256',self.certificate,
              '--tag',self.plan['tag'],'--title',self.plan['title'],
              '--source-receipt',str(self.source_path),'--build-inputs',str(self.build_inputs_path),
              '--source-checkout',str(self.checkout),'--toolchain-input',str(self.toolchain_path),
              '--preparation-baseline-commit',baseline or self.baseline,
              '--dependency-input',str(self.dependencies_path),'--native-input',str(self.native_path),
              '--test-reports',str(self.test_reports_path),*self.common_plan_args()]
        errors=io.StringIO()
        with patch('sys.argv',args),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            result=release_collect.main()
        self.assertEqual(result,expected,errors.getvalue())
        if expected:return None
        data=json.loads(self.manifest_path.read_text())
        self.assertEqual(data['package'],self.package)
        self.assertEqual(data['apks'][0]['file'],'trailblazer-beta.apk')
        self.assertEqual(data['tag'],self.plan['tag'])
        self.assertEqual(data['title'],self.plan['title'])
        self.assertIs(data['prerelease'],True)
        self.assertEqual(data['source_commit'],self.commit)
        self.assertEqual(data['run_id'],self.run_id)
        receipts.load(self.apks/'build-receipt.json','build',profile=self.channel)
        return data

    def sign(self,*,beta_keys=True,expected=0):
        checklist=self.root/'instrumentation.json'
        self.write_json(checklist,{'commit':self.commit,'status':'not run','evidence':'',
                                   'reason':'Test-only release pipeline fixture'})
        output=self.root/'signed';scratch=self.root/'sign-scratch';scratch.mkdir()
        args=['release_sign','--channel',self.channel,'--manifest',str(self.manifest_path),
              '--manifest-sha256',artifacts.sha256(self.manifest_path),'--approved-commit',self.commit,
              '--approved-run',self.run_id,'--certificate-sha256',self.certificate,
              '--apks',str(self.apks),'--output',str(output),'--build-tools',str(self.build_tools),
              '--scratch',str(scratch),'--instrumentation-checklist',str(checklist),
              '--source-receipt',str(self.source_path),'--build-inputs',str(self.build_inputs_path),
              '--source-checkout',str(self.checkout),'--toolchain-input',str(self.toolchain_path),
              '--preparation',str(self.apks/'preparation.json'),
              '--dependency-input',str(self.dependencies_path),'--native-input',str(self.native_path),
              '--build-receipt',str(self.apks/'build-receipt.json'),
              '--approved-build-receipt-sha256',receipts.digest(
                  receipts.load(self.apks/'build-receipt.json','build',profile=self.channel),profile=self.channel),
              '--version-ledger',str(self.ledger_path),'--published-inventory',str(self.inventory_path),
              '--published-inventory-sha256',artifacts.sha256(self.inventory_path),*self.common_plan_args()]
        env=({'TRAILBLAZER_BETA_KEYSTORE':str(self.root/'NO-REAL-KEY'),
              'TRAILBLAZER_BETA_KEY_ALIAS':'test-only',
              'TRAILBLAZER_BETA_KEYSTORE_PASSWORD':'test-only-never-a-secret',
              'TRAILBLAZER_BETA_KEY_PASSWORD':'test-only-never-a-secret'} if beta_keys else
             {'MOLLY_KEYSTORE':str(self.root/'STABLE-KEY-MUST-NOT-BE-USED'),
              'MOLLY_KEY_ALIAS':'stable-must-not-be-used'})
        errors=io.StringIO()
        with patch('sys.argv',args),patch.dict(os.environ,env,clear=False),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            if not beta_keys:
                for key in ('TRAILBLAZER_BETA_KEYSTORE','TRAILBLAZER_BETA_KEY_ALIAS',
                            'TRAILBLAZER_BETA_KEYSTORE_PASSWORD','TRAILBLAZER_BETA_KEY_PASSWORD'):
                    os.environ.pop(key,None)
            result=release_sign.main()
        self.assertEqual(result,expected,errors.getvalue())
        if expected:return None
        final_path=output/'final-manifest.json'
        final=json.loads(final_path.read_text())
        self.assertEqual(final['package'],self.package)
        self.assertEqual(final['apks'][0]['file'],'trailblazer-beta.apk')
        self.assertEqual(final['apks'][0]['certificate_sha256'],self.certificate)
        release_receipt=receipts.load(output/'release-receipt.json','release',profile=self.channel)
        return output,final_path,final,release_receipt

    def publish_dry_run(self,output,final_path,final,release_receipt,*,tag=None,title=None,prerelease=True,expected=0,approved_run=None,certificate=None):
        notes=self.root/'notes.md';notes.write_text('Test-only Beta producer notes\n')
        scratch=self.root/'publish-scratch';scratch.mkdir(exist_ok=True)
        plan_digest=receipts.digest(release_receipt,profile=self.channel)
        args=['release_publish','draft','--channel',self.channel,'--repo','0cwa/collieim-android',
              '--tag',tag or self.plan['tag'],'--title',title or self.plan['title'],'--approved-commit',self.commit,
              '--approved-run',approved_run or self.run_id,'--certificate-sha256',certificate or self.certificate,
              '--final-manifest',str(final_path),'--final-manifest-sha256',artifacts.sha256(final_path),
              '--provenance',str(self.manifest_path),'--notes',str(notes),'--scratch',str(scratch),
              '--release-receipt',str(output/'release-receipt.json'),
              '--approved-release-receipt-sha256',plan_digest,
              '--source-receipt',str(self.source_path),'--build-inputs',str(self.build_inputs_path),
              '--source-checkout',str(self.checkout),'--toolchain-input',str(self.toolchain_path),
              '--preparation',str(self.apks/'preparation.json'),
              '--dependency-input',str(self.dependencies_path),'--native-input',str(self.native_path),
              '--build-receipt',str(self.apks/'build-receipt.json'),
              '--approved-build-receipt-sha256',receipts.digest(
                  receipts.load(self.apks/'build-receipt.json','build',profile=self.channel),profile=self.channel),
              '--dry-run','--signing-key',str(self.root/'NO-REAL-TAG-KEY'),
              '--tagger-name','Test Fixture','--tagger-email','fixture@example.invalid',
              *self.common_plan_args()]
        if prerelease:args.append('--prerelease')
        errors=io.StringIO()
        with patch('sys.argv',args),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            result=release_publish.main()
        self.assertEqual(result,expected,errors.getvalue())
        return result

    def publish_mocked_draft(self,output,final_path,final,release_receipt):
        notes=self.root/'draft-notes.md';notes.write_text('Test-only Beta producer notes\n')
        scratch=self.root/'publish-scratch';scratch.mkdir(exist_ok=True)
        args=['release_publish','draft','--channel',self.channel,'--repo','0cwa/collieim-android',
              '--tag',self.plan['tag'],'--title',self.plan['title'],'--approved-commit',self.commit,
              '--approved-run',self.run_id,'--certificate-sha256',self.certificate,
              '--final-manifest',str(final_path),'--final-manifest-sha256',artifacts.sha256(final_path),
              '--provenance',str(self.manifest_path),'--notes',str(notes),'--scratch',str(scratch),
              '--release-receipt',str(output/'release-receipt.json'),
              '--approved-release-receipt-sha256',receipts.digest(release_receipt,profile=self.channel),
              '--source-receipt',str(self.source_path),'--build-inputs',str(self.build_inputs_path),
              '--source-checkout',str(self.checkout),'--toolchain-input',str(self.toolchain_path),
              '--preparation',str(self.apks/'preparation.json'),
              '--dependency-input',str(self.dependencies_path),'--native-input',str(self.native_path),
              '--build-receipt',str(self.apks/'build-receipt.json'),
              '--approved-build-receipt-sha256',receipts.digest(
                  receipts.load(self.apks/'build-receipt.json','build',profile=self.channel),profile=self.channel),
              '--version-ledger',str(self.ledger_path),'--published-inventory',str(self.inventory_path),
              '--published-inventory-sha256',artifacts.sha256(self.inventory_path),
              '--prerelease','--signing-key',str(self.root/'NO-REAL-TAG-KEY'),
              '--tagger-name','Test Fixture','--tagger-email','fixture@example.invalid',
              *self.common_plan_args()]
        remote={}
        def github(command,*unused,**kwargs):
            if command[1:3]==['release','view']:
                if not remote:
                    raise subprocess.CalledProcessError(1,command,stderr='release not found')
                assets=[{'id':'test-asset-'+str(index),'name':name,'size':len(data),
                         'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
                        for index,(name,data) in enumerate(sorted(remote['bytes'].items()))]
                return types.SimpleNamespace(stdout=json.dumps({
                    'name':remote['title'],'tagName':remote['tag'],'isDraft':True,
                    'isPrerelease':True,'assets':assets}))
            if command[1:3]==['release','create']:
                self.assertEqual(command[2],'create')
                self.assertEqual(command[command.index('--title')+1],self.plan['title'])
                self.assertIn('--prerelease',command)
                self.assertIn('--latest=false',command)
                notes_index=command.index('--notes-file')
                paths=[value for value in command[notes_index+2:] if isinstance(value,Path)]
                remote.update(tag=command[3],title=command[command.index('--title')+1],
                              bytes={path.name:path.read_bytes() for path in paths})
                self.assertIn('trailblazer-beta.apk',remote['bytes'])
                self.assertNotIn('collie.apk',remote['bytes'])
                return types.SimpleNamespace(stdout='')
            raise AssertionError('unexpected GitHub transport command: '+repr(command))
        errors=io.StringIO()
        with patch('sys.argv',args),patch.object(release_publish,'command',side_effect=github),\
                patch.object(release_publish,'ensure_signed_tag') as tag,\
                patch.object(release_publish,'verify_remote_tag'),\
                contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            result=release_publish.main()
        self.assertEqual(result,0,errors.getvalue())
        tag.assert_called_once()
        self.assertEqual(remote['tag'],self.plan['tag'])
        self.assertEqual(remote['title'],self.plan['title'])
        return remote

    def test_ci_metadata_is_authoritative_and_exact(self):
        runtime.verify_beta_ci_run(self.run_id,self.run_attempt,self.commit)
        mutations=[
            {'head_repository':{'full_name':'attacker/app'}},
            {'path':'.github/workflows/other.yml'},
            {'workflow_id':runtime.BETA_BUILD_WORKFLOW_ID+1},
            {'head_branch':'main'}, {'head_sha':'d'*40},
            {'conclusion':'failure'}, {'status':'in_progress'},
            {'event':'pull_request'}, {'run_attempt':int(self.run_attempt)+1},
            {'referenced_workflows':[{'path':'other/repo/.github/workflows/called.yml@main'}]},
            {'referenced_workflows':None}, {'referenced_workflows':{}},
        ]
        for change in mutations:
            with self.subTest(change=change):
                run=dict(self.run_metadata);run.update(change)
                with self.assertRaisesRegex(ValueError,'authoritative Beta source CI run'):
                    runtime.verify_beta_ci_run(self.run_id,self.run_attempt,self.commit,
                                               transport=lambda _endpoint:run)
        missing=dict(self.run_metadata);missing.pop('referenced_workflows')
        with self.assertRaisesRegex(ValueError,'authoritative Beta source CI run'):
            runtime.verify_beta_ci_run(self.run_id,self.run_attempt,self.commit,
                                       transport=lambda _endpoint:missing)

    def test_stale_unbound_beta_hold_cannot_sign_below_new_published_floor(self):
        version_policy.hold(self.ledger_path,self.version,self.version_code,
                            self.inventory_path,artifacts.sha256(self.inventory_path),channel=self.channel)
        before=self.ledger_path.read_bytes()
        published=[{'release':'newer-beta','code':self.version_code+100,
                    'sha256':'c'*64,'package':self.package}]
        with self.assertRaisesRegex(ValueError,'version must exceed'):
            version_policy.reserve(self.ledger_path,self.version,self.version_code,'b'*64,
                                   published,channel=self.channel)
        self.assertEqual(self.ledger_path.read_bytes(),before)

    def test_first_beta_requires_explicit_approved_package_empty_history(self):
        empty={'schema':1,'package':self.package,'releases':[]}
        self.write_json(self.inventory_path,empty)
        approved=artifacts.sha256(self.inventory_path)
        version_policy.hold(self.ledger_path,self.version,self.version_code,
                            self.inventory_path,approved,channel=self.channel)
        published=version_policy._read_inventory(self.inventory_path,approved,self.channel)
        reserved=version_policy.reserve(self.ledger_path,self.version,self.version_code,
                                        'b'*64,published,channel=self.channel)
        self.assertEqual(reserved['package'],self.package)
        self.assertEqual(reserved['code'],self.version_code)
        # Existing bound artifact retries remain permitted after a newer release.
        newer=[{'release':'newer-beta','code':self.version_code+100,
                'sha256':'c'*64,'package':self.package}]
        self.assertEqual(version_policy.reserve(self.ledger_path,self.version,self.version_code,
                                                'b'*64,newer,channel=self.channel),reserved)
        for value in ([],dict(empty,package='im.collie.app'),dict(empty,schema=True)):
            with self.subTest(value=value):
                self.write_json(self.inventory_path,value)
                with self.assertRaises(ValueError):
                    version_policy._read_inventory(self.inventory_path,
                                                   artifacts.sha256(self.inventory_path),self.channel)
        self.write_json(self.inventory_path,empty)
        with self.assertRaises(ValueError):
            version_policy._read_inventory(self.inventory_path,artifacts.sha256(self.inventory_path),'stable')
        with self.assertRaisesRegex(ValueError,'digest differs'):
            version_policy._read_inventory(self.inventory_path,'f'*64,self.channel)

    def test_plan_code_mismatch_reaches_neither_reservation_nor_signing(self):
        self.collect()
        manifest=json.loads(self.manifest_path.read_text())
        manifest['version_code']+=1
        self.write_json(self.manifest_path,manifest)
        with patch.object(version_policy,'enforce') as reserve,patch.object(release_sign,'run') as sign:
            self.sign(expected=1)
        reserve.assert_not_called()
        sign.assert_not_called()

    def test_wrong_beta_draft_title_is_rejected_before_upload(self):
        args=types.SimpleNamespace(repo='0cwa/collieim-android',tag=self.plan['tag'],
                                   title=self.plan['title'],approved_commit=self.commit,
                                   channel=self.channel,prerelease=True)
        apk=self.root/'missing-remote.apk';apk.write_bytes(b'candidate')
        draft={'isDraft':True,'isPrerelease':True,'tagName':self.plan['tag'],
               'name':'Wrong title','assets':[]}
        with patch.object(release_publish,'verify_remote_tag'),\
                patch.object(release_publish,'find_release',return_value=draft),\
                patch.object(release_publish,'command') as upload:
            with self.assertRaisesRegex(ValueError,'tag or title'):
                release_publish.resume_draft_assets(args,[apk],{apk.name:artifacts.sha256(apk)},
                                                   self.root,release_publish.PhaseRecorder(None))
        upload.assert_not_called()

    def test_publisher_binds_advertised_run_and_certificate_options(self):
        self.collect()
        output,final_path,final,release_receipt=self.sign()
        with patch.object(release_publish,'command') as github:
            self.publish_dry_run(output,final_path,final,release_receipt,
                                 approved_run=str(int(self.run_id)+1),expected=1)
            self.publish_dry_run(output,final_path,final,release_receipt,
                                 certificate='f'*64,expected=1)
        github.assert_not_called()

    def test_publisher_rejects_unsigned_engineering_and_workflow_substitution(self):
        self.collect()
        output,final_path,final,release_receipt=self.sign()
        unsigned=json.loads(self.manifest_path.read_text())
        build=receipts.load(self.apks/'build-receipt.json','build',profile=self.channel)
        pinned=runtime.verify_preparation_verifier_pin()
        self.assertEqual(pinned.VERIFIER_ID,'collie-preparation-verifier/v1')
        with patch.object(runtime,'PREPARATION_VERIFIER_SHA256','0'*64),self.assertRaisesRegex(ValueError,'configured immutable pin'):
            runtime.verify_preparation_verifier_pin()
        for field in ('engineering_revision','workflow_revision'):
            with self.subTest(field=field):
                changed=dict(unsigned);changed[field]='e'*40
                self.write_json(self.manifest_path,changed)
                updated=dict(final,unsigned_manifest_sha256=artifacts.sha256(self.manifest_path))
                self.write_json(final_path,updated)
                receipt=runtime.release((self.plan,self.plan_sha),self.source,build,
                                        updated,final_path.read_bytes())
                self.write_json(output/'release-receipt.json',receipt)
                self.publish_dry_run(output,final_path,updated,receipt,expected=1)

    def test_verifier_pin_rejects_malicious_source_before_execution_and_ignores_import_cache(self):
        marker=self.root/'malicious-verifier-ran'
        malicious=self.root/'preparation_verifier.py'
        malicious.write_text("from pathlib import Path\nPath("+repr(str(marker))+ ").write_text('executed')\nVERIFIER_ID='bad'\n")
        py_compile.compile(str(malicious),doraise=True)
        poisoned=types.ModuleType('preparation_verifier')
        poisoned.__file__=str(malicious)
        poisoned.VERIFIER_ID='poisoned-cache'
        with patch.object(runtime,'PREPARATION_VERIFIER_PATH',malicious),patch.dict(sys.modules,{'preparation_verifier':poisoned}):
            with self.assertRaisesRegex(ValueError,'configured immutable pin'):
                runtime.verify_preparation_verifier_pin()
        self.assertFalse(marker.exists())

    def test_collector_rejects_untrusted_output_location_before_receipt_verification(self):
        with self.assertRaisesRegex(ValueError,'outside the source checkout'):
            runtime.trusted_directories.open_trusted_directory(self.checkout,forbidden_tree=self.checkout)
        self.assertFalse((self.checkout/'preparation.json').exists())

    def test_collector_writes_to_open_directory_after_parent_path_retarget(self):
        original=self.apks
        moved=self.root/'original-collector-output';attacker=self.root/'attacker-output'
        attacker.mkdir(mode=0o700)
        opener=runtime.trusted_directories.open_trusted_directory
        def open_then_retarget(path,**kwargs):
            descriptor,absolute=opener(path,**kwargs)
            original.rename(moved)
            original.symlink_to(attacker, target_is_directory=True)
            self.apks=moved
            self.manifest_path=moved/'manifest.json'
            return descriptor,absolute
        with patch.object(runtime.trusted_directories,'open_trusted_directory',side_effect=open_then_retarget):
            self.collect()
        self.assertTrue((moved/'preparation.json').is_file())
        self.assertTrue((moved/'build-receipt.json').is_file())
        self.assertTrue((moved/'manifest.json').is_file())
        self.assertEqual(list(attacker.iterdir()),[])

    def test_stage_rejects_group_writable_root_and_descendant(self):
        self.collect()
        output=self.root/'release-output';output.mkdir()
        args=types.SimpleNamespace(channel=self.channel,release_plan=self.plan_path,
            approved_release_plan_sha256=self.plan_sha,source_receipt=self.source_path,
            build_receipt=self.apks/'build-receipt.json',
            approved_build_receipt_sha256=receipts.digest(
                receipts.load(self.apks/'build-receipt.json','build',profile=self.channel),profile=self.channel),
            preparation=self.apks/'preparation.json',release_id=123456789,bundle_root=output,
            commit=self.commit,approved_commit=self.commit,workflow_revision=self.workflow,
            engineering_revision=self.engineering,version_name=self.version,version_code=self.version_code,
            certificate_sha256=self.certificate)
        output.chmod(0o770)
        with self.assertRaisesRegex(ValueError,'trusted existing directory'):
            stage_preparation.stage(args)
        output.chmod(0o700)
        bundles=output/'bundles';bundles.mkdir();bundles.chmod(0o770)
        with self.assertRaisesRegex(ValueError,'group/other writable'):
            stage_preparation.stage(args)

    def test_beta_plan_rejects_stable_package_certificate_and_channel(self):
        args=type('Args',(),{})()
        for field,value in (('package','im.collie.app'),('certificate_sha256','f'*64),('channel','stable'),('mode','legacy')):
            plan=dict(self.plan);plan[field]=value
            path=self.root/'bad-plan.json';self.write_json(path,plan)
            args.release_plan=path;args.approved_release_plan_sha256=artifacts.sha256(path)
            args.channel=self.channel;args.commit=self.commit;args.approved_commit=None
            args.workflow_revision=self.workflow;args.engineering_revision=self.engineering
            args.version_code=self.version_code;args.version_name=self.version
            args.certificate_sha256=self.certificate;args.tag=self.plan['tag'];args.title=self.plan['title'];args.repo=''
            with self.subTest(field=field),self.assertRaises(ValueError):runtime.plan(args)
        for version in ('8.35.2-1-port-unvalidated','9.1.0-dev'):
            with self.subTest(version=version),self.assertRaisesRegex(ValueError,'development or unvalidated'):
                artifacts.validate_beta_identity(version,self.version_code,'trailblazer-v'+version,
                                                  'Collie TrailBlazer Beta '+version,True)

    def test_beta_version_inventory_is_package_scoped_and_monotonic(self):
        manifest={'version_code':999000,'version_name':self.version,
                  '_unsigned_manifest_sha256':'b'*64}
        with self.assertRaisesRegex(ValueError,'version must exceed|release identity/code'):
            version_policy.enforce(manifest,self.root/'rollback-ledger.json',self.inventory_path,
                                   artifacts.sha256(self.inventory_path),channel=self.channel)
        manifest['version_code']=self.version_code
        shared_ledger=self.root/'shared-ledger.json'
        self.write_json(shared_ledger,[{'release':'v8.35.2','code':self.version_code,'sha256':'c'*64}])
        accepted=version_policy.enforce(manifest,shared_ledger,self.inventory_path,
                                        artifacts.sha256(self.inventory_path),channel=self.channel)
        self.assertEqual(accepted['code'],self.version_code)
        self.assertEqual(len(json.loads(shared_ledger.read_text())),2)

    def test_real_collect_sign_and_existing_receipt_validator_accept_beta_graph(self):
        source_bytes=self.source_path.read_bytes()
        self.collect()
        for name in ('preparation.json','build-receipt.json','manifest.json'):
            with self.subTest(private_collection_output=name):
                self.assertEqual((self.apks/name).stat().st_mode&0o777,0o600)
        preparation,preparation_sha=runtime.preparation_document(self.apks/'preparation.json')
        self.assertEqual(preparation['baseline_commit'],self.baseline)
        self.assertEqual(preparation['final_commit'],self.commit)
        build=receipts.load(self.apks/'build-receipt.json','build',profile=self.channel)
        markers=[entry for entry in build['evidence'] if entry['description']==runtime.PREPARATION_EVIDENCE]
        self.assertEqual(markers,[{'status':'PASS','description':runtime.PREPARATION_EVIDENCE,'sha256':preparation_sha}])
        self.assertEqual(self.source_path.read_bytes(),source_bytes)
        self.assertEqual(runtime.verify_preparation_record(preparation,(self.plan,self.plan_sha),self.source,build),preparation_sha)
        for field,value in (('final_tree','e'*40),
                            ('release_plan_sha256','d'*64),('channel','stable'),
                            ('version_code',self.version_code+1)):
            changed=dict(preparation);changed[field]=value
            with self.subTest(preparation_field=field),self.assertRaises(ValueError):
                runtime.verify_preparation_record(changed,(self.plan,self.plan_sha),self.source,build)
        changed=dict(preparation,baseline_commit='f'*40)
        self.assertNotEqual(runtime.verify_preparation_record(changed,(self.plan,self.plan_sha),self.source,build),
                            hashlib.sha256((json.dumps(changed,sort_keys=True,indent=2)+'\n').encode()).hexdigest())
        changed_build=dict(build,evidence=[entry for entry in build['evidence']
            if entry['description']!=runtime.PREPARATION_EVIDENCE])
        with self.assertRaisesRegex(ValueError,'exactly one'):
            runtime.verify_preparation_record(preparation,(self.plan,self.plan_sha),self.source,changed_build)
        output,final_path,final,release_receipt=self.sign()
        self.assertEqual(final['tag'],'trailblazer-v'+self.version)
        self.assertEqual(final['title'],'Collie TrailBlazer Beta '+self.version)
        self.assertIs(final['prerelease'],True)
        self.assertEqual(release_receipt['payload']['aliases'],['trailblazer-beta.apk'])
        remote=self.publish_mocked_draft(output,final_path,final,release_receipt)
        self.assertIn('source.json',remote['bytes'])
        self.assertIn('build.json',remote['bytes'])
        self.assertIn('release.json',remote['bytes'])
        ledger=json.loads(self.ledger_path.read_text())
        self.assertEqual(ledger[0]['package'],self.package)
        self.assertEqual(ledger[0]['code'],self.version_code)

    def test_preparation_companion_rejects_changed_bytes_and_invalid_markers(self):
        self.collect()
        path=self.apks/'preparation.json'
        original=path.read_bytes()
        build=receipts.load(self.apks/'build-receipt.json','build',profile=self.channel)
        def verify(value=build):
            return runtime.verify_preparation_companion(path,channel=self.channel,
                version_name=self.version,version_code=self.version_code,source=self.source,build=value)
        changed=json.loads(original)
        changed['baseline_commit']='f'*40
        path.write_text(json.dumps(changed,sort_keys=True,indent=2)+'\n')
        with self.assertRaisesRegex(ValueError,'bytes differ'):
            verify()
        path.write_bytes(original)
        for description in (runtime.PREPARATION_EVIDENCE,runtime.PLAN_EVIDENCE):
            for mutation in ('missing','duplicate','nonpassing'):
                candidate=json.loads(json.dumps(build))
                marker=next(e for e in candidate['evidence'] if e['description']==description)
                if mutation=='missing':candidate['evidence'].remove(marker)
                elif mutation=='duplicate':candidate['evidence'].append(dict(marker))
                else:marker['status']='NOT RUN'
                with self.subTest(description=description,mutation=mutation),self.assertRaises(ValueError):
                    verify(candidate)

    def test_collector_rejects_wrong_preparation_baseline_before_emitting_build_evidence(self):
        self.collect(expected=1,baseline=self.commit)
        self.assertFalse((self.apks/'preparation.json').exists())
        self.assertFalse((self.apks/'build-receipt.json').exists())

    def test_stable_fixture_collects_and_signs_without_production_preparation(self):
        plan={k:v for k,v in self.plan.items() if k not in ('channel','tag','title','prerelease')}
        plan.update(purpose='fixture',package='im.collie.app')
        path=self.root/'stable-fixture-plan.json';self.write_json(path,plan)
        args=types.SimpleNamespace(channel='stable',release_plan=path,
            approved_release_plan_sha256=artifacts.sha256(path),source_receipt=self.source_path,
            build_inputs=self.build_inputs_path,source_checkout=self.checkout,
            toolchain_input=self.toolchain_path,dependency_input=self.dependencies_path,
            native_input=self.native_path,config=self.config_path,test_reports=self.test_reports_path,
            directory=self.apks,preparation_baseline_commit=None,preparation=None,
            build_receipt=self.apks/'build-receipt.json',approved_build_receipt_sha256=None)
        context=runtime.plan(args)
        unsigned_sha='a'*64
        source,build=runtime.collect(args,context,unsigned_sha)
        self.assertFalse((self.apks/'preparation.json').exists())
        self.assertFalse(any(e['description']==runtime.PREPARATION_EVIDENCE for e in build['evidence']))
        self.write_json(args.build_receipt,build)
        args.approved_build_receipt_sha256=receipts.digest(build)
        manifest={'schema':2,'apks':[{'sha256':unsigned_sha}],
            'release_plan_sha256':context[1],'source_receipt_sha256':receipts.digest(source),
            'build_receipt_sha256':receipts.digest(build)}
        self.assertEqual(runtime.signing(args,context,manifest),(source,build))
        plan['purpose']='production';self.write_json(path,plan)
        args.approved_release_plan_sha256=artifacts.sha256(path)
        production=runtime.plan(args)
        with self.assertRaisesRegex(ValueError,'operator-pinned preparation baseline'):
            runtime.collect(args,production,unsigned_sha)
        for entry in build['evidence']:
            if entry['description']==runtime.PLAN_EVIDENCE:entry['sha256']=production[1]
        self.write_json(args.build_receipt,build)
        args.approved_build_receipt_sha256=receipts.digest(build)
        with self.assertRaisesRegex(ValueError,'requires the trusted preparation record'):
            runtime.signing(args,production,manifest)

    def test_approved_preparation_staging_copies_exact_bytes_and_refuses_overwrite(self):
        self.collect()
        build=receipts.load(self.apks/'build-receipt.json','build',profile=self.channel)
        build_sha=receipts.digest(build,profile=self.channel)
        bundle_root=self.root/'release-output';bundle_root.mkdir()
        cli=['stage_preparation','--channel',self.channel,'--release-plan',str(self.plan_path),
            '--approved-release-plan-sha256',self.plan_sha,'--source-receipt',str(self.source_path),
            '--build-receipt',str(self.apks/'build-receipt.json'),
            '--approved-build-receipt-sha256',build_sha,'--preparation',str(self.apks/'preparation.json'),
            '--release-id','123456789','--bundle-root',str(bundle_root)]
        output=io.StringIO();errors=io.StringIO()
        with patch('sys.argv',cli),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
            result=stage_preparation.main()
        self.assertEqual(result,0,errors.getvalue())
        staged=bundle_root/'bundles/123456789/preparation.json'
        digest=output.getvalue().splitlines()[-1]
        self.assertNotIn(str(bundle_root),output.getvalue())
        self.assertEqual(staged,bundle_root/'bundles/123456789/preparation.json')
        self.assertEqual(staged.read_bytes(),(self.apks/'preparation.json').read_bytes())
        self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(),digest)
        self.assertEqual(os.stat(staged).st_mode&0o777,0o600)
        bad=[*cli];bad[bad.index('--approved-build-receipt-sha256')+1]='0'*64
        bad[bad.index('--release-id')+1]='123456790'
        errors=io.StringIO()
        with patch('sys.argv',bad),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            self.assertEqual(stage_preparation.main(),1)
        self.assertIn('independently approved digest',errors.getvalue())
        errors=io.StringIO()
        with patch('sys.argv',cli),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            self.assertEqual(stage_preparation.main(),1)
        self.assertIn('new preparation bundle path required',errors.getvalue())

    def test_beta_signer_never_falls_back_to_stable_key_variables(self):
        self.collect()
        with patch.object(release_sign,'run') as key_tool,patch.object(release_sign.getpass,'getpass') as prompt:
            self.sign(beta_keys=False,expected=1)
            key_tool.assert_not_called();prompt.assert_not_called()

    def test_beta_collector_rejects_any_stable_alias_apk(self):
        (self.apks/'collie.apk').write_bytes(b'wrong channel alias')
        with patch.object(runtime,'github_run_metadata',side_effect=AssertionError('unexpected GitHub request')):
            self.collect(expected=1)

    def test_beta_manifest_and_publisher_reject_cross_channel_or_identity_substitution(self):
        self.collect()
        manifest=json.loads(self.manifest_path.read_text())
        context=(self.plan,self.plan_sha)
        wrong=dict(manifest,package='im.collie.app')
        with self.assertRaisesRegex(ValueError,'package or APK certificate'):
            artifacts.validate_manifest(wrong,self.commit,self.run_id,self.certificate,
                                        runtime.identities(context),self.channel)
        wrong=dict(manifest,apks=[dict(manifest['apks'][0],file='collie.apk')])
        with self.assertRaisesRegex(ValueError,'only trailblazer-beta.apk'):
            artifacts.validate_manifest(wrong,self.commit,self.run_id,self.certificate,
                                        runtime.identities(context),self.channel)

    def test_publisher_rejects_tampered_beta_identity_before_github_transport(self):
        self.collect()
        output,final_path,final,release_receipt=self.sign()
        tampered=dict(final,package='im.collie.app')
        self.write_json(final_path,tampered)
        with patch.object(runtime,'github_run_metadata',side_effect=AssertionError('unexpected GitHub request')):
            self.publish_dry_run(output,final_path,tampered,release_receipt,expected=1)
        self.write_json(final_path,final)
        with patch.object(runtime,'github_run_metadata',side_effect=AssertionError('unexpected GitHub request')):
            self.publish_dry_run(output,final_path,final,release_receipt,tag='trailblazer-v9.99.0-wrong',expected=1)
        with patch.object(runtime,'github_run_metadata',side_effect=AssertionError('unexpected GitHub request')):
            self.publish_dry_run(output,final_path,final,release_receipt,title='Wrong title',expected=1)
        with patch.object(runtime,'github_run_metadata',side_effect=AssertionError('unexpected GitHub request')):
            self.publish_dry_run(output,final_path,final,release_receipt,prerelease=False,expected=1)
        (output/'collie.apk').write_bytes(b'forbidden Stable alias')
        with patch.object(runtime,'github_run_metadata',return_value=json.dumps(self.run_metadata)):
            self.publish_dry_run(output,final_path,final,release_receipt,expected=1)


if __name__=='__main__':
    unittest.main()
