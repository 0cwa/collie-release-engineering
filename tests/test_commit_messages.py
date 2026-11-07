import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'source'))
import commit_messages as messages
import publish
import test_publish as fixtures

class CommitMessageInputs(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PublicationTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;f.path.write_text(json.dumps(f.plan));self.plan_sha=hashlib.sha256(f.path.read_bytes()).hexdigest()
        self.wording=messages.preview(f.plan,self.plan_sha);self.file=f.repo/'messages.json'
    def emit(self):
        self.file.write_text(json.dumps(self.wording))
        return publish.publish(self.fixture.repo,self.fixture.path,self.plan_sha,self.fixture.old,messages=self.file,approved_messages_sha256=hashlib.sha256(self.file.read_bytes()).hexdigest())
    def test_exact_visible_messages_determine_commits_without_changing_tree(self):
        self.wording['layers'][0]['subject']='Keep the reviewed fixture content'
        one=self.emit();two=self.emit();self.assertEqual(one,two)
        raw=publish.git(self.fixture.repo,'cat-file','commit',one['source_commit']).split(b'\n\n',1)[1]
        expected=messages.message({k:self.wording['layers'][0][k] for k in ('subject','body')}).encode()
        self.assertEqual(raw,expected)
        self.wording['layers'][0]['subject']='Preserve the same reviewed fixture content'
        changed=self.emit();self.assertNotEqual(one['source_commit'],changed['source_commit']);self.assertEqual(one['source_tree'],changed['source_tree'])
    def test_missing_or_substituted_wording_cannot_use_historical_defaults(self):
        with self.assertRaisesRegex(ValueError,'reviewed message input'):
            publish.publish(self.fixture.repo,self.fixture.path,self.plan_sha,self.fixture.old)
        self.file.write_text(json.dumps(self.wording))
        with self.assertRaisesRegex(ValueError,'external approval'):
            publish.publish(self.fixture.repo,self.fixture.path,self.plan_sha,self.fixture.old,messages=self.file,approved_messages_sha256='0'*64)
        self.wording['publication_plan_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'exact publication plan'):self.emit()
    def test_assistance_and_provenance_are_visible_without_rewriting_original_data(self):
        original=copy.deepcopy(self.fixture.plan)
        original['layers'][0]['name']='Keep source meaning\n\nAI-Generated: true\n\nBased-On-Source-Commit: '+'a'*40
        data=messages.preview(original,'1'*64);messages.validate(data,original,'1'*64)
        self.assertIn('AI-Generated: true',original['layers'][0]['name'])
        self.assertNotIn('AI-Generated:',data['layers'][0]['body']);self.assertIn('LLM-Assisted: true',data['layers'][0]['body'])
        data['layers'][0]['body']='Removed provenance\nLLM-Assisted: true'
        with self.assertRaisesRegex(ValueError,'provenance'):messages.validate(data,original,'1'*64)


class CommitPreviewWriting(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.json=self.root/'messages.json';self.markdown=self.root/'messages.md'
        self.data={'schema':1,'publication_plan_sha256':'1'*64,'layers':[],
                   'integration':{'subject':'Integrate the reviewed source','body':'LLM-Assisted: true'},
                   'merge':{'subject':'Publish the reviewed source','body':'LLM-Assisted: true'},'pr_title':'Publish Collie source'}
    def write(self):messages.write_preview(self.json,self.markdown,self.data)
    def test_new_previews_preserve_exact_json_and_rendered_markdown(self):
        self.write();self.assertEqual(json.loads(self.json.read_text()),self.data)
        self.assertEqual(self.markdown.read_text(),messages.markdown(self.data))
    def test_existing_files_and_dangling_links_are_never_overwritten(self):
        for destination in (self.json,self.markdown):
            for kind in ('file','dangling-link'):
                with self.subTest(destination=destination.name,kind=kind):
                    if kind=='file':destination.write_bytes(b'owner draft')
                    else:destination.symlink_to('missing-owner-target')
                    try:
                        with self.assertRaises(FileExistsError) as caught:self.write()
                        if kind=='file':self.assertEqual(destination.read_bytes(),b'owner draft')
                        else:self.assertTrue(destination.is_symlink());self.assertFalse((self.root/'missing-owner-target').exists())
                        other=self.markdown if destination==self.json else self.json
                        if destination==self.markdown:
                            self.assertEqual(other.read_bytes(),b'')
                            self.assertIn(str(other),' '.join(caught.exception.__notes__))
                        else:self.assertFalse(other.exists())
                    finally:
                        for path in (self.json,self.markdown):
                            if path.exists() or path.is_symlink():path.unlink()
    def test_raced_creation_of_either_output_preserves_owner_bytes(self):
        from unittest.mock import patch
        original=Path.open
        for destination in (self.json,self.markdown):
            with self.subTest(destination=destination.name):
                def opening(path,mode='r',*args,**kwargs):
                    if path==destination and mode=='x':
                        with original(path,'w',encoding='utf-8') as owner:owner.write('raced owner draft')
                    return original(path,mode,*args,**kwargs)
                try:
                    with patch.object(Path,'open',opening):
                        with self.assertRaises(FileExistsError) as caught:self.write()
                    self.assertEqual(destination.read_text(),'raced owner draft')
                    other=self.markdown if destination==self.json else self.json
                    if destination==self.markdown:
                        self.assertEqual(other.read_bytes(),b'')
                        self.assertIn(str(other),' '.join(caught.exception.__notes__))
                    else:self.assertFalse(other.exists())
                finally:
                    for path in (self.json,self.markdown):
                        if path.exists() or path.is_symlink():path.unlink()
    def test_creation_error_preserves_a_replacement_of_first_reservation(self):
        from unittest.mock import patch
        original=Path.open;self.markdown.write_text('existing owner Markdown')
        def opening(path,mode='r',*args,**kwargs):
            if path==self.markdown and mode=='x':
                self.json.unlink();self.json.write_text('replacement owner JSON')
            return original(path,mode,*args,**kwargs)
        with patch.object(Path,'open',opening):
            with self.assertRaises(FileExistsError):self.write()
        self.assertEqual(self.json.read_text(),'replacement owner JSON');self.assertEqual(self.markdown.read_text(),'existing owner Markdown')
    def test_creation_error_preserves_owner_edits_to_first_reservation(self):
        from unittest.mock import patch
        original=Path.open;self.markdown.write_text('existing owner Markdown')
        def opening(path,mode='r',*args,**kwargs):
            if path==self.markdown and mode=='x':self.json.write_text('edited owner JSON')
            return original(path,mode,*args,**kwargs)
        with patch.object(Path,'open',opening):
            with self.assertRaises(FileExistsError):self.write()
        self.assertEqual(self.json.read_text(),'edited owner JSON');self.assertEqual(self.markdown.read_text(),'existing owner Markdown')
    def test_creation_error_preserves_bytes_written_before_first_open_returns(self):
        from unittest.mock import patch
        original=Path.open;self.markdown.write_text('existing owner Markdown')
        def opening(path,mode='r',*args,**kwargs):
            stream=original(path,mode,*args,**kwargs)
            if path==self.json and mode=='x':stream.write('owner bytes before open returns');stream.flush()
            return stream
        with patch.object(Path,'open',opening):
            with self.assertRaises(FileExistsError) as caught:self.write()
        self.assertEqual(self.json.read_text(),'owner bytes before open returns')
        self.assertIn(str(self.json),' '.join(caught.exception.__notes__))
    def test_creation_error_has_no_stat_or_unlink_rollback_window(self):
        from unittest.mock import patch
        self.markdown.write_text('existing owner Markdown')
        with patch.object(Path,'lstat',side_effect=AssertionError('must not inspect exposed names for rollback')), \
             patch.object(Path,'unlink',side_effect=AssertionError('must not remove exposed names')):
            with self.assertRaises(FileExistsError) as caught:self.write()
        self.assertEqual(self.json.read_bytes(),b'');self.assertEqual(self.markdown.read_text(),'existing owner Markdown')
        self.assertIn(str(self.json),' '.join(caught.exception.__notes__))
