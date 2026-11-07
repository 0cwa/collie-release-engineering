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
