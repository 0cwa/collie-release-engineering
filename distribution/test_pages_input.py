import hashlib,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import download_pages_input as d

class InputDownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.output=Path(self.tmp.name)/'approved.tar';self.body=b'data-only-input';self.expected=hashlib.sha256(self.body).hexdigest()
    def responses(self,url=None):
        metadata={'id':1,'size':len(self.body),'browser_download_url':url or 'https://github.com/0cwa/collie-release-engineering/releases/download/fixture/input.tar'}
        return [io.BytesIO(json.dumps(metadata).encode()),io.BytesIO(self.body)]
    def test_explicit_asset_hash_and_no_token_on_download_redirect(self):
        with patch.dict(os.environ,{'GITHUB_TOKEN':'synthetic-test-token'}),patch('urllib.request.urlopen',side_effect=self.responses()) as fetch:
            d.download('1',self.expected,self.output)
            self.assertEqual(self.output.read_bytes(),self.body)
            self.assertIsInstance(fetch.call_args_list[1].args[0],str)
    def test_wrong_digest_removes_partial_and_wrong_repository_stops(self):
        with patch('urllib.request.urlopen',side_effect=self.responses()):
            with self.assertRaisesRegex(ValueError,'approved input'):d.download('1','0'*64,self.output)
        self.assertFalse(self.output.exists());self.assertFalse(self.output.with_suffix('.tar.partial').exists())
        with patch('urllib.request.urlopen',side_effect=self.responses('https://example.invalid/input.tar')):
            with self.assertRaisesRegex(ValueError,'repository differs'):d.download('1',self.expected,self.output)
    def test_asset_id_rejects_latest_and_injected_shell_text(self):
        with patch('urllib.request.urlopen') as fetch:
            for value in ('latest','1;echo-token','-1','1\n2'):
                with self.assertRaises(ValueError):d.download(value,self.expected,self.output)
            fetch.assert_not_called()

    def test_preexisting_partial_preserved_without_network(self):
        temporary=self.output.with_suffix('.tar.partial');temporary.write_bytes(b'preexisting data')
        with patch('urllib.request.urlopen') as fetch:
            with self.assertRaisesRegex(ValueError,'fresh input'):d.download('1',self.expected,self.output)
            fetch.assert_not_called()
        self.assertEqual(temporary.read_bytes(),b'preexisting data')
    def test_dangling_output_and_partial_links_preserved_without_network(self):
        for path in (self.output,self.output.with_suffix('.tar.partial')):
            path.symlink_to(self.output.parent/'absent-target')
            with patch('urllib.request.urlopen') as fetch:
                with self.assertRaisesRegex(ValueError,'fresh input'):d.download('1',self.expected,self.output)
                fetch.assert_not_called()
            self.assertTrue(path.is_symlink());path.unlink()
    def test_raced_partial_and_destination_are_never_deleted_or_overwritten(self):
        for path in (self.output.with_suffix('.tar.partial'),self.output):
            responses=iter(self.responses())
            def raced(*args,**kwargs):
                if not path.exists():path.write_bytes(b'other job data')
                return next(responses)
            with patch('urllib.request.urlopen',side_effect=raced):
                with self.assertRaises(FileExistsError):d.download('1',self.expected,self.output)
            self.assertEqual(path.read_bytes(),b'other job data');path.unlink()
