import hashlib,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import download_pages_input as d

class InputDownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/'approved.tar';self.body=b'data-only-input'
        self.expected=hashlib.sha256(self.body).hexdigest()
    class Response(io.BytesIO):
        def __init__(self,body,final):super().__init__(body);self.final=final
        def geturl(self):return self.final
    def responses(self,url=None):
        metadata={'id':1,'name':'trailblazer-beta-release-input.tar','size':len(self.body),
                  'browser_download_url':url or 'https://github.com/0cwa/collie-release-engineering/releases/download/fixture/input.tar'}
        return (self.Response(json.dumps(metadata).encode(),
                    'https://api.github.com/repos/0cwa/collie-release-engineering/releases/assets/1'),
                self.Response(self.body,'https://release-assets.githubusercontent.com/asset'))
    def transport(self,url=None,metadata_hook=None):
        metadata,binary=self.responses(url)
        def read_metadata(*_args,**_kwargs):
            if metadata_hook:metadata_hook()
            return metadata
        opener=unittest.mock.Mock();opener.open.return_value=binary
        return patch('urllib.request.urlopen',side_effect=read_metadata),patch('urllib.request.build_opener',return_value=opener),opener
    def test_explicit_asset_hash_and_token_is_stripped_on_redirect(self):
        first,second,opener=self.transport()
        with patch.dict(os.environ,{'GITHUB_TOKEN':'synthetic-test-token'}),first,second as make_opener:
            d.download('1',self.expected,self.output)
            self.assertEqual(self.output.read_bytes(),self.body)
            request=make_opener.return_value.open.call_args.args[0]
            self.assertIn('/releases/assets/1',request.full_url)
            self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-test-token')
            self.assertEqual(request.get_header('Accept'),'application/octet-stream')
            handler=d.FixedAssetRedirect()
            redirected=handler.redirect_request(request,None,302,'Found',{},'https://release-assets.githubusercontent.com/asset')
            self.assertIsNone(redirected.get_header('Authorization'))
            same_host=handler.redirect_request(request,None,302,'Found',{},request.full_url+'?download=1')
            self.assertEqual(same_host.get_header('Authorization'),'Bearer synthetic-test-token')
    def test_wrong_digest_removes_partial_and_wrong_repository_stops(self):
        first,second,_=self.transport()
        with first,second:
            with self.assertRaisesRegex(ValueError,'approved input'):d.download('1','0'*64,self.output)
        self.assertFalse(self.output.exists());self.assertFalse(self.output.with_suffix('.tar.partial').exists())
        first,second,_=self.transport('https://example.invalid/input.tar')
        with first,second:
            with self.assertRaisesRegex(ValueError,'repository'):d.download('1',self.expected,self.output)
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
            def raced():
                if not path.exists():path.write_bytes(b'other job data')
            first,second,_=self.transport(metadata_hook=raced)
            with first,second:
                with self.assertRaises(FileExistsError):d.download('1',self.expected,self.output)
            self.assertEqual(path.read_bytes(),b'other job data');path.unlink()
    def test_redirect_rejects_unapproved_hosts_and_paths(self):
        handler=d.FixedAssetRedirect()
        request=d.urllib.request.Request('https://api.github.com/repos/0cwa/collie-release-engineering/releases/assets/1')
        for url in ('http://release-assets.githubusercontent.com/x','https://example.invalid/x',
                    'https://github.com/other/repo/releases/download/x/y'):
            with self.subTest(url=url),self.assertRaises(ValueError):
                handler.redirect_request(request,None,302,'Found',{},url)
