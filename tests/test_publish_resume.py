import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'tools/patches'))
import release_artifacts as a
import release_publish as m
from test_release_plan_support import legacy_args

class ResumeEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.commit='a'*40;self.calls=[];self.remote=None;self.remote_bytes=None;self.interrupt=None
        apks=[]
        for name in sorted(a.PROFILES):
            path=self.root/(name+'.apk');path.write_bytes(name.encode())
            apks.append({'profile':name,'file':path.name,'signed_sha256':a.sha256(path)})
        provenance=self.root/'provenance.json';provenance.write_text('legacy fixture')
        self.manifest=self.root/'final-manifest.json'
        self.manifest.write_text(json.dumps({'schema':1,'rehearsal':False,'commit':self.commit,'apks':apks,'unsigned_manifest_sha256':a.sha256(provenance),'instrumentation_checklist':{'commit':self.commit,'status':'not run','reason':'synthetic fixture','evidence':''}}))
        self.notes=self.root/'notes.md';self.notes.write_text('fixture')
    def fake(self,cmd,*unused,**kwargs):
        self.calls.append(cmd);action=cmd[2]
        inputs=next(self.root.glob('release-publish-*/inputs'))
        if action=='view':
            if self.remote is None:raise subprocess.CalledProcessError(1,cmd,stderr='release not found')
            return types.SimpleNamespace(stdout=json.dumps(self.remote))
        if action=='create':
            self.remote_bytes={p.name:p.read_bytes() for p in inputs.iterdir()}
            self.remote={'isDraft':True,'isPrerelease':False,'assets':[{'id':'fixture-'+n,'name':n,'size':len(data)} for n,data in self.remote_bytes.items()]}
            if self.interrupt=='create':self.interrupt=None;raise subprocess.CalledProcessError(1,cmd,stderr='lost create response')
        if action=='upload':
            for path in cmd[cmd.index('--repo')+2:]:
                path=Path(path);self.remote_bytes[path.name]=path.read_bytes()
            self.remote['assets']=[{'id':'fixture-'+n,'name':n,'size':len(data)} for n,data in self.remote_bytes.items()]
        if action=='download':
            dest=Path(cmd[cmd.index('--dir')+1])
            for name,data in self.remote_bytes.items():(dest/name).write_bytes(data)
        if action=='edit':
            if self.interrupt=='before-edit':self.interrupt=None;raise subprocess.CalledProcessError(1,cmd,stderr='interruption')
            self.remote['isDraft']=False
            if self.interrupt=='after-edit':self.interrupt=None;raise subprocess.CalledProcessError(1,cmd,stderr='lost publish response')
        return types.SimpleNamespace(stdout='')
    def invoke(self,action):
        args=['release_publish',action,'--repo','fixture/repo','--tag','vfixture','--approved-commit',self.commit,'--final-manifest',str(self.manifest),'--final-manifest-sha256',a.sha256(self.manifest),'--provenance',str(self.root/'provenance.json'),'--notes',str(self.notes),'--scratch',str(self.root)]
        with patch('sys.argv',legacy_args(args,self.root)),patch.object(m,'command',side_effect=self.fake),patch.object(m,'ensure_signed_tag'),patch.object(m,'verify_remote_tag'),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):return m.main()
    def test_create_response_loss_and_existing_draft_resume(self):
        self.interrupt='create';self.assertEqual(self.invoke('draft'),0)
        self.assertEqual(self.invoke('draft'),0)
        self.assertEqual(sum(c[2]=='create' for c in self.calls),1)
    def test_interrupt_before_and_after_publish_resumes_exact_bytes(self):
        for point in ('before-edit','after-edit'):
            with self.subTest(point=point):
                self.remote=None;self.calls=[];self.assertEqual(self.invoke('draft'),0)
                self.interrupt=point;self.assertEqual(self.invoke('publish'),1)
                self.assertEqual(self.invoke('publish'),0)
                edits=sum(c[2]=='edit' for c in self.calls)
                self.assertEqual(self.invoke('publish'),0)
                self.assertEqual(sum(c[2]=='edit' for c in self.calls),edits)
    def test_published_alias_substitution_and_draft_downgrade_refused(self):
        self.assertEqual(self.invoke('draft'),0);self.assertEqual(self.invoke('publish'),0)
        self.assertEqual(self.invoke('draft'),1)
        self.remote_bytes['collie.apk']=b'substituted'
        self.assertEqual(self.invoke('publish'),1)
    def test_partial_draft_upload_resumes_without_replacement(self):
        self.assertEqual(self.invoke('draft'),0)
        del self.remote_bytes['collie.apk'];self.remote['assets']=[{'id':'fixture-'+n,'name':n,'size':len(data)} for n,data in self.remote_bytes.items()]
        self.assertEqual(self.invoke('draft'),0)
        upload=[c for c in self.calls if c[2]=='upload']
        self.assertEqual(len(upload),1)
        self.assertTrue(str(upload[0][-1]).endswith('/collie.apk'))
        self.assertNotIn('--clobber',upload[0])

    def test_missing_lookup_is_not_permission_failure(self):
        self.assertTrue(m.missing_remote(subprocess.CalledProcessError(1,[],stderr='release not found')))
        self.assertFalse(m.missing_remote(subprocess.CalledProcessError(1,[],stderr='HTTP 403 forbidden')))
