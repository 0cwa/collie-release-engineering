import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import publication_plan

class PublicationPlanTests(unittest.TestCase):
    def test_plan_uses_verified_stage_receipt_and_exact_files_without_copy(self):
        with tempfile.TemporaryDirectory() as root:
            stage = Path(root)
            relative = Path('maven/im/collie/native/example/1/example-1.aar')
            (stage / relative).parent.mkdir(parents=True)
            (stage / relative).write_bytes(b'fixture')
            (stage / 'receipt.json').write_text('fixture receipt')
            lock = {'output_group':'im.collie.native','modules':[{'name':'example','output_version':'1'}]}
            receipt = {'artifacts':{'example':{'example-1.aar':'a'*64}},'verification_sha256':'b'*64}
            with patch.object(publication_plan.stage_artifacts,'load_lock',return_value=lock), patch.object(publication_plan.stage_artifacts,'verify_stage',return_value=receipt) as verify:
                output = publication_plan.plan(stage/'receipt.json', stage, stage/'original.xml','https://repo.collie.im/native-inputs/v1/maven/')
                verify.assert_called_once()
                self.assertEqual(output['files'][0]['bytes'],7)
                self.assertEqual(output['files'][0]['sha256'],'a'*64)
                self.assertEqual(output['operator_approval'],'REQUIRED')
                self.assertEqual(len(list(stage.rglob('*.aar'))),1)

    def test_credentialled_or_non_https_url_rejected_before_stage_access(self):
        for url in ['http://host/','file:///private/','https://name:secret@host/','https://host/?key=value','https://host/repo']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                publication_plan.plan(Path('absent'),Path('absent'),Path('absent'),url)

    def test_tampered_stage_fails_before_plan(self):
        with patch.object(publication_plan.stage_artifacts,'load_lock',return_value={}), patch.object(publication_plan.stage_artifacts,'verify_stage',side_effect=ValueError('tampered')):
            with self.assertRaisesRegex(ValueError,'tampered'):
                publication_plan.plan(Path('absent'),Path('absent'),Path('absent'),'https://repo.collie.im/native/')
