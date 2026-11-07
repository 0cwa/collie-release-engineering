import hashlib,json,os,pathlib,subprocess,tempfile,unittest
import nested_git
from unittest.mock import patch

HOOK = b'fixture hook'
class NestedGitTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=pathlib.Path(self.temp.name)
  (self.root/'src/ringrtc').mkdir(parents=True); (self.root/'src/ringrtc/patch_java_release.py').write_bytes(HOOK)
  self.hook_patch=patch.object(nested_git,'HOOK_SHA256',hashlib.sha256(HOOK).hexdigest());self.hook_patch.start();self.addCleanup(self.hook_patch.stop)
  self.rows=[]
  for name in ('src/build','src/other'):
   d=self.root/name; d.mkdir(parents=True); self.git(d,'init','-q'); self.git(d,'config','user.email','fixture@example.invalid'); self.git(d,'config','user.name','Fixture')
   if name=='src/build':
    p=d/'android/gyp';p.mkdir(parents=True);(p/'compile_java.py').write_text("args = [\n '--release',\n '25',\n]\n");(p/'turbine.py').write_text("javac_cmd = ['--release', '25']\n")
   else: (d/'content').write_text('expected\n')
   self.git(d,'add','.');self.git(d,'commit','-qm','fixture');commit=self.git(d,'rev-parse','HEAD').strip();self.rows.append({'path':name,'commit':commit})
  for p in (self.root/'src/build/android/gyp').glob('*.py'):p.write_text(p.read_text().replace("'25'", "'21'"))
  self.manifest=self.root/'manifest.json';self.manifest.write_text(json.dumps({'repositories':self.rows}));self.digest=hashlib.sha256(self.manifest.read_bytes()).hexdigest()
 def git(self,d,*args):return subprocess.check_output(['git','-C',str(d),*args],stderr=subprocess.DEVNULL,text=True)
 def verify(self):return nested_git.verify(self.root,self.manifest,self.digest)
 def test_exact_upstream_hook_and_clean_checkout_pass(self):self.assertEqual(2,self.verify())
 def test_nested_tracked_change_fails(self):
  (self.root/'src/other/content').write_text('changed\n')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_assume_unchanged_index_flag_fails(self):
  d=self.root/'src/other';self.git(d,'update-index','--assume-unchanged','content');(d/'content').write_text('hidden change')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_tracked_byte_change_with_restored_timestamp_fails(self):
  p=self.root/'src/other/content';st=p.stat();p.write_text('altered!\n');os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_git_replace_objects_are_ignored(self):
  d=self.root/'src/other';old=self.git(d,'rev-parse','HEAD').strip();(d/'content').write_text('replaced\n');self.git(d,'add','.');self.git(d,'commit','-qm','replacement');new=self.git(d,'rev-parse','HEAD').strip();self.git(d,'checkout','-q','--detach',old);self.git(d,'replace',old,new)
  self.assertEqual(b'expected\n',nested_git.git(d,'show','HEAD:content'));self.assertEqual(2,self.verify())
 def test_configured_fsmonitor_is_not_executed(self):
  d=self.root/'src/other'; marker=self.root/'monitor-ran'; hook=self.root/'fake-monitor';hook.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 0\n');hook.chmod(0o755);self.git(d,'config','core.fsmonitor',str(hook))
  self.assertEqual(2,self.verify());self.assertFalse(marker.exists())
 def test_extra_change_to_hook_output_fails(self):
  p=self.root/'src/build/android/gyp/turbine.py';p.write_text(p.read_text()+'# extra\n')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_dependency_head_change_fails(self):
  d=self.root/'src/other';(d/'content').write_text('new\n');self.git(d,'add','.');self.git(d,'commit','-qm','new')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_pinned_crlf_attributes_allow_only_exact_normalization(self):
  d=self.root/'src/other';(d/'.gitattributes').write_text('*.bat eol=crlf\n');(d/'file.bat').write_bytes(b'one\ntwo\n');self.git(d,'add','.');self.git(d,'commit','-qm','crlf fixture');self.rows[1]['commit']=self.git(d,'rev-parse','HEAD').strip();self.manifest.write_text(json.dumps({'repositories':self.rows}));self.digest=hashlib.sha256(self.manifest.read_bytes()).hexdigest();(d/'file.bat').write_bytes(b'one\r\ntwo\r\n')
  self.assertEqual(2,self.verify());(d/'file.bat').write_bytes(b'one\r\ntwo\n')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_ambient_git_attr_source_is_ignored(self):
  with patch.dict(os.environ, {'GIT_ATTR_SOURCE':'missing-fixture-tree'}):
   self.assertEqual(2,self.verify())
 def test_ambient_git_object_and_index_overrides_are_ignored(self):
  with patch.dict(os.environ, {'GIT_OBJECT_DIRECTORY':'/nonexistent-fixture','GIT_INDEX_FILE':'/nonexistent-fixture','GIT_DIR':'/nonexistent-fixture'}):
   self.assertEqual(2,self.verify())
 def test_git_info_attributes_path_is_absolute(self):
  d=self.root/'src/other';p=pathlib.Path(os.fsdecode(nested_git.git(d,'rev-parse','--path-format=absolute','--git-path','info/attributes').strip()));self.assertTrue(p.is_absolute());self.assertEqual(d/'.git/info/attributes',p)
 def test_untracked_git_info_attributes_rejected(self):
  d=self.root/'src/other';(d/'.git/info/attributes').write_text('* text eol=crlf\n')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_unpopulated_gitlink_only(self):
  d=self.root/'src/other';self.git(d,'update-index','--add','--cacheinfo','160000,'+'1'*40+',vendor');self.git(d,'commit','-qm','gitlink');self.rows[1]['commit']=self.git(d,'rev-parse','HEAD').strip();self.manifest.write_text(json.dumps({'repositories':self.rows}));self.digest=hashlib.sha256(self.manifest.read_bytes()).hexdigest();(d/'vendor').mkdir(exist_ok=True)
  self.assertEqual(2,self.verify());(d/'vendor/unverified').write_text('not allowed')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_manifest_tampering_fails(self):
  self.manifest.write_text('{}')
  with self.assertRaises(nested_git.Failure):self.verify()
 def test_hook_tampering_fails(self):
  (self.root/'src/ringrtc/patch_java_release.py').write_text('modified')
  with self.assertRaises(nested_git.Failure):self.verify()
if __name__=='__main__':unittest.main()
